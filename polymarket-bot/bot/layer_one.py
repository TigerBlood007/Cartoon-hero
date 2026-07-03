"""Layer One: signal detection.

One hundred $1 micro-bot instances (5 categories x 5 conviction bins x 4
replicas), each monitoring the top trader in its (category, conviction) pair.
Polls the leaderboard every 15 minutes, trader trade feeds every few seconds,
and writes confidence-scored signals to layer_one_signals. Never trades.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from .config import Config
from .connection import ConnectionManager
from .database import Database

log = logging.getLogger("layer1")


@dataclass
class Signal:
    signal_id: str
    ts: float
    source_wallet: str
    condition_id: str
    market_slug: str
    category: str
    outcome: str
    token_id: str
    size_usdc: float
    price: float
    confidence: float
    detector_bot_id: str


@dataclass
class L1Bot:
    bot_id: str
    category: str
    conviction_bin: int
    replica: int
    monitored_wallet: str | None = None
    seen_trade_ids: set[str] = field(default_factory=set)


class LayerOne:
    def __init__(self, config: Config, conn: ConnectionManager, db: Database):
        self.config = config
        self.conn = conn
        self.db = db
        self.bots: list[L1Bot] = []
        self.signal_queue: asyncio.Queue[Signal] = asyncio.Queue()
        self._category_keywords = {c.lower(): c for c in config.categories}
        self._build_bots()

    def _build_bots(self) -> None:
        """5 categories x 5 conviction bins x 4 replicas = 100 instances."""
        replicas = self.config["layers"]["layer_one"]["bot_count"] // (
            len(self.config.categories) * len(self.config.conviction_thresholds))
        now = time.time()
        rows = []
        for category in self.config.categories:
            for threshold in self.config.conviction_thresholds:
                for r in range(max(replicas, 1)):
                    bot_id = f"L1-{category[:4].lower()}-{threshold}-{r}"
                    self.bots.append(L1Bot(bot_id, category, threshold, r))
                    rows.append((bot_id, 1, category, threshold, None, 0, None,
                                 1.0, now))
        self.db.executemany(
            "INSERT INTO bot_metadata (bot_id, layer, category, conviction_bin, "
            "scale_factor, news_filter, source_wallet, allocation_usdc, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(bot_id) DO NOTHING",
            rows,
        )
        log.info("Layer One initialized with %d signal bots", len(self.bots))

    # ── Leaderboard / trader profiling (every 15 minutes) ────────────────────

    def _categorize(self, title_or_slug: str) -> str | None:
        text = (title_or_slug or "").lower()
        for kw, category in self._category_keywords.items():
            if kw in text:
                return category
        return None

    async def refresh_traders(self) -> None:
        """Fetch leaderboard, compute per-category win rates and conviction
        bins, store in traders table, and (re)assign each bot's target."""
        leaders = await self.conn.fetch_leaderboard()
        if not leaders:
            log.warning("Leaderboard empty this cycle; keeping previous assignments")
            return

        inactive_cutoff = time.time() - 3600 * float(
            self.config["layers"]["layer_one"]["inactive_after_hours"])
        thresholds = sorted(self.config.conviction_thresholds, reverse=True)

        for entry in leaders[:100]:
            wallet = entry.get("proxyWallet") or entry.get("wallet") or entry.get("address")
            if not wallet:
                continue
            lifetime_pnl = float(entry.get("amount") or entry.get("pnl") or 0)
            trades = await self.conn.fetch_trader_trades(wallet, limit=100)
            per_cat: dict[str, dict] = {}
            for t in trades:
                category = self._categorize(t.get("title") or t.get("slug") or "")
                if not category:
                    continue
                stats = per_cat.setdefault(category,
                                           {"wins": 0, "total": 0, "last_ts": 0.0})
                stats["total"] += 1
                stats["last_ts"] = max(stats["last_ts"],
                                       float(t.get("timestamp") or 0))
                # Winning trade proxy: redeemable / profitable outcome flag if
                # exposed, else price improvement on resolved markets.
                if t.get("outcome") == t.get("winningOutcome") or t.get("profit", 0) > 0:
                    stats["wins"] += 1
            for category, stats in per_cat.items():
                win_rate = stats["wins"] / stats["total"] if stats["total"] else 0.0
                conviction_bin = next(
                    (th for th in thresholds if stats["total"] >= th), 0)
                active = 1 if stats["last_ts"] >= inactive_cutoff else 0
                if not active:
                    log.warning("Trader %s inactive >24h in %s; removed from sources",
                                wallet[:10], category)
                self.db.execute(
                    "INSERT INTO traders (wallet, category, lifetime_pnl, "
                    "category_win_rate, category_trade_count, last_trade_ts, "
                    "conviction_bin, active, updated_ts) VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(wallet, category) DO UPDATE SET "
                    "lifetime_pnl=excluded.lifetime_pnl, "
                    "category_win_rate=excluded.category_win_rate, "
                    "category_trade_count=excluded.category_trade_count, "
                    "last_trade_ts=excluded.last_trade_ts, "
                    "conviction_bin=excluded.conviction_bin, "
                    "active=excluded.active, updated_ts=excluded.updated_ts",
                    (wallet, category, lifetime_pnl, win_rate, stats["total"],
                     stats["last_ts"], conviction_bin, active, time.time()),
                )
        self._assign_targets()

    def _assign_targets(self) -> None:
        """Point each bot at the top active trader in its (category, bin) pair.
        Replicas take rank 1st/2nd/3rd/4th so the 4 bots per pair diversify."""
        for bot in self.bots:
            rows = self.db.query(
                "SELECT wallet FROM traders WHERE category = ? AND active = 1 "
                "AND conviction_bin >= ? ORDER BY category_win_rate DESC, "
                "lifetime_pnl DESC LIMIT 4",
                (bot.category, bot.conviction_bin),
            )
            new_wallet = rows[min(bot.replica, len(rows) - 1)]["wallet"] if rows else None
            if new_wallet != bot.monitored_wallet:
                log.info("%s now monitoring %s", bot.bot_id,
                         (new_wallet or "nobody")[:12])
                bot.monitored_wallet = new_wallet
                bot.seen_trade_ids.clear()
                self.db.execute(
                    "UPDATE bot_metadata SET source_wallet = ?, updated_ts = ? "
                    "WHERE bot_id = ?", (new_wallet, time.time(), bot.bot_id))

    # ── Trade detection loop ─────────────────────────────────────────────────

    def _confidence(self, wallet: str, category: str) -> float:
        row = self.db.query_one(
            "SELECT category_win_rate, category_trade_count FROM traders "
            "WHERE wallet = ? AND category = ?", (wallet, category))
        if not row:
            return 0.0
        return min(1.0, row["category_win_rate"] * row["category_trade_count"] / 100.0)

    async def detect_once(self) -> int:
        """Poll each monitored wallet once (deduped across replicas) and emit
        signals for unseen trades. Returns number of new signals."""
        wallets = {b.monitored_wallet for b in self.bots if b.monitored_wallet}
        trades_by_wallet: dict[str, list[dict]] = {}
        for wallet in wallets:
            trades_by_wallet[wallet] = await self.conn.fetch_trader_trades(wallet, limit=20)

        emitted = 0
        for bot in self.bots:
            self.db.heartbeat(bot.bot_id, 1)
            wallet = bot.monitored_wallet
            if not wallet:
                continue
            for t in trades_by_wallet.get(wallet, []):
                trade_id = str(t.get("transactionHash") or t.get("id") or "")
                if not trade_id or trade_id in bot.seen_trade_ids:
                    continue
                bot.seen_trade_ids.add(trade_id)
                category = self._categorize(t.get("title") or t.get("slug") or "")
                if category != bot.category:
                    continue
                ts = float(t.get("timestamp") or time.time())
                if time.time() - ts > 120:   # stale history on first poll
                    continue
                confidence = self._confidence(wallet, category)
                sig = Signal(
                    signal_id=str(uuid.uuid4()),
                    ts=ts,
                    source_wallet=wallet,
                    condition_id=str(t.get("conditionId") or ""),
                    market_slug=str(t.get("slug") or ""),
                    category=category,
                    outcome=str(t.get("outcome") or "YES"),
                    token_id=str(t.get("asset") or t.get("tokenId") or ""),
                    size_usdc=float(t.get("size") or 0) * float(t.get("price") or 0),
                    price=float(t.get("price") or 0),
                    confidence=confidence,
                    detector_bot_id=bot.bot_id,
                )
                try:
                    self.db.execute(
                        "INSERT INTO layer_one_signals (signal_id, ts, source_wallet, "
                        "condition_id, market_slug, category, outcome, token_id, "
                        "size_usdc, price, confidence, detector_bot_id, source_trade_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sig.signal_id, sig.ts, sig.source_wallet, sig.condition_id,
                         sig.market_slug, sig.category, sig.outcome, sig.token_id,
                         sig.size_usdc, sig.price, sig.confidence, sig.detector_bot_id,
                         trade_id),
                    )
                except Exception:  # UNIQUE(source_wallet, source_trade_id) race
                    continue
                if sig.token_id:
                    self.conn.subscribe_assets({sig.token_id})
                await self.signal_queue.put(sig)
                emitted += 1
                log.info("SIGNAL %s: %s bet %.2f USDC on %s @ %.3f (conf %.2f)",
                         sig.signal_id[:8], wallet[:10], sig.size_usdc,
                         sig.outcome, sig.price, sig.confidence)
        return emitted

    async def run(self) -> None:
        poll_s = float(self.config["layers"]["layer_one"]["trade_poll_seconds"])
        lb_s = float(self.config["layers"]["layer_one"]["leaderboard_poll_seconds"])
        last_lb = 0.0
        while True:
            try:
                if time.time() - last_lb >= lb_s:
                    await self.refresh_traders()
                    last_lb = time.time()
                await self.detect_once()
            except Exception:  # noqa: BLE001
                log.exception("Layer One loop error")
            await asyncio.sleep(poll_s)
