import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from abs_mcp.cache import Cache


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.db"


async def test_set_then_get_within_ttl(db_path: Path) -> None:
    cache = Cache(db_path)
    await cache.set("k1", b"hello", kind="data")
    payload = await cache.get("k1", ttl=timedelta(hours=1))
    assert payload == b"hello"


async def test_get_after_ttl_returns_none(db_path: Path) -> None:
    cache = Cache(db_path)
    await cache.set("k1", b"hello", kind="data")
    # Negative TTL means "must have been written in the future" — guaranteed miss.
    payload = await cache.get("k1", ttl=timedelta(seconds=-1))
    assert payload is None


async def test_persists_across_instances(db_path: Path) -> None:
    cache1 = Cache(db_path)
    await cache1.set("k1", b"persisted", kind="catalogue")
    cache2 = Cache(db_path)
    payload = await cache2.get("k1", ttl=timedelta(hours=24))
    assert payload == b"persisted"


async def test_upsert_replaces_value(db_path: Path) -> None:
    cache = Cache(db_path)
    await cache.set("k1", b"v1", kind="data")
    await cache.set("k1", b"v2", kind="data")
    assert await cache.get("k1", ttl=timedelta(hours=1)) == b"v2"


async def test_clear_by_kind(db_path: Path) -> None:
    cache = Cache(db_path)
    await cache.set("k1", b"a", kind="catalogue")
    await cache.set("k2", b"b", kind="data")
    await cache.clear(kind="data")
    assert await cache.get("k1", ttl=timedelta(hours=24)) == b"a"
    assert await cache.get("k2", ttl=timedelta(hours=24)) is None
