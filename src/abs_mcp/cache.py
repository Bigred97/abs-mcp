"""SQLite-backed HTTP cache with per-read TTL.

Single table; the same row can satisfy different TTL windows because TTL is
evaluated at read time. The `kind` column lets us run targeted invalidation
later without renaming.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from typing import Literal

import aiosqlite

CacheKind = Literal["catalogue", "datastructure", "data", "latest", "calendar"]

DEFAULT_DB_PATH = Path.home() / ".abs-mcp" / "cache.db"

TTL: dict[CacheKind, timedelta] = {
    "catalogue": timedelta(hours=24),
    "datastructure": timedelta(days=7),
    # 2h "latest" matches the cadence at which ABS actually re-publishes
    # series. ABS releases happen at 11:30 AEST on weekdays; between
    # publish boundaries the latest observation doesn't change, so the
    # old 15-min TTL was burning network for no freshness gain. The
    # release_calendar tool (0.11.0) gives the gateway a precise way to
    # invalidate after a published embargo instead of polling.
    "data": timedelta(hours=1),
    "latest": timedelta(hours=2),
    # ABS shifts published release dates a few times a year; 24h is the right
    # cadence for the release-calendar scrape — fresh enough to catch a
    # rescheduled embargo, cheap enough that the gateway's 5-min polling
    # never hits the live HTML.
    "calendar": timedelta(hours=24),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    cache_key  TEXT PRIMARY KEY,
    payload    BLOB NOT NULL,
    cached_at  REAL NOT NULL,
    kind       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kind_cached_at ON http_cache(kind, cached_at);
"""


class Cache:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            # Re-check under the lock: another waiter may have initialised
            # while we were queued.
            if self._initialized:
                return
            try:
                await self._init_schema()
            except sqlite3.DatabaseError:
                # Pre-existing cache.db is corrupt or has an incompatible
                # schema (e.g. left over from an older version, partial
                # write after a crash, or user accident). The cache is a
                # performance optimisation, not a source of truth — dropping
                # and recreating it is always safe. Mirrors the rba-mcp
                # 0.1.2 fix; gate 4 says raw library exceptions must not
                # leak to the MCP tool surface.
                self.db_path.unlink(missing_ok=True)
                await self._init_schema()
            self._initialized = True

    async def _init_schema(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.executescript(_SCHEMA)
            await conn.commit()

    async def get(self, key: str, ttl: timedelta) -> bytes | None:
        await self._ensure_init()
        cutoff = time.time() - ttl.total_seconds()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT payload FROM http_cache WHERE cache_key = ? AND cached_at >= ?",
                (key, cutoff),
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else None

    async def get_stale(self, key: str) -> tuple[bytes, float] | None:
        """Return cached (payload, cached_at_epoch) regardless of TTL.

        Used by the client as a fallback when the upstream source is
        unavailable — graceful degradation per CLAUDE.md quality dimension
        #4. The caller computes "how stale" from the timestamp and surfaces
        it in `DataResponse.stale_reason`.
        """
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT payload, cached_at FROM http_cache WHERE cache_key = ?",
                (key,),
            ) as cur:
                row = await cur.fetchone()
        return (row[0], row[1]) if row else None

    async def set(self, key: str, value: bytes, kind: CacheKind) -> None:
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO http_cache (cache_key, payload, cached_at, kind)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    cached_at = excluded.cached_at,
                    kind = excluded.kind
                """,
                (key, value, time.time(), kind),
            )
            await conn.commit()

    async def clear(self, kind: CacheKind | None = None) -> None:
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as conn:
            if kind:
                await conn.execute("DELETE FROM http_cache WHERE kind = ?", (kind,))
            else:
                await conn.execute("DELETE FROM http_cache")
            await conn.commit()
