"""Shared execution engine for Layers Two and Three.

Layer Two consumes Layer One signals directly; Layer Three subclasses this
and adds the news catalyst filter. Both run one hundred $1 micro-bots that
map to signal sources and are reweighted toward the conviction bins that are
actually winning. A parallel $5000 paper-trading simulation records what the
same signals would do at size.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from .config import Config
from .connection import ConnectionManager
from .database import Database
from .errors import BotError, ErrorKind, classify_clob_error
from .layer_one import Signal

log = logging.getLogger("layer2")


@dataclass
class ExecBot:
    bot_id: str
    layer: int
    category: str
    conviction_bin: int
    source_wallet: str | None = None
    paused: bool = False        # e.g. insufficient balance
    halted: bool = False        # circuit breaker


class ExecutionLayer:
    LAYER = 2
    NAME = "Layer Two"

    def __init__(self, config: Config, conn: ConnectionManager, db: Database,
                 signal_queue: asyncio.Queue[Signal]):
        self.config = config
        self.conn = conn
        self.db = db
        self.signal_queue = signal_queue
        self.layer_cfg = config["layers"][f"layer_{'two' if self.LAYER == 2 else 'three'}"]
        self.bots: list[ExecBot] = []
        self._build_bots()

    def _build_bots(self) -> None:
        count = int(self.layer_cfg["bot_count"])
        cats = self.config.categories
        bins = self.config.conviction_thresholds
        now = time.time()
        rows = []
        prefix = f"L{self.LAYER}"
        for i in range(count):
            category = cats[i % len(cats)]
            conviction = bins[(i // len(cats)) % len(bins)]
            bot_id = f"{prefix}-{category[:4].lower()}-{conviction}-{i}"
            self.bots.append(ExecBot(bot_id, self.LAYER, category, conviction))
            rows.append((bot_id, self.LAYER, category, conviction,
                         float(self.layer_cfg["scale_factor"]),
                         1 if self.LAYER == 3 else 0, None,
                         float(self.layer_cfg["per_bot_allocation_usdc"]), now))
        self.db.executemany(
            "INSERT INTO bot_metadata (bot_id, layer, category, conviction_bin, "
            "scale_factor, news_filter, source_wallet, allocation_usdc, updated_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(bot_id) DO NOTHING", rows)
        log.info("%s initialized with %d execution bots", self.NAME, len(self.bots))

    # ── Signal filtering hooks (Layer Three overrides) ───────────────────────

    async def effective_confidence(self, signal: Signal) -> tuple[float, bool]:
        """Return (effective confidence, allowed). Layer Two passes through."""
        return signal.confidence, True

    # ── Bot selection & adaptive weighting ───────────────────────────────────

    def _bin_win_rates(self) -> dict[int, float]:
        """Rolling 30-day win rate of resolved trades per conviction bin —
        the live leaderboard of which Layer One source classes are profitable."""
        rows = self.db.query(
            "SELECT b.conviction_bin AS bin, "
            "AVG(CASE WHEN e.current_pnl > 0 THEN 1.0 ELSE 0.0 END) AS wr, "
            "COUNT(*) AS n FROM layer_two_executions e "
            "JOIN bot_metadata b ON b.bot_id = e.bot_id "
            "WHERE e.layer = ? AND e.status = 'resolved' AND e.is_paper = 0 "
            "AND e.ts > ? GROUP BY b.conviction_bin",
            (self.LAYER, time.time() - 30 * 86400))
        return {int(r["bin"]): float(r["wr"]) for r in rows if r["n"] >= 5}

    def _pick_bot(self, signal: Signal) -> ExecBot | None:
        """Choose an eligible bot for this signal, preferring bots in the
        conviction bins with the best rolling win rate (dynamic reweighting)."""
        wrs = self._bin_win_rates()
        candidates = [
            b for b in self.bots
            if not b.halted and not b.paused and b.category == signal.category
            and not self.db.has_position_in_market(b.bot_id, signal.condition_id)
            and self.db.open_position_count(b.bot_id)
                < int(self.config["risk"]["max_concurrent_positions_per_bot"])
        ]
        if not candidates:
            return None
        # Sort by that bin's realized win rate (unknown bins get neutral 0.5),
        # so capital flows toward e.g. 50-trade bins hitting 65% over 10-trade
        # bins at 45%.
        candidates.sort(key=lambda b: wrs.get(b.conviction_bin, 0.5), reverse=True)
        return candidates[0]

    # ── Execution ────────────────────────────────────────────────────────────

    async def handle_signal(self, signal: Signal) -> None:
        threshold = float(self.layer_cfg["confidence_threshold"])
        latency_budget = float(self.layer_cfg["latency_budget_seconds"])

        age = time.time() - signal.ts
        if age > latency_budget:
            log.warning("%s skip %s: signal age %.2fs exceeds %.1fs latency budget",
                        self.NAME, signal.signal_id[:8], age, latency_budget)
            return

        effective, allowed = await self.effective_confidence(signal)
        if not allowed or effective <= threshold:
            log.info("%s skip %s: effective confidence %.2f <= %.2f",
                     self.NAME, signal.signal_id[:8], effective, threshold)
            return

        bot = self._pick_bot(signal)
        if bot is None:
            log.warning("%s skip %s: no eligible bot (limits or halts)",
                        self.NAME, signal.signal_id[:8])
            return

        scale = float(self.layer_cfg["scale_factor"])
        size = round(min(signal.size_usdc * scale,
                         float(self.layer_cfg["per_bot_allocation_usdc"])), 2)
        if size < 0.10:
            return
        exposure_cap = float(self.layer_cfg["max_total_exposure_usdc"])
        if self.db.layer_exposure(self.LAYER) + size > exposure_cap:
            log.warning("%s skip %s: layer exposure cap %.0f reached",
                        self.NAME, signal.signal_id[:8], exposure_cap)
            return

        mid = self.conn.mid_price(signal.token_id) or signal.price
        exec_id = str(uuid.uuid4())
        try:
            result = await self.conn.place_maker_order(signal.token_id, mid, size)
            status = result["status"]
            order_id = result["order_id"]
        except Exception as exc:  # noqa: BLE001
            kind = exc.kind if isinstance(exc, BotError) else classify_clob_error(exc)
            if kind == ErrorKind.INSUFFICIENT_BALANCE:
                bot.paused = True
                log.error("%s PAUSED %s: insufficient balance", self.NAME, bot.bot_id)
            elif kind == ErrorKind.MARKET_CLOSED:
                log.warning("%s: market %s closed/resolved mid-order, no retry",
                            self.NAME, signal.condition_id[:12])
            elif kind == ErrorKind.INVALID_TOKEN:
                log.warning("%s: invalid token id %s, trade skipped",
                            self.NAME, signal.token_id[:12])
            else:
                log.error("%s order error (%s): %s", self.NAME, kind.value, exc)
            status, order_id = "failed", None

        self.db.execute(
            "INSERT INTO layer_two_executions (execution_id, ts, signal_id, layer, "
            "bot_id, condition_id, outcome, exec_price, size_usdc, filled_ts, "
            "status, is_paper, order_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?)",
            (exec_id, time.time(), signal.signal_id, self.LAYER, bot.bot_id,
             signal.condition_id, signal.outcome, mid, size,
             time.time() if status == "dry_run" else None, status, order_id))
        if status != "failed":
            bot.source_wallet = signal.source_wallet
            self.db.execute("UPDATE bot_metadata SET source_wallet=?, updated_ts=? "
                            "WHERE bot_id=?",
                            (signal.source_wallet, time.time(), bot.bot_id))
            log.info("%s EXEC %s: bot %s copied %.2f USDC @ %.3f (scale %.0f%%, %s)",
                     self.NAME, exec_id[:8], bot.bot_id, size, mid, scale * 100, status)
        self._record_paper_trade(signal, mid)

    def _record_paper_trade(self, signal: Signal, mid: float) -> None:
        """Parallel $5000 paper simulation: same signal, bigger scale factor —
        tests whether the $1 edge survives at size."""
        paper_cfg = self.config["paper_trading"]
        if not paper_cfg.get("enabled") or self.LAYER != 2:
            return
        size = round(signal.size_usdc * float(paper_cfg["scale_factor"]), 2)
        exposure = self.db.query_one(
            "SELECT COALESCE(SUM(size_usdc),0) AS t FROM layer_two_executions "
            "WHERE is_paper = 1 AND status IN ('open','dry_run')")
        if float(exposure["t"]) + size > float(paper_cfg["allocation_usdc"]):
            return
        self.db.execute(
            "INSERT INTO layer_two_executions (execution_id, ts, signal_id, layer, "
            "bot_id, condition_id, outcome, exec_price, size_usdc, status, is_paper) "
            "VALUES (?,?,?,?,?,?,?,?,?,'dry_run',1)",
            (str(uuid.uuid4()), time.time(), signal.signal_id, self.LAYER,
             f"PAPER-L{self.LAYER}", signal.condition_id, signal.outcome, mid, size))

    # ── Mark-to-market / resolution sweep ────────────────────────────────────

    async def mark_positions(self) -> None:
        """Refresh current_pnl from live mids; settle resolved markets and roll
        results into bot stats. Partial fills tracked via filled_size."""
        rows = self.db.query(
            "SELECT * FROM layer_two_executions WHERE layer = ? "
            "AND status IN ('open','partial','filled','dry_run')", (self.LAYER,))
        for r in rows:
            market = await self.conn.fetch_market(r["condition_id"])
            if market and (market.get("closed") or market.get("resolved")):
                winning = str(market.get("winningOutcome")
                              or market.get("outcome") or "").upper()
                won = winning == str(r["outcome"]).upper()
                pnl = round(r["size_usdc"] * ((1 / max(r["exec_price"], 0.01)) - 1), 4) \
                    if won else -r["size_usdc"]
                self.db.execute(
                    "UPDATE layer_two_executions SET status='resolved', "
                    "current_pnl=? WHERE execution_id=?", (pnl, r["execution_id"]))
                if not r["is_paper"]:
                    self.db.record_bot_result(r["bot_id"], pnl, won)
                log.info("%s RESOLVED %s: %s pnl %+.2f", self.NAME,
                         r["execution_id"][:8], "WIN" if won else "LOSS", pnl)

    async def run(self) -> None:
        asyncio.create_task(self._mark_loop(), name=f"{self.NAME}-marker")
        while True:
            signal = await self.signal_queue.get()
            for bot in self.bots[:5]:   # heartbeat sample; full sweep in _mark_loop
                self.db.heartbeat(bot.bot_id, self.LAYER)
            try:
                await self.handle_signal(signal)
            except Exception:  # noqa: BLE001
                log.exception("%s signal handling error", self.NAME)

    async def _mark_loop(self) -> None:
        hb = float(self.config["runtime"]["heartbeat_seconds"])
        while True:
            for bot in self.bots:
                self.db.heartbeat(bot.bot_id, self.LAYER)
            try:
                await self.mark_positions()
            except Exception:  # noqa: BLE001
                log.exception("%s mark loop error", self.NAME)
            await asyncio.sleep(hb)


class LayerTwo(ExecutionLayer):
    LAYER = 2
    NAME = "Layer Two"
