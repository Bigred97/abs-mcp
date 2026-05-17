from pathlib import Path

import httpx
import pytest

from abs_mcp.cache import Cache
from abs_mcp.client import ABSAPIError, ABSClient


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.db"


def _mock_transport(responses: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in responses:
            return responses[url]
        return httpx.Response(404, text=f"No mock for {url}")

    return httpx.MockTransport(handler)


async def test_get_dataflows_caches(db_path: Path) -> None:
    """First call hits HTTP; second call hits cache."""
    fixture = (Path(__file__).parent / "fixtures" / "dataflows_min.xml").read_bytes()
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, content=fixture)

    transport = httpx.MockTransport(handler)
    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=transport) as client:
        msg1 = await client.get_dataflows()
        msg2 = await client.get_dataflows()
    assert call_count["n"] == 1, "second call should hit the cache"
    # Both messages should parse to a StructureMessage with dataflow content
    assert hasattr(msg1, "dataflow")
    assert hasattr(msg2, "dataflow")


async def test_4xx_raises_abs_api_error(db_path: Path) -> None:
    transport = _mock_transport({})  # all 404
    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=transport) as client:
        with pytest.raises(ABSAPIError):
            await client.get_datastructure("DOES_NOT_EXIST")


async def test_parse_error_wraps_as_abs_api_error(db_path: Path) -> None:
    """If ABS returns a 200 with a malformed body (HTML error page slipping
    through, schema drift, truncated XML), sdmx.read_sdmx raises a library
    exception. That used to escape the ABSAPIError contract and crash the
    server tool with an unstructured error."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"<html>this is not sdmx</html>")
    )
    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=transport) as client:
        with pytest.raises(ABSAPIError, match="parse SDMX response"):
            await client.get_dataflows()


async def test_get_data_url_includes_filters(db_path: Path) -> None:
    fixture = (Path(__file__).parent / "fixtures" / "lf_one_obs.xml").read_bytes()
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["accept"] = request.headers.get("accept", "")
        return httpx.Response(200, content=fixture)

    transport = httpx.MockTransport(handler)
    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=transport) as client:
        await client.get_data(
            "LF",
            key="M13.3.1599.20.1.M",
            start_period="2024",
            last_n=1,
        )
    assert "data/ABS,LF/M13.3.1599.20.1.M" in captured["url"]
    assert "startPeriod=2024" in captured["url"]
    assert "lastNObservations=1" in captured["url"]
    assert "genericdata+xml" in captured["accept"]


async def test_get_data_rejects_non_positive_last_n(db_path: Path) -> None:
    """0.2.10 audit finding: `if last_n:` used to silently drop `last_n=0`
    and negative values via the falsy check, returning a full fetch instead
    of an error. The defensive guard now raises ValueError. Internal-only
    today (latest hardcodes last_n=1), but the latent footgun is sealed."""
    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=_mock_transport({})) as client:
        with pytest.raises(ValueError, match="last_n must be a positive integer"):
            await client.get_data("LF", last_n=0)
        with pytest.raises(ValueError, match="last_n must be a positive integer"):
            await client.get_data("LF", last_n=-5)


# ─── stale-fallback graceful degradation (CLAUDE.md quality dim #4) ──────

async def _prime_stale_cache(db_path: Path, url: str, payload: bytes, age_hours: float) -> None:
    """Put `payload` into the cache as if it was fetched `age_hours` ago.
    Used to test the stale-fallback path: a regular cache.get() with a normal
    TTL will miss this row (because cached_at is older than the TTL window),
    but cache.get_stale() will still return it.
    """
    import time
    import aiosqlite
    from abs_mcp.cache import Cache
    cache = Cache(db_path)
    await cache._ensure_init()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO http_cache (cache_key, payload, cached_at, kind) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET "
            "payload=excluded.payload, cached_at=excluded.cached_at",
            (url, payload, time.time() - age_hours * 3600, "catalogue"),
        )
        await conn.commit()


async def test_stale_fallback_serves_cached_payload_on_5xx(db_path: Path) -> None:
    """When upstream ABS returns 5xx and we have a cached payload past its
    TTL, serve the cached payload and mark the response as stale. Agents
    continue reasoning rather than crashing."""
    from abs_mcp.client import get_stale_signal, reset_stale_signal

    fixture = (Path(__file__).parent / "fixtures" / "dataflows_min.xml").read_bytes()
    url = "https://data.api.abs.gov.au/rest/dataflow/ABS/all/latest"

    # Prime a 48h-old cache entry — past the 24h catalogue TTL, so cache.get()
    # misses but cache.get_stale() will still return it.
    await _prime_stale_cache(db_path, url, fixture, age_hours=48)

    reset_stale_signal()
    cache = Cache(db_path)
    async with ABSClient(
        cache=cache,
        transport=httpx.MockTransport(lambda req: httpx.Response(503, text="Service Unavailable")),
    ) as client:
        msg = await client.get_dataflows()
        assert hasattr(msg, "dataflow"), "fallback payload must still parse"
        stale, reason = get_stale_signal()
        assert stale is True, "stale flag must be set after 5xx fallback"
        assert reason and "503" in reason, f"stale_reason should mention the 5xx: {reason}"
        assert "minute" in reason.lower(), f"stale_reason should report age: {reason}"


async def test_stale_fallback_serves_cached_on_request_error(db_path: Path) -> None:
    """Same as 5xx test but for httpx.RequestError (DNS / connection refused / etc.)."""
    from abs_mcp.client import get_stale_signal, reset_stale_signal

    fixture = (Path(__file__).parent / "fixtures" / "dataflows_min.xml").read_bytes()
    url = "https://data.api.abs.gov.au/rest/dataflow/ABS/all/latest"
    await _prime_stale_cache(db_path, url, fixture, age_hours=48)

    reset_stale_signal()
    def raise_request_error(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated DNS failure")

    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=httpx.MockTransport(raise_request_error)) as client:
        msg = await client.get_dataflows()
        assert hasattr(msg, "dataflow")
        stale, reason = get_stale_signal()
        assert stale is True
        assert reason and "ConnectError" in reason


async def test_raises_when_no_stale_cache_to_fall_back_to(db_path: Path) -> None:
    """Empty cache + upstream 5xx → still raises ABSAPIError (original behaviour
    when there's nothing to gracefully degrade to)."""
    from abs_mcp.client import reset_stale_signal

    reset_stale_signal()
    cache = Cache(db_path)
    async with ABSClient(
        cache=cache,
        transport=httpx.MockTransport(lambda req: httpx.Response(503, text="Service Unavailable")),
    ) as client:
        with pytest.raises(ABSAPIError, match="503"):
            await client.get_dataflows()


async def test_cache_get_stale_returns_payload_and_timestamp(db_path: Path) -> None:
    """Cache.get_stale() returns (payload, cached_at) regardless of TTL —
    the building block for client's stale-fallback path."""
    from datetime import timedelta

    cache = Cache(db_path)
    await cache.set("https://example.org/x", b"hello", kind="data")
    # Normal `get` with a tiny TTL should miss
    fresh = await cache.get("https://example.org/x", ttl=timedelta(seconds=0))
    assert fresh is None
    # `get_stale` should return regardless of TTL
    stale = await cache.get_stale("https://example.org/x")
    assert stale is not None
    payload, cached_at = stale
    assert payload == b"hello"
    assert cached_at > 0
    # Non-existent key → None
    miss = await cache.get_stale("https://example.org/missing")
    assert miss is None


async def test_parsed_cache_skips_re_parse_on_warm_hit(db_path: Path) -> None:
    """In-process parsed-message LRU: byte cache hit STILL re-parses without
    the parsed-cache; first this test confirms the cache works, then that
    a second call doesn't even re-decompress / re-parse."""
    fixture = (Path(__file__).parent / "fixtures" / "dataflows_min.xml").read_bytes()
    parse_count = {"n": 0}

    real_read_sdmx = __import__("sdmx").read_sdmx

    def counting_read_sdmx(*args, **kwargs):  # noqa: ANN001, ANN201
        parse_count["n"] += 1
        return real_read_sdmx(*args, **kwargs)

    cache = Cache(db_path)
    async with ABSClient(
        cache=cache,
        transport=_mock_transport({
            "https://data.api.abs.gov.au/rest/dataflow/ABS/all/latest": httpx.Response(
                200, content=fixture, headers={"content-type": "application/xml"},
            )
        }),
    ) as client:
        # Monkey-patch the sync read_sdmx the client uses via asyncio.to_thread
        import abs_mcp.client as client_mod

        original = client_mod.sdmx.read_sdmx
        client_mod.sdmx.read_sdmx = counting_read_sdmx
        try:
            r1 = await client.get_dataflows()
            r2 = await client.get_dataflows()
            r3 = await client.get_dataflows()
        finally:
            client_mod.sdmx.read_sdmx = original

    # Three calls but only one parse — the LRU short-circuited calls 2+3.
    assert parse_count["n"] == 1
    # Returned objects are the same in-memory reference (LRU hands out the
    # cached instance directly, no re-parse).
    assert r1 is r2 is r3


async def test_parsed_cache_invalidates_per_url(db_path: Path) -> None:
    """Different URLs get separate parsed-cache slots."""
    fixture = (Path(__file__).parent / "fixtures" / "dataflows_min.xml").read_bytes()
    parse_count = {"n": 0}
    real_read_sdmx = __import__("sdmx").read_sdmx

    def counting(*args, **kwargs):  # noqa: ANN001, ANN201
        parse_count["n"] += 1
        return real_read_sdmx(*args, **kwargs)

    cache = Cache(db_path)
    async with ABSClient(
        cache=cache,
        transport=_mock_transport({
            "https://data.api.abs.gov.au/rest/dataflow/ABS/all/latest": httpx.Response(
                200, content=fixture, headers={"content-type": "application/xml"},
            ),
            "https://data.api.abs.gov.au/rest/datastructure/ABS/LF?references=all": httpx.Response(
                200, content=fixture, headers={"content-type": "application/xml"},
            ),
        }),
    ) as client:
        import abs_mcp.client as client_mod

        original = client_mod.sdmx.read_sdmx
        client_mod.sdmx.read_sdmx = counting
        try:
            await client.get_dataflows()
            await client.get_datastructure("LF")
        finally:
            client_mod.sdmx.read_sdmx = original

    # Two distinct URLs → two parses, no collision.
    assert parse_count["n"] == 2


async def test_reset_parsed_cache_for_tests_clears_lru(db_path: Path) -> None:
    """Test helper drops cached entries (tests that expect cold parses)."""
    fixture = (Path(__file__).parent / "fixtures" / "dataflows_min.xml").read_bytes()
    parse_count = {"n": 0}
    real_read_sdmx = __import__("sdmx").read_sdmx

    def counting(*args, **kwargs):  # noqa: ANN001, ANN201
        parse_count["n"] += 1
        return real_read_sdmx(*args, **kwargs)

    cache = Cache(db_path)
    async with ABSClient(
        cache=cache,
        transport=_mock_transport({
            "https://data.api.abs.gov.au/rest/dataflow/ABS/all/latest": httpx.Response(
                200, content=fixture, headers={"content-type": "application/xml"},
            )
        }),
    ) as client:
        import abs_mcp.client as client_mod

        original = client_mod.sdmx.read_sdmx
        client_mod.sdmx.read_sdmx = counting
        try:
            await client.get_dataflows()
            await client.get_dataflows()  # cache hit
            client.reset_parsed_cache_for_tests()
            await client.get_dataflows()  # second cold parse after reset
        finally:
            client_mod.sdmx.read_sdmx = original

    assert parse_count["n"] == 2
