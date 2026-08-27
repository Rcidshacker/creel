"""SQLite persistence: runs, attempts, fetch_cache, extract_cache — one file.

Kept in one database (not four) so run history, cost reporting, and both
caches are joinable without a second connection. Phase 1a defines the schema
and CRUD; Phase 1b/2 modules (cache.py logic, memory.py, learn.py) are the
callers that give these tables their policy (TTL choices, invalidation
rules, single-flight).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    ts REAL NOT NULL,
    engine_path TEXT NOT NULL,     -- JSON list
    status TEXT NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0,
    cost_exact INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    engine TEXT NOT NULL,
    started_at REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    failure_class TEXT,
    detail TEXT NOT NULL DEFAULT ''   -- MUST be pre-redacted by core.guard.redact
);

CREATE TABLE IF NOT EXISTS fetch_cache (
    cache_key TEXT PRIMARY KEY,       -- sha256(canonical_url|tier|accept_language|cookies|ua_profile)
    url TEXT NOT NULL,
    tier TEXT NOT NULL,
    status INTEGER,
    body BLOB,
    headers TEXT,                     -- JSON
    fetched_at REAL NOT NULL,
    ttl_s REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS extract_cache (
    cache_key TEXT PRIMARY KEY,       -- sha256(html_hash|prompt|model|params)
    data TEXT NOT NULL,               -- JSON
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_run ON attempts(run_id);
CREATE INDEX IF NOT EXISTS idx_fetch_cache_url ON fetch_cache(url);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- runs / attempts ------------------------------------------------

    def record_run(
        self,
        run_id: str,
        url: str,
        engine_path: list[str],
        status: str,
        cost_usd: float = 0.0,
        cost_exact: bool = False,
        ts: Optional[float] = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs (id, url, ts, engine_path, status, cost_usd, cost_exact) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                url,
                ts if ts is not None else time.time(),
                json.dumps(engine_path),
                status,
                cost_usd,
                int(cost_exact),
            ),
        )
        self._conn.commit()

    def record_attempt(
        self,
        run_id: str,
        engine: str,
        started_at: float,
        duration_ms: int,
        failure_class: Optional[str],
        detail: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO attempts (run_id, engine, started_at, duration_ms, failure_class, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, engine, started_at, duration_ms, failure_class, detail),
        )
        self._conn.commit()

    def attempts_for_run(self, run_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM attempts WHERE run_id = ? ORDER BY id", (run_id,))
        return cur.fetchall()

    # -- fetch cache ------------------------------------------------------

    def get_fetch(self, cache_key: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM fetch_cache WHERE cache_key = ?", (cache_key,))
        row = cur.fetchone()
        if row is None:
            return None
        if row["fetched_at"] + row["ttl_s"] < time.time():
            return None  # expired -> treat as miss; purge_expired() reclaims the row
        return row

    def put_fetch(
        self,
        cache_key: str,
        url: str,
        tier: str,
        status: Optional[int],
        body: bytes,
        headers: dict,
        ttl_s: float,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO fetch_cache "
            "(cache_key, url, tier, status, body, headers, fetched_at, ttl_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cache_key, url, tier, status, body, json.dumps(headers), time.time(), ttl_s),
        )
        self._conn.commit()

    def invalidate_fetch(self, cache_key: str) -> None:
        self._conn.execute("DELETE FROM fetch_cache WHERE cache_key = ?", (cache_key,))
        self._conn.commit()

    # -- extract cache ------------------------------------------------------

    def get_extract(self, cache_key: str) -> Optional[dict]:
        cur = self._conn.execute("SELECT data FROM extract_cache WHERE cache_key = ?", (cache_key,))
        row = cur.fetchone()
        return json.loads(row["data"]) if row else None

    def put_extract(self, cache_key: str, data: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO extract_cache (cache_key, data, created_at) VALUES (?, ?, ?)",
            (cache_key, json.dumps(data), time.time()),
        )
        self._conn.commit()

    def purge_expired(self) -> int:
        cur = self._conn.execute("DELETE FROM fetch_cache WHERE fetched_at + ttl_s < ?", (time.time(),))
        self._conn.commit()
        return cur.rowcount

    def vacuum(self) -> None:
        self._conn.execute("VACUUM")
