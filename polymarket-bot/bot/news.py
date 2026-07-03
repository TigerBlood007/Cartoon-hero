"""Layer Three news filter: catalyst strength scoring with a 1-hour
per-slug cache so we never hammer the news API's rate limits."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass

from .config import Config
from .connection import ConnectionManager
from .database import Database

log = logging.getLogger("news")


@dataclass
class NewsCheck:
    catalyst_score: float          # 0.0 (none) / 0.3 (minor) / 0.7 (major)
    headlines: list[str]
    cached: bool = False


class NewsFilter:
    def __init__(self, config: Config, conn: ConnectionManager, db: Database):
        self.config = config
        self.conn = conn
        self.db = db
        self._cache: dict[str, tuple[float, NewsCheck]] = {}
        self._cache_ttl = float(config["layers"]["layer_three"]["news_cache_seconds"])

    def _query_terms(self, market_slug: str, category: str) -> str:
        # The category name ("Google", "Bitcoin", …) is the primary search term;
        # the slug adds market-specific words.
        slug_words = [w for w in (market_slug or "").replace("-", " ").split()
                      if len(w) > 3][:4]
        return " ".join(dict.fromkeys([category, *slug_words]))

    async def check(self, signal_id: str, market_slug: str, category: str) -> NewsCheck:
        key = (market_slug or category).lower()
        cached = self._cache.get(key)
        now = time.time()
        if cached and now - cached[0] < self._cache_ttl:
            result = NewsCheck(cached[1].catalyst_score, cached[1].headlines, cached=True)
        else:
            result = await self._fetch_and_score(market_slug, category)
            self._cache[key] = (now, result)

        self.db.execute(
            "INSERT INTO layer_three_news_checks "
            "(check_id, signal_id, ts, market_slug, news_events, catalyst_score, "
            " recommendation) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), signal_id, now, market_slug,
             json.dumps(result.headlines[:5]), result.catalyst_score,
             "pending"),  # recommendation updated by layer_three after threshold math
        )
        return result

    async def _fetch_and_score(self, market_slug: str, category: str) -> NewsCheck:
        api_key = self.config.secrets.news_api_key
        if not api_key:
            log.warning("NEWS_API_KEY missing; catalyst score defaults to 0")
            return NewsCheck(0.0, [])

        news_cfg = self.config["news"]
        lookback_h = self.config["layers"]["layer_three"]["news_lookback_hours"]
        from_ts = time.strftime("%Y-%m-%dT%H:%M:%S",
                                time.gmtime(time.time() - lookback_h * 3600))
        try:
            assert self.conn.session is not None
            async with self.conn.session.get(
                news_cfg["endpoint"],
                params={
                    "q": self._query_terms(market_slug, category),
                    "from": from_ts,
                    "sortBy": "publishedAt",
                    "pageSize": 20,
                    "language": "en",
                    "apiKey": api_key,
                },
            ) as resp:
                if resp.status == 429:
                    log.warning("News API rate-limited; scoring 0 and relying on cache")
                    return NewsCheck(0.0, [])
                data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log.error("News API error: %s", exc)
            return NewsCheck(0.0, [])

        articles = data.get("articles", []) or []
        headlines = [a.get("title", "") for a in articles if a.get("title")]

        if not headlines:
            return NewsCheck(0.0, [])

        major_keywords = [k.lower() for k in news_cfg["major_keywords"]]
        has_major_kw = any(kw in h.lower() for h in headlines for kw in major_keywords)
        if has_major_kw or len(headlines) >= int(news_cfg["major_news_threshold"]):
            return NewsCheck(0.7, headlines)   # major: earnings, regulatory, etc.
        if len(headlines) >= int(news_cfg["minor_news_threshold"]):
            return NewsCheck(0.3, headlines)
        return NewsCheck(0.0, headlines)
