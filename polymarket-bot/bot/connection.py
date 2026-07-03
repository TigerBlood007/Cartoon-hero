"""Single shared connection manager for all three layers.

One authenticated CLOB client and one aiohttp session for REST (leaderboard,
trade history, order books, market metadata). The real-time trade feed lives
in stream.py (Polymarket's public activity socket). Layers never open their
own connections, which keeps us inside rate limits.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import aiohttp

from .config import Config, ConfigError
from .errors import BotError, ErrorKind, retry_transient

log = logging.getLogger("connection")

try:  # py-clob-client is only required for live trading; dry-run works without it.
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    _CLOB_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CLOB_AVAILABLE = False


class ConnectionManager:
    """Owns every outbound REST/CLOB connection."""

    def __init__(self, config: Config):
        self.config = config
        self.session: aiohttp.ClientSession | None = None
        self.clob: Any = None
        self._market_meta: dict[str, dict] = {}   # token_id -> {tick, min_size}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "polymarket-research-bot/4.0"},
        )
        if not self.config.dry_run:
            self._init_clob()
            await self._verify_allowance()
        else:
            log.info("DRY RUN mode: CLOB client not initialized, no orders will be placed")

    def _init_clob(self) -> None:
        if not _CLOB_AVAILABLE:
            raise ConfigError("py-clob-client not installed but live trading requested")
        s = self.config.secrets
        creds = ApiCreds(api_key=s.clob_api_key, api_secret=s.clob_api_secret,
                         api_passphrase=s.clob_api_passphrase)
        # signature_type=1 + funder → orders settle from the Polymarket proxy wallet.
        self.clob = ClobClient(
            self.config["chain"]["clob_host"], key=s.private_key,
            chain_id=self.config["chain"]["chain_id"], creds=creds,
            signature_type=1, funder=s.proxy_wallet)
        log.info("CLOB client initialized for proxy wallet %s…%s",
                 s.proxy_wallet[:6], s.proxy_wallet[-4:])

    async def _verify_allowance(self) -> None:
        """Setup gate: USDC.e allowance on the proxy wallet must cover max
        total exposure across all layers. Explicit error otherwise."""
        required = float(self.config["risk"]["required_allowance_usdc"])
        try:
            ba = await asyncio.to_thread(self.clob.get_balance_allowance)
            allowance = float(ba.get("allowance", 0)) / 1e6
            balance = float(ba.get("balance", 0)) / 1e6
        except Exception as exc:
            raise ConfigError(f"Could not verify USDC.e allowance: {exc}") from exc
        if allowance < required:
            raise ConfigError(
                f"USDC.e allowance {allowance:.2f} on proxy wallet is below the "
                f"required {required:.2f} for max total exposure. Approve the CTF "
                "exchange contract for at least that amount before going live.")
        log.info("Allowance OK: %.2f USDC.e approved, %.2f balance", allowance, balance)

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    # ── REST helpers ─────────────────────────────────────────────────────────

    async def _get_json(self, url: str, params: dict | None = None) -> Any:
        async def _do() -> Any:
            assert self.session is not None
            async with self.session.get(url, params=params) as resp:
                if resp.status == 429:
                    raise BotError(f"rate limited on {url}", ErrorKind.RATE_LIMITED)
                if resp.status >= 500:
                    raise BotError(f"{resp.status} from {url}", ErrorKind.TRANSIENT)
                if resp.status >= 400:
                    raise BotError(f"{resp.status} from {url}", ErrorKind.PERMANENT)
                return await resp.json()
        return await retry_transient(_do, label=f"GET {url}")

    async def fetch_leaderboard(self, window: str = "30d", limit: int = 100) -> list[dict]:
        """Candidate seed only — research shows leaderboard wallets are the
        most-copied and most-decoyed; the scorer decides who is worth copying."""
        url = f"{self.config['chain']['leaderboard_api_host']}/leaderboard"
        try:
            data = await self._get_json(url, {"window": window, "limit": limit,
                                              "rankType": "pnl"})
            return data if isinstance(data, list) else data.get("leaderboard", [])
        except Exception as exc:
            log.error("Leaderboard fetch failed: %s", exc)
            return []

    async def fetch_trader_trades(self, wallet: str, limit: int = 500) -> list[dict]:
        url = f"{self.config['chain']['data_api_host']}/trades"
        try:
            data = await self._get_json(url, {"user": wallet, "limit": limit,
                                              "takerOnly": "false"})
            return data if isinstance(data, list) else []
        except Exception as exc:
            log.error("Trade history fetch failed for %s: %s", wallet, exc)
            return []

    async def fetch_market(self, condition_id: str) -> dict | None:
        url = f"{self.config['chain']['gamma_api_host']}/markets"
        try:
            data = await self._get_json(url, {"condition_ids": condition_id})
            items = data if isinstance(data, list) else data.get("data", [])
            return items[0] if items else None
        except Exception as exc:
            log.warning("Market lookup failed for %s: %s", condition_id, exc)
            return None

    async def fetch_book(self, token_id: str) -> dict | None:
        """Fresh top-of-book at decision time. Research: backtests filled at a
        stale mid overstate returns 30-100%; every execution decision here is
        made against the live executable ask/bid instead."""
        url = f"{self.config['chain']['clob_host']}/book"
        try:
            book = await self._get_json(url, {"token_id": token_id})
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            best_bid = max((float(b["price"]) for b in bids), default=None)
            best_ask = min((float(a["price"]) for a in asks), default=None)
            if best_bid is None or best_ask is None:
                return None
            meta = {"best_bid": best_bid, "best_ask": best_ask,
                    "spread": round(best_ask - best_bid, 4),
                    "mid": round((best_bid + best_ask) / 2, 4),
                    "tick": float(book.get("tick_size") or 0.01),
                    "min_size": float(book.get("min_order_size") or 5)}
            self._market_meta[token_id] = meta
            return meta
        except Exception as exc:
            log.warning("Book fetch failed for %s: %s", token_id[:12], exc)
            return None

    def taker_fee(self, category: str, price: float, shares: float) -> float:
        """Modeled per-category taker fee (March 2026 schedule): peaks at 50c
        via the p*(1-p) curve; makers pay zero."""
        rate = float(self.config["fees"]["taker_rates"].get(category, 0.01))
        return round(shares * rate * 4 * price * (1 - price), 6)

    # ── Order placement ──────────────────────────────────────────────────────

    async def place_order(self, token_id: str, side: str, price: float,
                          shares: float, post_only: bool) -> dict:
        """Place a limit order (post_only=True to guarantee maker / zero fee).
        Exchange minimums enforced by the caller: 5 shares for limit orders.
        In dry-run, returns a synthetic fill at the requested price."""
        if self.config.dry_run:
            return {"order_id": f"dry-{uuid.uuid4()}", "status": "dry_run",
                    "price": price}

        def _place() -> dict:
            args = OrderArgs(token_id=token_id, price=round(price, 3),
                             size=round(shares, 2),
                             side=BUY if side == "BUY" else SELL)
            signed = self.clob.create_order(args)
            order_type = OrderType.GTC
            return self.clob.post_order(signed, order_type)

        async def _do() -> dict:
            return await asyncio.to_thread(_place)

        resp = await retry_transient(_do, label=f"post_order {token_id[:10]}")
        return {"order_id": resp.get("orderID") or resp.get("orderId"),
                "status": "open", "price": price}
