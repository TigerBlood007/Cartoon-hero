"""Shared execution engine for Layers Two and Three.

What research changed here versus the naive copy-bot design:

- Every decision is made against a FRESH top-of-book, never a stale mid
  (mid-fill backtests overstate returns 30-100%).
- Price-band guard: if the live ask has already moved more than the slippage
  cap past the source trader's fill, the edge is gone — skip. Copying a whale
  through their own price impact is the canonical way copiers lose.
- Fee-aware routing: taker fees (March 2026 schedule) are computed per
  category and charged against the trade's expected value; wide-spread
  markets are entered post-only (resting maker order, zero fee) instead of
  crossing.
- Exchange minimums enforced: limit orders need 5 shares, market orders $1 —
  positions are sized to clear the minimum or skipped, never bounced.
- Exits are copied too: when the source wallet sells a market we copied from
  them, we sell. Riding a position its author abandoned is not a strategy.
- Instrumentation: slippage vs source price and signal→submit latency are
  recorded on every execution, because measuring edge decay IS the research.
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
                 signal_queue: "asyncio.Queue[Signal]"):
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
        for i in range(count):
            category = cats[i % len(cats)]
            conviction = bins[(i // len(cats)) % len(bins)]
            bot_id = f"L{self.LAYER}-{category[:4].lower()}-{conviction}-{i}"
            self.bots.append(ExecBot(bot_id, self.LAYER, category, conviction))
            rows.append((bot_id, self.LAYER, category, conviction,
                         float(self.layer_cfg["scale_factor"]),
                         1 if self.LAYER == 3 else 0, None,
                         float(self.layer_cfg["per_position_usdc"]), now))
        self.db.executemany(
            "INSERT INTO bot_metadata (bot_id, layer, category, conviction_bin, "
            "scale_factor, news_filter, source_wallet, allocation_usdc, updated_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(bot_id) DO NOTHING", rows)
        log.info("%s initialized with %d execution bots", self.NAME, len(self.bots))

    # ── Signal filtering hooks (Layer Three overrides) ───────────────────────

    async def effective_confidence(self, signal: Signal) -> tuple[float, bool]:
        return signal.confidence, True

    # ── Bot selection & adaptive weighting ───────────────────────────────────

    def _bin_rois(self) -> dict[int, float]:
        """Rolling 30-day fee-adjusted ROI per conviction bin (not win rate —
        win rate without entry price is meaningless on prediction markets)."""
        rows = self.db.query(
            "SELECT b.conviction_bin AS bin, "
            "SUM(e.current_pnl) / NULLIF(SUM(e.size_usdc), 0) AS roi, COUNT(*) n "
            "FROM layer_two_executions e JOIN bot_metadata b ON b.bot_id = e.bot_id "
            "WHERE e.layer = ? AND e.status IN ('resolved','exited') "
            "AND e.is_paper = 0 AND e.ts > ? GROUP BY b.conviction_bin",
            (self.LAYER, time.time() - 30 * 86400))
        return {int(r["bin"]): float(r["roi"] or 0) for r in rows if r["n"] >= 10}

    def _pick_bot(self, signal: Signal) -> ExecBot | None:
        rois = self._bin_rois()
        candidates = [
            b for b in self.bots
            if not b.halted and not b.paused and b.category == signal.category
            and not self.db.has_position_in_market(b.bot_id, signal.condition_id)
            and self.db.open_position_count(b.bot_id)
                < int(self.config["risk"]["max_concurrent_positions_per_bot"])]
        if not candidates:
            return None
        candidates.sort(key=lambda b: rois.get(b.conviction_bin, 0.0), reverse=True)
        return candidates[0]

    # ── Entry execution ──────────────────────────────────────────────────────

    async def handle_signal(self, signal: Signal) -> None:
        if signal.side == "SELL":
            await self.handle_exit(signal)
            return

        threshold = float(self.layer_cfg["confidence_threshold"])
        latency_budget = float(self.layer_cfg["latency_budget_seconds"])
        age = time.time() - signal.ts
        if age > latency_budget:
            log.warning("%s skip %s: signal age %.2fs exceeds %.1fs budget",
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

        book = await self.conn.fetch_book(signal.token_id)
        if book is None:
            log.warning("%s skip %s: no live book", self.NAME, signal.signal_id[:8])
            return

        # Price-band guard: the whole failure mode of copy trading is buying
        # the source's price impact. If the ask already ran past the cap, pass.
        max_slip = float(self.layer_cfg["max_slippage"])
        slippage = book["best_ask"] - signal.price
        if slippage > max_slip:
            log.warning("%s skip %s: ask %.3f is %.3f past source fill %.3f "
                        "(cap %.3f) — edge already priced in", self.NAME,
                        signal.signal_id[:8], book["best_ask"], slippage,
                        signal.price, max_slip)
            return
        if book["best_ask"] >= float(self.layer_cfg["max_entry_price"]):
            return  # never chase near-certainties; fee curve + no room to win

        # Sizing: scale the source stake, clamp to per-position budget, then
        # bump to the exchange minimum (5 shares for limit orders) or skip.
        scale = float(self.layer_cfg["scale_factor"])
        per_pos = float(self.layer_cfg["per_position_usdc"])
        wide_spread = book["spread"] > float(self.layer_cfg["max_taker_spread"])
        price = (book["best_bid"] + book["tick"]) if wide_spread else book["best_ask"]
        price = min(price, book["best_ask"])
        shares = max(min(signal.size_usdc * scale, per_pos) / max(price, 0.01),
                     book["min_size"])
        size = round(shares * price, 2)
        if size > per_pos * 1.5:   # min-share bump would exceed budget by >50%
            log.warning("%s skip %s: 5-share exchange minimum needs %.2f USDC, "
                        "over per-position budget %.2f", self.NAME,
                        signal.signal_id[:8], size, per_pos)
            return
        exposure_cap = float(self.layer_cfg["max_total_exposure_usdc"])
        if self.db.layer_exposure(self.LAYER) + size > exposure_cap:
            log.warning("%s skip %s: layer exposure cap %.0f reached",
                        self.NAME, signal.signal_id[:8], exposure_cap)
            return

        # Fee-aware mode: crossing pays the category taker fee; wide spreads
        # rest post-only at bid+tick (zero fee, may not fill — that asymmetry
        # is itself measured, see exec_mode column).
        mode = "post_only" if wide_spread else "taker"
        fee = 0.0 if mode == "post_only" else self.conn.taker_fee(
            signal.category, price, shares)

        exec_id = str(uuid.uuid4())
        submit_ts = time.time()
        try:
            result = await self.conn.place_order(signal.token_id, "BUY", price,
                                                 shares, post_only=wide_spread)
            status, order_id = result["status"], result["order_id"]
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

        latency_ms = (submit_ts - signal.ts) * 1000
        self.db.execute(
            "INSERT INTO layer_two_executions (execution_id, ts, signal_id, layer, "
            "bot_id, condition_id, outcome, exec_price, source_price, slippage, "
            "latency_ms, fee_usdc, exec_mode, size_usdc, shares, filled_ts, status, "
            "is_paper, order_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
            (exec_id, submit_ts, signal.signal_id, self.LAYER, bot.bot_id,
             signal.condition_id, signal.outcome, price, signal.price,
             round(price - signal.price, 4), round(latency_ms, 1), fee, mode,
             size, round(shares, 2),
             submit_ts if status == "dry_run" else None, status, order_id))
        if status != "failed":
            bot.source_wallet = signal.source_wallet
            self.db.execute("UPDATE bot_metadata SET source_wallet=?, updated_ts=? "
                            "WHERE bot_id=?",
                            (signal.source_wallet, time.time(), bot.bot_id))
            log.info("%s EXEC %s: %s %.2f USDC (%.1f sh) @ %.3f %s | slip %+.3f "
                     "| sig->submit %.0fms | fee %.4f", self.NAME, exec_id[:8],
                     bot.bot_id, size, shares, price, mode,
                     price - signal.price, latency_ms, fee)
        self._record_paper_trade(signal, price)

    # ── Exit mirroring ───────────────────────────────────────────────────────

    async def handle_exit(self, signal: Signal) -> None:
        """Source wallet sold a market we copied from them → sell too."""
        rows = self.db.query(
            "SELECT e.* FROM layer_two_executions e JOIN layer_one_signals s "
            "ON s.signal_id = e.signal_id WHERE e.layer = ? AND e.is_paper = 0 "
            "AND e.condition_id = ? AND s.source_wallet = ? "
            "AND e.status IN ('open','partial','filled','dry_run')",
            (self.LAYER, signal.condition_id, signal.source_wallet))
        for r in rows:
            book = await self.conn.fetch_book(signal.token_id)
            exit_price = book["best_bid"] if book else signal.price
            try:
                await self.conn.place_order(signal.token_id, "SELL", exit_price,
                                            max(r["shares"], 5), post_only=False)
            except Exception as exc:  # noqa: BLE001
                log.error("%s exit order failed for %s: %s", self.NAME,
                          r["execution_id"][:8], exc)
                continue
            fee = self.conn.taker_fee(signal.category, exit_price, r["shares"])
            pnl = round((exit_price - r["exec_price"]) * r["shares"]
                        - r["fee_usdc"] - fee, 4)
            self.db.execute(
                "UPDATE layer_two_executions SET status='exited', current_pnl=?, "
                "filled_ts=? WHERE execution_id=?",
                (pnl, time.time(), r["execution_id"]))
            self.db.record_bot_result(r["bot_id"], pnl, pnl > 0)
            log.info("%s EXIT %s: source sold, mirrored @ %.3f, pnl %+.3f",
                     self.NAME, r["execution_id"][:8], exit_price, pnl)

    def _record_paper_trade(self, signal: Signal, price: float) -> None:
        """Parallel $5000 paper simulation at a larger scale factor — tests
        whether the micro edge survives at size."""
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
            "bot_id, condition_id, outcome, exec_price, source_price, size_usdc, "
            "shares, status, is_paper) VALUES (?,?,?,?,?,?,?,?,?,?,?,'dry_run',1)",
            (str(uuid.uuid4()), time.time(), signal.signal_id, self.LAYER,
             f"PAPER-L{self.LAYER}", signal.condition_id, signal.outcome, price,
             signal.price, size, round(size / max(price, 0.01), 2)))

    # ── Mark-to-market / resolution sweep ────────────────────────────────────

    async def mark_positions(self) -> None:
        rows = self.db.query(
            "SELECT * FROM layer_two_executions WHERE layer = ? "
            "AND status IN ('open','partial','filled','dry_run')", (self.LAYER,))
        for r in rows:
            market = await self.conn.fetch_market(r["condition_id"])
            if market and (market.get("closed") or market.get("resolved")):
                winning = str(market.get("winningOutcome")
                              or market.get("outcome") or "").upper()
                won = winning == str(r["outcome"]).upper()
                shares = r["shares"] or (r["size_usdc"] / max(r["exec_price"], 0.01))
                pnl = round((shares * (1 - r["exec_price"]) if won
                             else -r["size_usdc"]) - (r["fee_usdc"] or 0), 4)
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
