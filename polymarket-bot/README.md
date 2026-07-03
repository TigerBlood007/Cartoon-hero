# Polymarket Multi-Layer Copy-Trading Research Bot v3

A research system that runs **three parallel bot layers** to test and validate
copy-trading edges across Polymarket categories:

| Layer | Bots | Role |
|-------|------|------|
| **One** | 100 × $1 | Signal detection only — monitors elite traders per (category, conviction-bin) pair and logs confidence-scored signals. Never trades. |
| **Two** | 100 × $1 | Adaptive execution — copies Layer One signals above 0.60 confidence at 1% scale, maker orders at mid-price, dynamically reweighted toward winning conviction bins. |
| **Three** | 100 × $1 | News-aware variant — same as Layer Two but gates each signal on a news catalyst score, so you can compare insider-blind vs news-informed after two weeks. |

Everything persists to a single SQLite database; a read-only Flask dashboard
serves live status on `localhost:5000`.

> **Safety default:** all layers start in **dry-run** mode. Nothing touches the
> CLOB API until you set `DRY_RUN=false` in `.env` **and** `runtime.dry_run:
> false` in `config.yaml` — both switches must be off to go live.

## 1. Install

```bash
cd polymarket-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env
```

## 2. Generate CLOB API credentials

1. You need a Polygon wallet funded with USDC.e and used with Polymarket at
   least once (so the proxy wallet exists).
2. Derive L2 API credentials from your private key:

```python
from py_clob_client.client import ClobClient
client = ClobClient("https://clob.polymarket.com", key="0xYOUR_PRIVATE_KEY", chain_id=137)
print(client.create_or_derive_api_creds())   # api key / secret / passphrase
```

3. Put the key, secret, and passphrase into `.env` along with your private key
   and the **proxy wallet address** (visible in your Polymarket profile URL).

## 3. Token allowances

Orders settle from your Polymarket **proxy wallet**, which must have approved
the CTF Exchange contract to spend USDC.e. If you have ever traded through the
Polymarket UI this approval usually exists. On startup in live mode the bot
verifies the allowance covers `risk.required_allowance_usdc` (default **$300**
≈ max total exposure across all three layers) and raises an explicit setup
error if it doesn't. To approve manually, use the Polymarket UI (any small
trade sets approvals) or call `approve` on the USDC.e contract
(`0x2791…4174`) for the exchange spender from your proxy.

## 4. News API signup (Layer Three)

Create a free account at <https://newsapi.org>, copy the API key into
`NEWS_API_KEY` in `.env`. The free tier is sufficient: news checks are cached
**one hour per market slug**, so Layer Three stays far under the rate limit.
Without a key, Layer Three still runs but every catalyst score is 0 (i.e. it
behaves like a stricter Layer Two).

## 5. Dry-run validation (do this before real capital)

```bash
python main.py          # terminal 1 — bot, dry-run
python dashboard.py     # terminal 2 — http://localhost:5000
```

Run **one week** in dry-run and verify:

- **Signal volume**: 5–10 signals per Layer One bot per day (dashboard: “Layer 1
  signals / last hour”).
- **Latency**: Layer Two/Three log lines show signal→execution under one
  second; anything older than the 1s latency budget is skipped as stale.
- **Heartbeats**: all bots green (<60s) on the dashboard.
- Dry-run “executions” accumulate in the DB with `status='dry_run'` so you can
  later compare dry-run P&L to live fills and measure slippage.

## 6. Going live

1. Set `DRY_RUN=false` in `.env` **and** `runtime.dry_run: false` in
   `config.yaml`.
2. Start with $1 per bot (defaults). Run **two weeks minimum** to gather win
   rates per parameter set; compare Layer Two vs Layer Three by category on the
   dashboard.
3. The risk manager automatically: enforces one position per market and max 3
   concurrent positions per bot, caps Layer Two and Three at $100 exposure
   each, halts any bot below −$2 cumulative P&L (circuit breaker), and every
   10 trades / 12 hours shifts $1 from bots under 45% win rate to bots over
   55% (10+ trades).
4. Only scale beyond $1/bot after **four straight weeks above 55% win rate**.

## Layout

```
polymarket-bot/
├── main.py               # entry point: wires all layers together
├── dashboard.py          # read-only Flask dashboard (localhost:5000)
├── config.yaml           # tunables (no secrets)
├── .env.example          # secrets template
└── bot/
    ├── config.py         # env + yaml loading, live-mode safety gate
    ├── database.py       # SQLite schema (6 tables incl. heartbeats)
    ├── connection.py     # single CLOB client + multiplexed WebSocket
    ├── layer_one.py      # signal detection (100 bots)
    ├── execution.py      # Layer Two engine + $5000 paper simulation
    ├── layer_three.py    # news-aware variant
    ├── news.py           # catalyst scoring with 1h cache
    ├── risk.py           # circuit breaker + reallocation
    ├── errors.py         # transient/permanent taxonomy, retry backoff
    └── logging_setup.py  # file + stdout logging
```

## Edge cases handled

- **Market closes/resolves mid-order** → logged as market-closed, never retried.
- **Partial fills** → tracked via `filled_size`, never double-copied.
- **Silent WebSocket drops** → 5-minute data timeout forces reconnect (logged).
- **Trader goes inactive (24h)** → flagged inactive, removed from Layer One sources.
- **Signal/fill race conditions** → UUID signal & execution IDs plus a unique
  `(source_wallet, source_trade_id)` constraint keep Layer Two from confusing
  its own fills with Layer One signals.
- **News API rate limits** → 1-hour per-slug cache; 429s degrade to score 0.

## Disclaimer

Research tooling for your own account at micro-stakes. Prediction-market
trading carries real risk of loss; copy-trading past performance guarantees
nothing. Review your jurisdiction's rules before trading.
