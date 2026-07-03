# Polymarket Multi-Layer Copy-Trading Research Bot v4

A research system that runs **three parallel bot layers** to test and validate
copy-trading edges across Polymarket categories:

| Layer | Bots | Role |
|-------|------|------|
| **One** | 100 cells | Signal detection only — real-time stream matching against scored elite wallets per (category, conviction-bin) pair. Never trades. |
| **Two** | 100 bots | Adaptive execution — copies Layer One signals above 0.60 confidence, price-band-guarded, fee-aware taker/post-only routing, reweighted toward conviction bins with proven fee-adjusted ROI. |
| **Three** | 100 bots | News-aware variant — same as Layer Two but gates each signal on a news catalyst score, so you can compare insider-blind vs news-informed after two weeks. |

## Why v4 looks the way it does (research findings)

The v3 design had four flaws that field research on working copy-bots exposed:

1. **Polling was too slow to copy anything.** The data API lags trades by
   5–30+ seconds; public measurements put copyable edge half-life well under
   that. v4 detects trades on Polymarket's public **real-time activity socket**
   (`wss://ws-live-data.polymarket.com`, topic `activity/trades`) which pushes
   every platform trade — wallet, price, size, side — in under a second, no
   auth. The data API survives only as the scorer's history backfill.
2. **$1 maker orders were physically impossible.** The exchange requires **5
   shares minimum on limit orders** ($1 minimum on market orders), so "$1
   bots" would simply be rejected. Positions are now $5 nominal and sized up
   to the exchange minimum or skipped — explicitly, with a log line.
3. **Slippage is the failure mode, not an inconvenience.** You always buy
   behind the trader you copy — their own order moves the price, and MEV bots
   fill within milliseconds. v4 adds a **price-band guard** (skip if the live
   ask moved more than 2¢ past the source fill), decides against a **fresh
   top-of-book** (mid-fill backtests overstate returns 30–100%), and records
   **slippage + detection latency on every trade** — measuring edge decay is
   the actual research product.
4. **Win rate was the wrong scoreboard, and the leaderboard the wrong menu.**
   Buying at 90¢ "wins" 90% of the time with zero edge; leaderboards surface
   exactly the most-copied, most-decoyed wallets (top traders run decoy trades
   and split across wallets). v4 scores candidates on **fee-adjusted ROI over
   7/30/90-day windows**, **edge vs entry price** (Wilson lower bound of win
   rate minus average entry price), **profit concentration** (one lucky market
   ≠ skill), and **decoy heuristics** (rapid buy-sell flips, erratic sizing).
   Reallocation promotes only bots whose *pessimistic* estimate beats their
   own breakeven, and demotes only proven failures — no more noise-chasing.

Two additions the research demanded: **exits are copied** (when the source
wallet sells a market you copied from them, you sell — riding an abandoned
position is not a strategy), and **fees are first-class** — Polymarket's
March 2026 schedule charges takers up to 1.8% (crypto) peaking at 50¢ via a
p×(1−p) curve while makers pay zero, so wide-spread markets are entered
post-only and the fee-free **Geopolitics** category joins the test grid.
Sobering base rate to keep in mind: roughly **0.5% of all Polymarket wallets
have ever cleared $1,000 profit**, and only ~13% of users profit at all.

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

- **Signal volume**: 5–10 signals per Layer One cell per day (dashboard: “Layer 1
  signals / last hour”).
- **Detection latency**: the dashboard's “edge decay instruments” panel shows
  median stream detection latency — expect well under one second. Signals
  older than the 3s budget are skipped as stale.
- **Slippage**: average slippage vs source fill should sit inside the 2¢ band;
  if most signals are skipped on the price-band guard, the wallets you're
  tracking are too big or too crowded — the scorer should rotate them out.
- **Heartbeats**: all bots green (<60s) on the dashboard.
- Dry-run “executions” accumulate in the DB with `status='dry_run'` so you can
  later compare dry-run P&L to live fills and measure real execution quality.

## 6. Going live

1. Set `DRY_RUN=false` in `.env` **and** `runtime.dry_run: false` in
   `config.yaml`.
