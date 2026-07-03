"""Real-Time Data Socket client — the primary trade-detection path.

Subscribes to Polymarket's public activity stream
(wss://ws-live-data.polymarket.com, topic "activity", type "trades"), which
pushes every platform trade with the trader's proxy wallet, price, size, side,
and market — no auth required. This replaces data-API polling and cuts
detection latency from 5-30+ seconds to sub-second, which research shows is
the difference between copying an edge and copying a price that already moved.

Keepalive: the server expects a literal "ping" text frame every 5 seconds.
Silent drops are detected via a data timeout and reconnected with backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

import websockets

log = logging.getLogger("stream")

TradeCallback = Callable[[dict, float], Awaitable[None]]  # (payload, detected_ts)


class ActivityStream:
    def __init__(self, url: str, silence_timeout: float = 300.0):
        self.url = url
        self.silence_timeout = silence_timeout
        self._callbacks: list[TradeCallback] = []
        self._closed = False
        self.last_message_ts = 0.0
        self.messages_seen = 0

    def on_trade(self, cb: TradeCallback) -> None:
        self._callbacks.append(cb)

    async def run(self) -> None:
        backoff = 1.0
        while not self._closed:
            try:
                async with websockets.connect(self.url, ping_interval=None,
                                              max_queue=4096) as ws:
                    await ws.send(json.dumps({
                        "action": "subscribe",
                        "subscriptions": [{"topic": "activity", "type": "trades"}],
                    }))
                    log.info("Activity stream connected (%s)", self.url)
                    backoff = 1.0
                    self.last_message_ts = time.time()
                    ping_task = asyncio.create_task(self._pinger(ws))
                    try:
                        await self._read_loop(ws)
                    finally:
                        ping_task.cancel()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.error("Activity stream dropped (%s); reconnecting in %.1fs",
                          exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _pinger(self, ws) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await ws.send("ping")
            except Exception:  # noqa: BLE001
                return

    async def _read_loop(self, ws) -> None:
        while not self._closed:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                if time.time() - self.last_message_ts > self.silence_timeout:
                    raise ConnectionError("stream silent past timeout")
                continue
            detected = time.time()
            self.last_message_ts = detected
            if raw in ("pong", "ping"):
                continue
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            for item in msg if isinstance(msg, list) else [msg]:
                if not isinstance(item, dict):
                    continue
                payload = item.get("payload")
                if item.get("topic") == "activity" and payload:
                    self.messages_seen += 1
                    for cb in self._callbacks:
                        try:
                            await cb(payload, detected)
                        except Exception:  # noqa: BLE001
                            log.exception("trade callback error")

    def close(self) -> None:
        self._closed = True
