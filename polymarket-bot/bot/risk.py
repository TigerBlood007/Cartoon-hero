"""Risk management: circuit breakers and statistically honest reallocation.

The naive version of this loop chased noise: a bot at 60% win rate over ten
trades is indistinguishable from a coin flip, and win rate itself is
meaningless without entry price (buying at 90c "wins" 90% of the time with
zero edge). Reallocation therefore uses:

  - the Wilson lower bound of each bot's win rate (small samples can't inflate it)
  - compared against the bot's own average entry price + fee drag, which is
    the actual breakeven line for that bot's trades
  - promote when even the pessimistic estimate clears breakeven; demote when
    even the optimistic estimate can't reach it.

Circuit breaker unchanged: any bot below -200% of its allocation halts
immediately, checked every cycle.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

from .config import Config
from .database import Database
from .execution import ExecutionLayer
from .scoring import wilson_lower_bound

log = logging.getLogger("risk")


def wilson_upper_bound(wins: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return min(1.0, (centre + spread) / denom)


class RiskManager:
    def __init__(self, config: Config, db: Database,
                 layers: list[ExecutionLayer]):
        self.config = config
        self.db = db
        self.layers = layers
        self._last_eval = 0.0
        self._trades_at_last_eval: dict[str, int] = {}

    # ── Circuit breaker ──────────────────────────────────────────────────────

    def check_circuit_breakers(self) -> None:
        limit = float(self.config["risk"]["circuit_breaker_pnl_usdc"])
        rows = self.db.query(
            "SELECT bot_id, cumulative_pnl FROM bot_metadata "
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

    # ── Evaluation cadence ───────────────────────────────────────────────────

    def _due(self) -> bool:
        hours = float(self.config["risk"]["reallocation_hours_interval"])
        if time.time() - self._last_eval >= hours * 3600:
            return True
        interval = int(self.config["risk"]["reallocation_trade_interval"])
        rows = self.db.query("SELECT bot_id, trade_count FROM bot_metadata "
                             "WHERE layer IN (2, 3)")
        return any(r["trade_count"] - self._trades_at_last_eval.get(r["bot_id"], 0)
                   >= interval for r in rows)

    def _bot_breakeven(self, bot_id: str) -> float | None:
        """Breakeven win rate for this bot's actual trades: average entry price
        plus fee drag per dollar staked."""
        r = self.db.query_one(
            "SELECT AVG(exec_price) p, SUM(fee_usdc) / NULLIF(SUM(size_usdc),0) f "
            "FROM layer_two_executions WHERE bot_id = ? "
            "AND status IN ('resolved','exited') AND is_paper = 0", (bot_id,))
        if not r or r["p"] is None:
            return None
        return float(r["p"]) + float(r["f"] or 0)

    def evaluate_and_reallocate(self) -> None:
        risk = self.config["risk"]
        min_trades = int(risk["min_trades_for_evaluation"])

        for layer_num in (2, 3):
            rows = self.db.query(
                "SELECT bot_id, source_wallet, trade_count, win_count, win_rate "
                "FROM bot_metadata WHERE layer = ? AND halted = 0 "
                "AND trade_count >= ?", (layer_num, min_trades))
            proven, failing = [], []
            for r in rows:
                breakeven = self._bot_breakeven(r["bot_id"])
                if breakeven is None:
                    continue
                lo = wilson_lower_bound(r["win_count"], r["trade_count"])
                hi = wilson_upper_bound(r["win_count"], r["trade_count"])
                if lo > breakeven:      # pessimistic estimate still beats breakeven
                    proven.append((lo - breakeven, r))
                elif hi < breakeven:    # even optimistic estimate can't break even
                    failing.append((breakeven - hi, r))
            proven.sort(key=lambda x: x[0], reverse=True)
            failing.sort(key=lambda x: x[0], reverse=True)

            for (_, loser), (_, winner) in zip(failing, proven):
                self.db.execute(
                    "UPDATE bot_metadata SET source_wallet = ?, "
                    "allocation_usdc = allocation_usdc - 1.0, updated_ts = ? "
                    "WHERE bot_id = ? AND allocation_usdc >= 1.0",
                    (winner["source_wallet"], time.time(), loser["bot_id"]))
                self.db.execute(
                    "UPDATE bot_metadata SET allocation_usdc = allocation_usdc + 1.0, "
                    "updated_ts = ? WHERE bot_id = ?", (time.time(), winner["bot_id"]))
                log.warning(
                    "REALLOCATION L%d: $1 shifted from %s (statistically below "
                    "breakeven) to %s (proven above); %s now follows %s",
                    layer_num, loser["bot_id"], winner["bot_id"], loser["bot_id"],
                    (winner["source_wallet"] or "?")[:12])
            if proven or failing:
                log.info("L%d evaluation: %d proven above breakeven, %d proven "
                         "below, %d inconclusive", layer_num, len(proven),
                         len(failing), len(rows) - len(proven) - len(failing))

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
