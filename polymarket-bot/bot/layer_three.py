"""Layer Three: news-aware variant of Layer Two.

Identical execution engine, but every Layer One signal is checked against
recent news first. Execution rule:

    confidence x (1 + 0.5 x catalyst_strength) > 0.60

so insider-blind bets need higher raw confidence while catalyst-aligned bets
clear the bar more readily. Results are tracked separately so the two-week
news-informed vs news-blind comparison is a single SQL query.
"""

from __future__ import annotations

import logging

from .execution import ExecutionLayer
from .layer_one import Signal
from .news import NewsFilter

log = logging.getLogger("layer3")


class LayerThree(ExecutionLayer):
    LAYER = 3
    NAME = "Layer Three"

    def attach_news_filter(self, news: NewsFilter) -> None:
        self.news = news

    async def effective_confidence(self, signal: Signal) -> tuple[float, bool]:
        if not self.layer_cfg.get("news_filter_enabled", True):
            return signal.confidence, True
        check = await self.news.check(signal.signal_id, signal.market_slug,
                                      signal.category)
        boost = float(self.layer_cfg["catalyst_boost"])
        effective = signal.confidence * (1 + boost * check.catalyst_score)
        threshold = float(self.layer_cfg["confidence_threshold"])
        decision = "execute" if effective > threshold else "skip"
        self.db.execute(
            "UPDATE layer_three_news_checks SET recommendation = ? "
            "WHERE signal_id = ? AND recommendation = 'pending'",
            (decision, signal.signal_id))
        log.info("Layer Three news check %s: catalyst %.1f (cached=%s) -> "
                 "effective %.2f (%s)", signal.signal_id[:8], check.catalyst_score,
                 check.cached, effective, decision)
        return effective, True
