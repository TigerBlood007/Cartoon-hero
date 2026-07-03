"""SQLite persistence layer.

Six tables: traders, layer_one_signals, layer_two_executions,
layer_three_news_checks, bot_metadata, heartbeats. The bot process is the
only writer (WAL mode); the dashboard opens the file read-only.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS traders (
    wallet              TEXT NOT NULL,
    category            TEXT NOT NULL,
    lifetime_pnl        REAL DEFAULT 0,
    category_win_rate   REAL DEFAULT 0,
    category_trade_count INTEGER DEFAULT 0,
    last_trade_ts       REAL,
    conviction_bin      INTEGER,          -- 10/25/50/75/100 trade threshold
    active              INTEGER DEFAULT 1,
    updated_ts          REAL,
    PRIMARY KEY (wallet, category)
);

CREATE TABLE IF NOT EXISTS layer_one_signals (
    signal_id       TEXT PRIMARY KEY,     -- uuid: never collides with fills
    ts              REAL NOT NULL,
    source_wallet   TEXT NOT NULL,
    condition_id    TEXT NOT NULL,
    market_slug     TEXT,
    category        TEXT NOT NULL,
    outcome         TEXT NOT NULL,        -- YES / NO token side
    token_id        TEXT,
    size_usdc       REAL NOT NULL,
    price           REAL NOT NULL,
    confidence      REAL NOT NULL,
    detector_bot_id TEXT NOT NULL,
    source_trade_id TEXT,
    UNIQUE (source_wallet, source_trade_id)
);

CREATE TABLE IF NOT EXISTS layer_two_executions (
    execution_id    TEXT PRIMARY KEY,
    ts              REAL NOT NULL,
    signal_id       TEXT NOT NULL REFERENCES layer_one_signals(signal_id),
    layer           INTEGER NOT NULL DEFAULT 2,   -- 2 or 3 (L3 reuses schema)
    bot_id          TEXT NOT NULL,
    condition_id    TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    exec_price      REAL,
    size_usdc       REAL NOT NULL,
    filled_ts       REAL,
    filled_size     REAL DEFAULT 0,               -- partial-fill tracking
    status          TEXT NOT NULL,                -- dry_run/open/filled/partial/skipped/failed/resolved
    current_pnl     REAL DEFAULT 0,
    is_paper        INTEGER DEFAULT 0,            -- $5000 paper sim rows
    order_id        TEXT
);

CREATE TABLE IF NOT EXISTS layer_three_news_checks (
    check_id        TEXT PRIMARY KEY,
    signal_id       TEXT NOT NULL REFERENCES layer_one_signals(signal_id),
    ts              REAL NOT NULL,
    market_slug     TEXT,
    news_events     TEXT,                 -- JSON list of headline summaries
    catalyst_score  REAL NOT NULL,        -- 0 / 0.3 / 0.7
    recommendation  TEXT NOT NULL         -- execute / skip
);

CREATE TABLE IF NOT EXISTS bot_metadata (
    bot_id          TEXT PRIMARY KEY,
    layer           INTEGER NOT NULL,
    category        TEXT,
    conviction_bin  INTEGER,
    scale_factor    REAL,
    news_filter     INTEGER DEFAULT 0,
    source_wallet   TEXT,                 -- signal source this bot follows
    allocation_usdc REAL DEFAULT 1.0,
    cumulative_pnl  REAL DEFAULT 0,
    win_rate        REAL DEFAULT 0,
    trade_count     INTEGER DEFAULT 0,
    win_count       INTEGER DEFAULT 0,
    halted          INTEGER DEFAULT 0,    -- circuit breaker tripped
    halted_reason   TEXT,
    updated_ts      REAL
);

CREATE TABLE IF NOT EXISTS heartbeats (
    bot_id      TEXT PRIMARY KEY,
    layer       INTEGER NOT NULL,
    last_alive  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_ts ON layer_one_signals(ts);
CREATE INDEX IF NOT EXISTS idx_exec_ts ON layer_two_executions(ts);
CREATE INDEX IF NOT EXISTS idx_exec_bot ON layer_two_executions(bot_id, status);
CREATE INDEX IF NOT EXISTS idx_exec_signal ON layer_two_executions(signal_id);
"""


class Database:
    """Thread-safe writer handle (asyncio loop + heartbeat threads share it)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, tuple(params))
            self._conn.commit()

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ── Convenience helpers used across layers ──────────────────────────────

    def heartbeat(self, bot_id: str, layer: int) -> None:
        self.execute(
            "INSERT INTO heartbeats (bot_id, layer, last_alive) VALUES (?, ?, ?) "
            "ON CONFLICT(bot_id) DO UPDATE SET last_alive = excluded.last_alive",
            (bot_id, layer, time.time()),
        )

    def open_position_count(self, bot_id: str) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM layer_two_executions "
            "WHERE bot_id = ? AND status IN ('open', 'partial', 'filled', 'dry_run') "
            "AND is_paper = 0",
            (bot_id,),
        )
        return int(row["n"]) if row else 0

    def has_position_in_market(self, bot_id: str, condition_id: str) -> bool:
        row = self.query_one(
            "SELECT 1 FROM layer_two_executions WHERE bot_id = ? AND condition_id = ? "
            "AND status IN ('open', 'partial', 'filled', 'dry_run') AND is_paper = 0 LIMIT 1",
            (bot_id, condition_id),
        )
        return row is not None

    def layer_exposure(self, layer: int) -> float:
        row = self.query_one(
            "SELECT COALESCE(SUM(size_usdc), 0) AS total FROM layer_two_executions "
            "WHERE layer = ? AND status IN ('open', 'partial', 'filled', 'dry_run') "
            "AND is_paper = 0",
            (layer,),
        )
        return float(row["total"]) if row else 0.0

    def record_bot_result(self, bot_id: str, pnl_delta: float, won: bool | None) -> None:
        """Roll a resolved trade into the bot's cumulative stats."""
        win_inc = 1 if won else 0
        trade_inc = 0 if won is None else 1
        self.execute(
            "UPDATE bot_metadata SET "
            "cumulative_pnl = cumulative_pnl + ?, "
            "trade_count = trade_count + ?, "
            "win_count = win_count + ?, "
            "win_rate = CASE WHEN trade_count + ? > 0 "
            "  THEN CAST(win_count + ? AS REAL) / (trade_count + ?) ELSE 0 END, "
            "updated_ts = ? WHERE bot_id = ?",
            (pnl_delta, trade_inc, win_inc, trade_inc, win_inc, trade_inc,
             time.time(), bot_id),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
