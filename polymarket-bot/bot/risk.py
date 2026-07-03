"""Risk management: circuit breakers and adaptive capital reallocation.

Runs as its own loop. After every ten resolved trades per bot — or every
twelve hours, whichever comes first — bot performance is evaluated: top
performers (>55% win rate over 10+ trades) receive $1 shifted from bottom
performers (<45%), implemented by swapping which signal sources the bottom
bot follows. Any bot whose cumulative P&L drops below -$2 (200% of its $1
allocation) is halted immediately.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .config import Config
from .database import Database
from .execution import ExecutionLayer

log = logging.getLogger("risk")


class RiskManager:
    def __init__(self, config: Config, db: Database,
                 layers: list[ExecutionLayer]):
        self.config = config
        self.db = db
        self.layers = layers
        self._last_eval = 0.0
        self._trades_at_last_eval: dict[str, int] = {}

    # ── Circuit breaker (checked every cycle, low latency) ──────────────────

    def check_circuit_breakers(self) -> None:
        limit = float(self.config["risk"]["circuit_breaker_pnl_usdc"])
        rows = self.db.query(
            "SELECT bot_id, layer, cumulative_pnl FROM bot_metadata "
            "WHERE halted = 0 AND cumulative_pnl < ?", (limit,))
        for r in rows:
            self.db.execute(
                "UPDATE bot_metadata SET halted = 1, halted_reason = ?, "
                "updated_ts = ? WHERE bot_id = ?",
                (f"circuit breaker: pnl {r['cumulative_pnl']:.2f} < {limit:.2f}",
                 time.time(), r["bot_id"]))
            for layer in self.layers:
                for bot in layer.bots:
                    if bot.bot_id == r["bot_id"]:
                        bot.halted = True
            log.error("CIRCUIT BREAKER: bot %s halted at pnl %.2f (limit %.2f)",
                      r["bot_id"], r["cumulative_pnl"], limit)

    # ── Periodic evaluation + reallocation ───────────────────────────────────

    def _due(self) -> bool:
        hours = float(self.config["risk"]["reallocation_hours_interval"])
        if time.time() - self._last_eval >= hours * 3600:
            return True
        interval = int(self.config["risk"]["reallocation_trade_interval"])
        rows = self.db.query("SELECT bot_id, trade_count FROM bot_metadata "
                             "WHERE layer IN (2, 3)")
        return any(r["trade_count"] - self._trades_at_last_eval.get(r["bot_id"], 0)
                   >= interval for r in rows)

    def evaluate_and_reallocate(self) -> None:
        risk = self.config["risk"]
        min_trades = int(risk["min_trades_for_evaluation"])
        top_wr = float(risk["top_performer_win_rate"])
        bottom_wr = float(risk["bottom_performer_win_rate"])

        for layer_num in (2, 3):
            top = self.db.query(
                "SELECT bot_id, source_wallet, win_rate FROM bot_metadata "
                "WHERE layer = ? AND halted = 0 AND trade_count >= ? "
                "AND win_rate > ? ORDER BY win_rate DESC", (layer_num, min_trades, top_wr))
            bottom = self.db.query(
                "SELECT bot_id, win_rate FROM bot_metadata "
                "WHERE layer = ? AND halted = 0 AND trade_count >= ? "
                "AND win_rate < ? ORDER BY win_rate ASC", (layer_num, min_trades, bottom_wr))
            for loser, winner in zip(bottom, top):
                # Shift $1 by repointing the losing bot at the winner's source.
                self.db.execute(
                    "UPDATE bot_metadata SET source_wallet = ?, "
                    "allocation_usdc = allocation_usdc - 1.0, updated_ts = ? "
                    "WHERE bot_id = ? AND allocation_usdc >= 1.0",
                    (winner["source_wallet"], time.time(), loser["bot_id"]))
                self.db.execute(
                    "UPDATE bot_metadata SET allocation_usdc = allocation_usdc + 1.0, "
                    "updated_ts = ? WHERE bot_id = ?", (time.time(), winner["bot_id"]))
                log.warning(
                    "REALLOCATION L%d: $1 shifted from %s (wr %.0f%%) to %s "
                    "(wr %.0f%%); %s now follows %s", layer_num, loser["bot_id"],
                    loser["win_rate"] * 100, winner["bot_id"],
                    winner["win_rate"] * 100, loser["bot_id"],
                    (winner["source_wallet"] or "?")[:12])

        self._last_eval = time.time()
        for r in self.db.query("SELECT bot_id, trade_count FROM bot_metadata "
                               "WHERE layer IN (2, 3)"):
            self._trades_at_last_eval[r["bot_id"]] = r["trade_count"]

    async def run(self) -> None:
        while True:
            try:
                self.check_circuit_breakers()
                if self._due():
                    self.evaluate_and_reallocate()
            except Exception:  # noqa: BLE001
                log.exception("Risk manager loop error")
            await asyncio.sleep(30)
