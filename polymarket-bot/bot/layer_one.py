"""Layer One: signal detection (stream-driven).

One hundred detector cells (5 categories x 5 conviction bins x 4 replicas)
define the research grid. Detection itself is push-based: every platform trade
arrives on Polymarket's public activity socket in well under a second, and we
match it against the set of wallets our scorer currently rates as worth
copying. Data-API polling survives only as the history backfill the scorer
reads — never as the detection path (5-30s lag = dead edge).

Discovery draws candidates from two pools: the leaderboard (seed only — it
surfaces the most-copied wallets) and wallets observed live on the stream
making clean category trades. The TraderScorer decides who actually gets
tracked; raw leaderboard rank never does.

Exits are signals too: a tracked wallet SELLing a market we copied emits a
SELL signal so execution layers can mirror the exit instead of riding a
position its source already abandoned.
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
from .scoring import TraderScorer
from .stream import ActivityStream

log = logging.getLogger("layer1")


@dataclass
class Signal:
    signal_id: str
    ts: float                  # when the source trade happened
    detected_ts: float         # when the stream delivered it to us
    source_wallet: str
    condition_id: str
    market_slug: str
    category: str
    outcome: str
    side: str                  # BUY = entry signal, SELL = exit signal
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


class LayerOne:
    def __init__(self, config: Config, conn: ConnectionManager, db: Database,
                 stream: ActivityStream):
        self.config = config
        self.conn = conn
        self.db = db
        self.stream = stream
        self.scorer = TraderScorer(config, conn, db)
        self.bots: list[L1Bot] = []
        self.signal_queue: asyncio.Queue[Signal] = asyncio.Queue()
        self._tracked: dict[str, list[L1Bot]] = {}      # wallet -> detector cells
        self._seen_trade_ids: set[str] = set()
        self._candidate_counts: dict[str, int] = {}     # stream-discovered wallets
        self._scored_recently: dict[str, float] = {}
        self._category_keywords = {c.lower(): c for c in config.categories}
        self._build_bots()
        stream.on_trade(self._on_stream_trade)

    def _build_bots(self) -> None:
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
            rows)
        log.info("Layer One initialized with %d detector cells", len(self.bots))

    def _categorize(self, text: str) -> str | None:
        text = (text or "").lower()
        for kw, category in self._category_keywords.items():
            if kw in text:
                return category
        return None

    # ── Discovery + scoring cycle (every 15 minutes) ─────────────────────────

    async def refresh_traders(self) -> None:
        """Score leaderboard seeds and stream-discovered candidates, then
        reassign each detector cell to the best-scored wallet in its
        (category, conviction) pair."""
        candidates: list[str] = []
        for entry in await self.conn.fetch_leaderboard():
            w = entry.get("proxyWallet") or entry.get("wallet") or entry.get("address")
            if w:
                candidates.append(w)
        # Stream discovery: wallets seen trading our categories most often.
        hot = sorted(self._candidate_counts, key=self._candidate_counts.get,
                     reverse=True)[:30]
        candidates.extend(hot)
        self._candidate_counts.clear()

        now = time.time()
        budget = int(self.config["layers"]["layer_one"]["score_scans_per_cycle"])
        scanned = 0
        for wallet in dict.fromkeys(candidates):
            if scanned >= budget:
                break
            if now - self._scored_recently.get(wallet, 0) < 6 * 3600:
                continue  # rescore each wallet at most every 6h
            self._scored_recently[wallet] = now
            scanned += 1
            for s in await self.scorer.score_trader(wallet):
                self.scorer.persist(s)
        log.info("Scoring cycle done: %d wallets scanned", scanned)
        self._assign_targets()

    def _assign_targets(self) -> None:
        """Rank tracked wallets by composite score (never leaderboard rank).
        Replicas take ranks 1-4 so each (category, bin) pair diversifies
        across four wallets — the portfolio approach."""
        min_score = float(self.config["layers"]["layer_one"]["min_tracked_score"])
        self._tracked.clear()
        for bot in self.bots:
            rows = self.db.query(
                "SELECT wallet FROM traders WHERE category = ? AND active = 1 "
                "AND conviction_bin >= ? AND score >= ? "
                "ORDER BY score DESC, edge_vs_price DESC LIMIT 4",
                (bot.category, bot.conviction_bin, min_score))
            new_wallet = rows[min(bot.replica, len(rows) - 1)]["wallet"] if rows else None
            if new_wallet != bot.monitored_wallet:
                log.info("%s now monitoring %s", bot.bot_id, (new_wallet or "nobody")[:12])
                bot.monitored_wallet = new_wallet
                self.db.execute(
                    "UPDATE bot_metadata SET source_wallet = ?, updated_ts = ? "
                    "WHERE bot_id = ?", (new_wallet, time.time(), bot.bot_id))
            if new_wallet:
                self._tracked.setdefault(new_wallet, []).append(bot)
        log.info("Tracking %d unique wallets across %d cells",
                 len(self._tracked), sum(len(v) for v in self._tracked.values()))

    # ── Stream callback: the actual detection path ───────────────────────────

    async def _on_stream_trade(self, p: dict, detected_ts: float) -> None:
        wallet = str(p.get("proxyWallet") or "")
        category = self._categorize(p.get("slug") or p.get("eventSlug") or "")
        if not wallet:
            return
        if wallet not in self._tracked:
            if category:  # count for stream discovery
                self._candidate_counts[wallet] = self._candidate_counts.get(wallet, 0) + 1
            return

        trade_id = str(p.get("transactionHash") or "") + str(p.get("asset") or "")
        if not trade_id or trade_id in self._seen_trade_ids:
            return
        self._seen_trade_ids.add(trade_id)
        if len(self._seen_trade_ids) > 50000:
            self._seen_trade_ids.clear()

        cells = [b for b in self._tracked[wallet] if b.category == category]
        if not cells:
            return
        bot = cells[0]
        ts = float(p.get("timestamp") or detected_ts)
        if ts > 1e12:
            ts /= 1000.0
        latency_ms = max(0.0, (detected_ts - ts) * 1000)

        row = self.db.query_one(
            "SELECT score FROM traders WHERE wallet = ? AND category = ?",
            (wallet, category))
        confidence = float(row["score"]) if row else 0.0

        sig = Signal(
            signal_id=str(uuid.uuid4()), ts=ts, detected_ts=detected_ts,
            source_wallet=wallet, condition_id=str(p.get("conditionId") or ""),
            market_slug=str(p.get("slug") or ""), category=category,
            outcome=str(p.get("outcome") or "YES"),
            side=str(p.get("side") or "BUY").upper(),
            token_id=str(p.get("asset") or ""),
            size_usdc=float(p.get("size") or 0) * float(p.get("price") or 0),
            price=float(p.get("price") or 0), confidence=confidence,
            detector_bot_id=bot.bot_id)
        try:
            self.db.execute(
                "INSERT INTO layer_one_signals (signal_id, ts, source_wallet, "
                "condition_id, market_slug, category, outcome, side, token_id, "
                "size_usdc, price, confidence, detected_ts, detection_latency_ms, "
                "detector_bot_id, source_trade_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sig.signal_id, sig.ts, sig.source_wallet, sig.condition_id,
                 sig.market_slug, sig.category, sig.outcome, sig.side,
                 sig.token_id, sig.size_usdc, sig.price, sig.confidence,
                 sig.detected_ts, latency_ms, bot.bot_id, trade_id))
        except Exception:  # UNIQUE(source_wallet, source_trade_id) race
            return
        await self.signal_queue.put(sig)
        log.info("SIGNAL %s: %s %s %.2f USDC on %s @ %.3f (conf %.2f, det %.0fms)",
                 sig.signal_id[:8], wallet[:10], sig.side, sig.size_usdc,
                 sig.outcome, sig.price, sig.confidence, latency_ms)

    # ── Maintenance loop (heartbeats, periodic rescoring) ────────────────────

    async def run(self) -> None:
        lb_s = float(self.config["layers"]["layer_one"]["leaderboard_poll_seconds"])
        hb = float(self.config["runtime"]["heartbeat_seconds"])
        last_lb = 0.0
        while True:
            try:
                if time.time() - last_lb >= lb_s:
                    await self.refresh_traders()
                    last_lb = time.time()
                for bot in self.bots:
                    self.db.heartbeat(bot.bot_id, 1)
            except Exception:  # noqa: BLE001
                log.exception("Layer One maintenance error")
            await asyncio.sleep(hb)