2. Start with $1 per bot (defaults). Run **two weeks minimum** to gather win
   rates per parameter set; compare Layer Two vs Layer Three by category on the
   dashboard.
3. The risk manager automatically: enforces one position per market and max 3
   concurrent positions per bot, caps Layer Two and Three at $100 exposure
   each, halts any bot below −$10 cumulative P&L (−200% of its $5 allocation),
   and every 10 trades / 12 hours shifts $1 from bots whose Wilson-bound win
   rate is provably below their own breakeven (entry price + fees) to bots
   provably above it.
4. Only scale up after **four straight weeks of fee-adjusted P&L above zero
   with the Wilson lower bound clearing breakeven** — raw win-rate streaks
   don't count.

## Layout

```
polymarket-bot/
├── main.py               # entry point: wires all layers together
├── dashboard.py          # read-only Flask dashboard (localhost:5000)
├── config.yaml           # tunables incl. fee table (no secrets)
├── .env.example          # secrets template
└── bot/
    ├── config.py         # env + yaml loading, live-mode safety gate
    ├── database.py       # SQLite schema (6 tables incl. heartbeats)
    ├── connection.py     # single CLOB client, books, fees, orders
    ├── stream.py         # real-time activity socket (primary detection)
    ├── scoring.py        # fee-adjusted trader scoring + Wilson bounds
    ├── layer_one.py      # stream-driven signal detection (100 cells)
    ├── execution.py      # L2 engine: guards, routing, exits, paper sim
    ├── layer_three.py    # news-aware variant
    ├── news.py           # catalyst scoring with 1h cache
    ├── risk.py           # circuit breaker + breakeven-aware reallocation
    ├── errors.py         # transient/permanent taxonomy, retry backoff
    └── logging_setup.py  # file + stdout logging
```

## Edge cases handled

- **Market closes/resolves mid-order** → logged as market-closed, never retried.
- **Partial fills** → tracked via `filled_size`, never double-copied.
- **Silent WebSocket drops** → 5-minute data timeout forces reconnect (logged);
  the stream also answers server keepalives (literal `ping` every 5s).
- **Trader goes inactive (24h)** → flagged inactive, removed from Layer One sources.
- **Decoy/bait wallets** → scoring penalizes rapid buy-sell flips and single
  bets 25× the wallet's median size; leaderboard rank is never a selection input.
- **Exchange minimums** → 5-share limit-order minimum enforced at sizing time;
  orders that can't clear it within budget are skipped, not bounced.
- **Signal/fill race conditions** → UUID signal & execution IDs plus a unique
  `(source_wallet, source_trade_id)` constraint keep Layer Two from confusing
  its own fills with Layer One signals.
- **News API rate limits** → 1-hour per-slug cache; 429s degrade to score 0.

## Research sources

Polymarket docs & clients: [real-time data client](https://github.com/Polymarket/real-time-data-client),
[orderbook & minimums](https://docs.polymarket.com/trading/orderbook),
[fee schedule](https://docs.polymarket.com/trading/fees),
[maker rebates](https://help.polymarket.com/en/articles/13364471-maker-rebates-program).
Failure-mode analyses: [Start Polymarket on copy-trading slippage/manipulation](https://startpolymarket.com/strategies/copy-trading/),
[QuantVPS on latency](https://www.quantvps.com/blog/how-latency-impacts-polymarket-trading-performance),
[QuantVPS on copy bots](https://www.quantvps.com/blog/polymarket-copy-trading-bot),
[fee breakdown](https://startpolymarket.com/learn/polymarket-fees/),
[Market Math fee formula](https://marketmath.io/blog/polymarket-fees-explained).
Wallet-selection practice: [Ratio on finding wallets](https://ratio.you/blog/how-to-find-best-polymarket-wallets-copy-trade),
[Laika Labs wallet tracking](https://laikalabs.ai/prediction-markets/how-to-track-polymarket-wallets),
[Quicknode copy-bot guide](https://www.quicknode.com/guides/defi/polymarket-copy-trading-bot).

## Disclaimer

Research tooling for your own account at micro-stakes. Prediction-market
trading carries real risk of loss; copy-trading past performance guarantees
nothing. Review your jurisdiction's rules before trading.
