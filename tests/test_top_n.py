"""Tests for the top_n convenience tool.

top_n ranks rows by a measure and returns the top (or bottom) N. It's the
most common agent workflow — "show me the top 5 X by Y" — collapsed into
a single server-side call. For abs-mcp it runs over the most-recent
period (lastNObservations=1 under the hood) so the rank is a clean
"top N entities at the latest period" view, not a noisy historical-high
mix.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from abs_mcp import server
from abs_mcp.cache import Cache
from abs_mcp.client import ABSClient

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_for(url: str) -> bytes:
    """Map a request URL to the bundled fixture bytes."""
    if "/datastructure/ABS/LF" in url:
        return (FIXTURES / "lf_dsd.xml").read_bytes()
    if "/data/ABS,LF/" in url:
        return (FIXTURES / "lf_one_obs.xml").read_bytes()
    raise AssertionError(f"no fixture for {url}")


def _handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=_fixture_for(str(request.url)))


@pytest.fixture
async def mocked_client(tmp_path: Path, monkeypatch):
    """Install an ABSClient backed by httpx.MockTransport + a per-test cache.

    Mirrors the pattern in test_integration.py but with a MockTransport so the
    suite runs offline. Replaces server._get_client so the FastMCP tool wrappers
    use our instance.
    """
    transport = httpx.MockTransport(_handler)
    client = ABSClient(cache=Cache(tmp_path / "cache.db"), transport=transport)

    async def _get_client() -> ABSClient:
        return client

    monkeypatch.setattr(server, "_get_client", _get_client)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_top_n_returns_top_rows_by_measure(mocked_client):
    """Top-5 ranking by unemployment_rate at the most-recent period."""
    r = await server.top_n("LF", "unemployment_rate", n=5)
    assert 1 <= r.row_count <= 5
    values = [rec.value for rec in r.records]
    assert values == sorted(values, reverse=True)


@pytest.mark.asyncio
async def test_top_n_bottom_direction_reverses(mocked_client):
    """direction='bottom' returns ascending (smallest-first) values."""
    r = await server.top_n("LF", "unemployment_rate", n=5, direction="bottom")
    assert 1 <= r.row_count <= 5
    values = [rec.value for rec in r.records]
    assert values == sorted(values)


@pytest.mark.asyncio
async def test_top_n_applies_filters_before_ranking(mocked_client):
    """Filters from the `filters=` arg are applied before the sort/slice."""
    r = await server.top_n(
        "LF", "unemployment_rate", n=3, filters={"sex": "persons"}
    )
    # Every returned row must carry the persons filter.
    for rec in r.records:
        sex = rec.dimensions.get("sex")
        assert sex in ("Persons", None), f"unexpected sex {sex!r}"


@pytest.mark.asyncio
async def test_top_n_caps_at_available_rows(mocked_client):
    """If the slice asks for more than what's available, return what we have."""
    r = await server.top_n("LF", "unemployment_rate", n=100)
    assert r.row_count <= 100


@pytest.mark.asyncio
async def test_top_n_envelope_preserved(mocked_client):
    """Trust-contract fields survive the rank-and-slice transformation."""
    r = await server.top_n("LF", "unemployment_rate", n=3)
    assert r.source == "Australian Bureau of Statistics (ABS)"
    assert "Creative Commons" in r.attribution
    assert r.source_url
    assert r.source_url == r.abs_url  # source_url and legacy alias match


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_top_n_rejects_empty_measure():
    with pytest.raises(ValueError, match="measure is required"):
        await server.top_n("LF", "", n=5)


@pytest.mark.asyncio
async def test_top_n_rejects_non_string_measure():
    with pytest.raises(ValueError, match="measure is required"):
        await server.top_n("LF", 123, n=5)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_top_n_rejects_bad_direction():
    with pytest.raises(ValueError, match="direction must be"):
        await server.top_n(
            "LF", "unemployment_rate", n=5, direction="sideways"  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_top_n_rejects_n_zero():
    with pytest.raises(ValueError, match=">= 1"):
        await server.top_n("LF", "unemployment_rate", n=0)


@pytest.mark.asyncio
async def test_top_n_rejects_n_too_large():
    with pytest.raises(ValueError, match="<= 100"):
        await server.top_n("LF", "unemployment_rate", n=500)


@pytest.mark.asyncio
async def test_top_n_rejects_n_bool():
    with pytest.raises(ValueError, match="positive integer"):
        await server.top_n("LF", "unemployment_rate", n=True)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_top_n_rejects_unknown_measure():
    """Unknown measure → ValueError listing valid measure keys + fuzzy hint."""
    with pytest.raises(ValueError, match="Unknown measure"):
        await server.top_n("LF", "not_a_real_measure", n=5)


@pytest.mark.asyncio
async def test_top_n_rejects_uncurated_dataset():
    """top_n requires a curated dataflow with a 'measure' dimension."""
    with pytest.raises(ValueError, match="not curated"):
        await server.top_n("RANDOM_RAW_ID", "x", n=5)


@pytest.mark.asyncio
async def test_top_n_rejects_measure_in_filters():
    """Passing 'measure' inside filters is rejected — supply it via measure="""
    with pytest.raises(ValueError, match="Do not pass 'measure'"):
        await server.top_n(
            "LF", "unemployment_rate", n=5, filters={"measure": "employed_persons"}
        )
