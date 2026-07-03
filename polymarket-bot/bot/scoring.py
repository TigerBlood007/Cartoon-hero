"""Trader scoring engine.

Research consensus on why most copy-traders lose: they pick wallets off the
leaderboard (which surfaces exactly the most-copied, most-decoyed accounts)
and judge them by raw win rate (meaningless without entry price — buying at
90c wins 90% of the time with zero edge). This module scores candidates the
way the profitable minority does:

  - fee-adjusted ROI over 7/30/90-day windows (consistency beats one hot run)
  - edge vs entry price: did wins exceed the breakeven rate implied by price?
  - profit concentration: penalize wallets whose P&L is one lucky market
  - decoy heuristics: penalize rapid buy-then-sell flips and erratic sizing
  - crowding: penalize top-of-leaderboard prominence (most-copied = most decayed)
  - statistical significance: Wilson lower bound, not point estimates
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field

from .config import Config
from .connection import ConnectionManager
from .database import Database

log = logging.getLogger("scoring")

DAY = 86400.0


def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval — a pessimistic win-rate
    estimate that small samples can't inflate. wlb(9, 10) ~= 0.60, not 0.90."""
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - spread) / denom)


@dataclass
class TraderScore:
    wallet: str
    category: str
    score: float                # 0..1 composite used as signal confidence
    roi_7d: float
    roi_30d: float
    roi_90d: float
    trade_count: int
    edge_vs_price: float        # wlb(win) - avg breakeven price; >0 = real edge
    concentration: float        # share of |pnl| from the single biggest market
    decoy_penalty: float
    last_trade_ts: float
    conviction_bin: int


class TraderScorer:
    def __init__(self, config: Config, conn: ConnectionManager, db: Database):
        self.config = config
        self.conn = conn
        self.db = db
        self._category_keywords = {c.lower(): c for c in config.categories}

    def _categorize(self, text: str) -> str | None:
        text = (text or "").lower()
        for kw, category in self._category_keywords.items():
            if kw in text:
                return category
        return None

    async def score_trader(self, wallet: str) -> list[TraderScore]:
        """Fetch up to 500 recent trades for a wallet and produce one score per
        category it's active in. Fees are modeled per category so ROI is what
        a copier would actually keep."""
        trades = await self.conn.fetch_trader_trades(wallet, limit=500)
        if not trades:
            return []

        now = time.time()
        fee_rates = self.config["fees"]["taker_rates"]
        by_cat: dict[str, list[dict]] = {}
        for t in trades:
            category = self._categorize(t.get("title") or t.get("slug") or "")
            if category:
                by_cat.setdefault(category, []).append(t)

        scores: list[TraderScore] = []
        for category, cat_trades in by_cat.items():
            scores.append(self._score_category(wallet, category, cat_trades,
                                               float(fee_rates.get(category, 0.01)),
                                               now))
        return scores

    def _score_category(self, wallet: str, category: str, trades: list[dict],
                        fee_rate: float, now: float) -> TraderScore:
        windows = {7: [0.0, 0.0], 30: [0.0, 0.0], 90: [0.0, 0.0]}  # [pnl, staked]
        wins = 0
        resolved = 0
        breakeven_sum = 0.0
        pnl_by_market: dict[str, float] = {}
        flip_count = 0
        sizes: list[float] = []
        last_ts = 0.0
        seen_buy_ts: dict[str, float] = {}

        for t in sorted(trades, key=lambda x: float(x.get("timestamp") or 0)):
            ts = float(t.get("timestamp") or 0)
            price = float(t.get("price") or 0)
            size = float(t.get("size") or 0)
            side = str(t.get("side") or "BUY").upper()
            cond = str(t.get("conditionId") or "")
            stake = price * size
            last_ts = max(last_ts, ts)
            sizes.append(stake)

            if side == "BUY":
                seen_buy_ts[cond] = ts
            elif cond in seen_buy_ts and ts - seen_buy_ts[cond] < 600:
                flip_count += 1  # bought then dumped within 10 min: decoy shape

            # Realized outcome proxy from the data API where available.
            profit = t.get("profit")
            won = None
            if profit is not None:
                won = float(profit) > 0
            elif t.get("winningOutcome") is not None:
                won = t.get("outcome") == t.get("winningOutcome")
            if won is None or side != "BUY" or stake <= 0:
                continue

            resolved += 1
            wins += 1 if won else 0
            breakeven_sum += price
            fee = stake * fee_rate * 4 * price * (1 - price)
            pnl = (size * (1 - price) if won else -stake) - fee
            pnl_by_market[cond] = pnl_by_market.get(cond, 0.0) + pnl
            age = now - ts
            for days, acc in windows.items():
                if age <= days * DAY:
                    acc[0] += pnl
                    acc[1] += stake

        def roi(days: int) -> float:
            pnl, staked = windows[days]
            return pnl / staked if staked > 0 else 0.0

        avg_breakeven = breakeven_sum / resolved if resolved else 0.5
        wlb = wilson_lower_bound(wins, resolved)
        edge = wlb - avg_breakeven          # positive only with a real, proven edge

        total_abs = sum(abs(v) for v in pnl_by_market.values()) or 1.0
        concentration = max((abs(v) for v in pnl_by_market.values()), default=0.0) / total_abs

        decoy = 0.0
        if trades and flip_count / max(len(trades), 1) > 0.2:
            decoy += 0.5                    # >20% rapid flips
        if sizes and max(sizes) > 25 * (sorted(sizes)[len(sizes) // 2] or 1):
            decoy += 0.25                   # single bet 25x median size: bait shape

        # Composite: edge is the core (scaled so +5pts of proven edge ~ 0.5),
        # consistency across windows adds, concentration and decoys subtract.
        consistency = sum(1 for d in (7, 30, 90) if roi(d) > 0) / 3.0
        raw = (10.0 * max(edge, 0.0)) * 0.6 + consistency * 0.3 \
            + min(resolved / 100.0, 1.0) * 0.1
        score = max(0.0, min(1.0, raw * (1 - 0.5 * concentration) * (1 - decoy)))

        thresholds = sorted(self.config.conviction_thresholds, reverse=True)
        conviction_bin = next((th for th in thresholds if resolved >= th), 0)

        return TraderScore(wallet, category, round(score, 4), roi(7), roi(30),
                           roi(90), resolved, round(edge, 4),
                           round(concentration, 4), decoy, last_ts, conviction_bin)

    def persist(self, s: TraderScore) -> None:
        self.db.execute(
            "INSERT INTO traders (wallet, category, lifetime_pnl, category_win_rate, "
            "category_trade_count, last_trade_ts, conviction_bin, active, score, "
            "roi_7d, roi_30d, roi_90d, edge_vs_price, concentration, updated_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(wallet, category) DO UPDATE SET "
            "category_trade_count=excluded.category_trade_count, "
            "last_trade_ts=excluded.last_trade_ts, "
            "conviction_bin=excluded.conviction_bin, active=excluded.active, "
            "score=excluded.score, roi_7d=excluded.roi_7d, roi_30d=excluded.roi_30d, "
            "roi_90d=excluded.roi_90d, edge_vs_price=excluded.edge_vs_price, "
            "concentration=excluded.concentration, updated_ts=excluded.updated_ts",
            (s.wallet, s.category, 0.0, 0.0, s.trade_count, s.last_trade_ts,
             s.conviction_bin,
             1 if time.time() - s.last_trade_ts < 3600 * float(
                 self.config["layers"]["layer_one"]["inactive_after_hours"]) else 0,
             s.score, s.roi_7d, s.roi_30d, s.roi_90d, s.edge_vs_price,
             s.concentration, time.time()))
