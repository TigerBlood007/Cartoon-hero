"""Single shared connection manager for all three layers.

One authenticated CLOB client, one aiohttp session for REST polling
(leaderboard / trade history), and one WebSocket subscription multiplexed
across every tracked market — the layers never open their own connections,
which keeps us inside Polymarket rate limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable

import aiohttp

from .config import Config, ConfigError
from .errors import BotError, ErrorKind, retry_transient

log = logging.getLogger("connection")

try:  # py-clob-client is only required for live trading; dry-run works without it.
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY
    _CLOB_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CLOB_AVAILABLE = False


class ConnectionManager:
    """Owns every outbound connection. Layers call through this object only."""

    def __init__(self, config: Config):
        self.config = config
        self.session: aiohttp.ClientSession | None = None
        self.clob: Any = None
        self._ws_task: asyncio.Task | None = None
        self._ws_subscribed_assets: set[str] = set()
        self._ws_callbacks: list[Callable[[dict], None]] = []
        self._last_ws_message_ts = time.time()
        self._mid_prices: dict[str, float] = {}   # token_id -> last known mid
        self._closed = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "polymarket-research-bot/3.0"},
        )
        if not self.config.dry_run:
            self._init_clob()
            await self._verify_allowance()
        else:
            log.info("DRY RUN mode: CLOB client not initialized, no orders will be placed")
        self._ws_task = asyncio.create_task(self._ws_loop(), name="ws-market-feed")

    def _init_clob(self) -> None:
        if not _CLOB_AVAILABLE:
            raise ConfigError("py-clob-client not installed but live trading requested")
        s = self.config.secrets
        creds = ApiCreds(
            api_key=s.clob_api_key,
            api_secret=s.clob_api_secret,
            api_passphrase=s.clob_api_passphrase,
        )
        # signature_type=1 + funder → orders settle from the Polymarket proxy wallet.
        self.clob = ClobClient(
            self.config["chain"]["clob_host"],
            key=s.private_key,
            chain_id=self.config["chain"]["chain_id"],
            creds=creds,
            signature_type=1,
            funder=s.proxy_wallet,
        )
        log.info("CLOB client initialized for proxy wallet %s…%s",
                 s.proxy_wallet[:6], s.proxy_wallet[-4:])

    async def _verify_allowance(self) -> None:
        """Setup gate: USDC.e allowance on the proxy wallet must cover max
        total exposure across all layers (~$300). Explicit error otherwise."""
        required = float(self.config["risk"]["required_allowance_usdc"])
        try:
            balance_allowance = await asyncio.to_thread(
                self.clob.get_balance_allowance
            )
            allowance = float(balance_allowance.get("allowance", 0)) / 1e6
            balance = float(balance_allowance.get("balance", 0)) / 1e6
        except Exception as exc:
            raise ConfigError(f"Could not verify USDC.e allowance: {exc}") from exc
        if allowance < required:
            raise ConfigError(
                f"USDC.e allowance {allowance:.2f} on proxy wallet is below the "
                f"required {required:.2f} for max total exposure. Approve the CTF "
                "exchange contract for at least that amount before going live."
            )
        log.info("Allowance OK: %.2f USDC.e approved, %.2f balance", allowance, balance)

    async def close(self) -> None:
        self._closed = True
        if self._ws_task:
            self._ws_task.cancel()
        if self.session:
            await self.session.close()

    # ── REST: leaderboard + trade history (Polymarket data API) ─────────────

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
        url = f"{self.config['chain']['leaderboard_api_host']}/leaderboard"
        try:
            data = await self._get_json(url, {"window": window, "limit": limit,
                                              "rankType": "pnl"})
            return data if isinstance(data, list) else data.get("leaderboard", [])
        except Exception as exc:
            log.error("Leaderboard fetch failed: %s", exc)
            return []

    async def fetch_trader_trades(self, wallet: str, limit: int = 100) -> list[dict]:
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

    # ── WebSocket: one multiplexed market subscription ───────────────────────

    def add_ws_callback(self, cb: Callable[[dict], None]) -> None:
        self._ws_callbacks.append(cb)

    def subscribe_assets(self, token_ids: set[str]) -> None:
        """Add tokens to the multiplexed subscription (applied on next (re)connect
        cycle; the ws loop reconnects when the set changes)."""
        new = token_ids - self._ws_subscribed_assets
        if new:
            self._ws_subscribed_assets |= new
            log.info("WS subscription set grew to %d assets", len(self._ws_subscribed_assets))

    def mid_price(self, token_id: str) -> float | None:
        return self._mid_prices.get(token_id)

    async def _ws_loop(self) -> None:
        """Maintain the single market WebSocket. Detect silent drops via a
        5-minute data timeout and reconnect with backoff, logging every
        reconnection."""
        import websockets

        url = self.config["chain"]["ws_market_url"]
        silence_timeout = self.config["runtime"]["ws_silence_timeout_seconds"]
        backoff = 1.0
        while not self._closed:
            if not self._ws_subscribed_assets:
                await asyncio.sleep(5)
                continue
            subscribed_snapshot = set(self._ws_subscribed_assets)
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    await ws.send(json.dumps({
                        "type": "market",
                        "assets_ids": sorted(subscribed_snapshot),
                    }))
                    log.info("WS connected, %d assets subscribed", len(subscribed_snapshot))
                    backoff = 1.0
                    self._last_ws_message_ts = time.time()
                    while not self._closed:
                        if subscribed_snapshot != self._ws_subscribed_assets:
                            log.info("WS asset set changed, reconnecting to resubscribe")
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            if time.time() - self._last_ws_message_ts > silence_timeout:
                                raise BotError("WS silent for > timeout",
                                               ErrorKind.TRANSIENT)
                            continue
                        self._last_ws_message_ts = time.time()
                        self._handle_ws_message(raw)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                log.error("WS dropped (%s); reconnecting in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _handle_ws_message(self, raw: str) -> None:
        try:
            messages = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(messages, dict):
            messages = [messages]
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("event_type") == "book":
                token = msg.get("asset_id")
                bids = msg.get("bids") or msg.get("buys") or []
                asks = msg.get("asks") or msg.get("sells") or []
                if token and bids and asks:
                    try:
                        best_bid = max(float(b["price"]) for b in bids)
                        best_ask = min(float(a["price"]) for a in asks)
                        self._mid_prices[token] = round((best_bid + best_ask) / 2, 4)
                    except (KeyError, ValueError, TypeError):
                        pass
            for cb in self._ws_callbacks:
                try:
                    cb(msg)
                except Exception:  # noqa: BLE001
                    log.exception("WS callback error")

    # ── Order placement (Layer Two / Three call this) ────────────────────────

    async def place_maker_order(self, token_id: str, price: float,
                                size_usdc: float) -> dict:
        """Place a BUY limit order at the given (mid) price so we rest as maker.
        Returns {order_id, status}. In dry-run, returns a synthetic fill."""
        if self.config.dry_run:
            return {"order_id": f"dry-{uuid.uuid4()}", "status": "dry_run",
                    "price": price}

        def _place() -> dict:
            args = OrderArgs(
                token_id=token_id,
                price=round(price, 3),
                size=round(size_usdc / max(price, 0.001), 2),
                side=BUY,
            )
            signed = self.clob.create_order(args)
            return self.clob.post_order(signed, OrderType.GTC)

        async def _do() -> dict:
            return await asyncio.to_thread(_place)

        resp = await retry_transient(_do, label=f"post_order {token_id[:10]}")
        return {"order_id": resp.get("orderID") or resp.get("orderId"),
                "status": "open", "price": price}
