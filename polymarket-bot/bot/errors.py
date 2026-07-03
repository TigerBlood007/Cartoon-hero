"""Error taxonomy: transient (retry with backoff) vs permanent (skip + alert),
plus CLOB-specific categorization (insufficient balance / invalid token /
rate-limited / market closed)."""

from __future__ import annotations

import asyncio
import logging
import random
from enum import Enum
from typing import Awaitable, Callable, TypeVar

log = logging.getLogger("errors")

T = TypeVar("T")


class ErrorKind(Enum):
    TRANSIENT = "transient"                  # network timeout, rate limit -> retry
    PERMANENT = "permanent"                  # bad credentials, market closed -> skip
    INSUFFICIENT_BALANCE = "insufficient_balance"  # pause the bot
    INVALID_TOKEN = "invalid_token"          # skip this trade
    RATE_LIMITED = "rate_limited"            # retry with backoff
    MARKET_CLOSED = "market_closed"          # log, never retry


class BotError(Exception):
    def __init__(self, message: str, kind: ErrorKind):
        super().__init__(message)
        self.kind = kind


def classify_clob_error(exc: Exception) -> ErrorKind:
    """Map a raw CLOB/HTTP exception onto our taxonomy by message inspection."""
    text = str(exc).lower()
    if "insufficient" in text and ("balance" in text or "funds" in text or "allowance" in text):
        return ErrorKind.INSUFFICIENT_BALANCE
    if "invalid token" in text or "token id" in text or "no such token" in text:
        return ErrorKind.INVALID_TOKEN
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return ErrorKind.RATE_LIMITED
    if "closed" in text or "resolved" in text or "not accepting" in text:
        return ErrorKind.MARKET_CLOSED
    if "401" in text or "403" in text or "unauthorized" in text or "credential" in text:
        return ErrorKind.PERMANENT
    if "timeout" in text or "connection" in text or "temporarily" in text or "503" in text:
        return ErrorKind.TRANSIENT
    return ErrorKind.TRANSIENT  # default: give it a retry before giving up


async def retry_transient(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    label: str = "operation",
) -> T:
    """Retry an async callable on transient/rate-limit errors with exponential
    backoff + jitter. Permanent errors propagate immediately."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 — classified below
            kind = exc.kind if isinstance(exc, BotError) else classify_clob_error(exc)
            if kind not in (ErrorKind.TRANSIENT, ErrorKind.RATE_LIMITED):
                raise
            last = exc
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            log.warning("%s failed (%s, attempt %d/%d), retrying in %.1fs: %s",
                        label, kind.value, attempt + 1, attempts, delay, exc)
            await asyncio.sleep(delay)
    assert last is not None
    raise last
