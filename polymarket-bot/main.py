"""Polymarket Multi-Layer Copy Trading Research Bot v3 — entry point.

Wires up: config/secrets, logging, SQLite, the single connection manager,
Layer One (signal detection), Layer Two (adaptive execution), Layer Three
(news-aware execution), and the risk manager. Run the dashboard separately:

    python main.py          # bot (dry-run by default)
    python dashboard.py     # read-only dashboard on localhost:5000
"""

from __future__ import annotations

import asyncio
import logging
import signal as os_signal
import sys

from bot.config import ConfigError, load_config
from bot.connection import ConnectionManager
from bot.database import Database
from bot.execution import LayerTwo
from bot.layer_one import LayerOne, Signal
from bot.layer_three import LayerThree
from bot.logging_setup import setup_logging
from bot.news import NewsFilter
from bot.risk import RiskManager


async def fan_out(source: asyncio.Queue[Signal],
                  sinks: list[asyncio.Queue[Signal]]) -> None:
    """Duplicate every Layer One signal to Layers Two and Three."""
    while True:
        sig = await source.get()
        for q in sinks:
            await q.put(sig)


async def amain() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"SETUP ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)

    log = setup_logging(config.base_dir / config["runtime"]["log_file"])
    log.info("Starting Polymarket research bot v3 — mode: %s",
             "DRY RUN" if config.dry_run else "LIVE TRADING")
    if not config.dry_run:
        log.warning("LIVE TRADING ENABLED: real orders will be placed. "
                    "Max exposure ~$%.0f", config["risk"]["required_allowance_usdc"])

    db = Database(config.db_path)
    conn = ConnectionManager(config)
    await conn.start()

    layer_one = LayerOne(config, conn, db)
    l2_queue: asyncio.Queue[Signal] = asyncio.Queue()
    l3_queue: asyncio.Queue[Signal] = asyncio.Queue()
    layer_two = LayerTwo(config, conn, db, l2_queue)
    layer_three = LayerThree(config, conn, db, l3_queue)
    layer_three.attach_news_filter(NewsFilter(config, conn, db))
    risk = RiskManager(config, db, [layer_two, layer_three])

    tasks = [
        asyncio.create_task(layer_one.run(), name="layer-one"),
        asyncio.create_task(fan_out(layer_one.signal_queue, [l2_queue, l3_queue]),
                            name="signal-fan-out"),
        asyncio.create_task(layer_two.run(), name="layer-two"),
        asyncio.create_task(layer_three.run(), name="layer-three"),
        asyncio.create_task(risk.run(), name="risk-manager"),
    ]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(os_signal, sig_name), stop.set)
        except (NotImplementedError, AttributeError):
            pass

    await stop.wait()
    log.info("Shutdown requested; cancelling tasks")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await conn.close()
    db.close()
    log.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
