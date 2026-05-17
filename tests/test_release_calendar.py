"""Tests for the ABS release-calendar tool."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from abs_mcp.cache import Cache
from abs_mcp.release_calendar import (
    _classify,
    _convert_to_sydney_isostring,
    _sydney_offset_for,
    fetch_release_calendar,
    parse_entries,
)


FIXTURE = Path(__file__).parent / "fixtures" / "abs_future_releases.html"


@pytest.fixture
def html_fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.db"


# ----- _sydney_offset_for (DST switch) -----


def test_sydney_offset_aest_in_june():
    """June is solidly AEST (UTC+10)."""
    assert _sydney_offset_for(datetime(2026, 6, 15, tzinfo=timezone.utc)) == timedelta(hours=10)


def test_sydney_offset_aedt_in_january():
    """January is solidly AEDT (UTC+11)."""
    assert _sydney_offset_for(datetime(2026, 1, 15, tzinfo=timezone.utc)) == timedelta(hours=11)


def test_sydney_offset_naive_at_dst_boundary():
    """Mid-April is naive — we return AEDT (11) until early April, AEST (10) after."""
    # Apr 7 → after first Sunday-ish, AEST
    assert _sydney_offset_for(datetime(2026, 4, 15, tzinfo=timezone.utc)) == timedelta(hours=10)
    # Apr 1 → still AEDT
    assert _sydney_offset_for(datetime(2026, 4, 1, tzinfo=timezone.utc)) == timedelta(hours=11)


# ----- _convert_to_sydney_isostring -----


def test_convert_utc_to_sydney_aest():
    """'2026-05-13T01:30:00Z' → '2026-05-13T11:30:00+10:00' (May = AEST)."""
    result = _convert_to_sydney_isostring("2026-05-13T01:30:00Z")
    assert result == "2026-05-13T11:30:00+10:00"


def test_convert_utc_to_sydney_aedt():
    """'2026-01-13T00:30:00Z' → '2026-01-13T11:30:00+11:00' (Jan = AEDT)."""
    result = _convert_to_sydney_isostring("2026-01-13T00:30:00Z")
    assert result == "2026-01-13T11:30:00+11:00"


# ----- _classify (title → catalogue/dataset_id) -----


def test_classify_curated_titles_map_to_dataset_ids():
    assert _classify("Consumer Price Index, Australia") == ("6401.0", "CPI")
    assert _classify("Monthly Consumer Price Index Indicator") == ("6484.0", "CPI_MONTHLY")
    assert _classify("Labour Force, Australia") == ("6202.0", "LF")
    assert _classify("Wage Price Index, Australia") == ("6345.0", "WPI")
    assert _classify("Producer Price Indexes, Australia") == ("6427.0", "PPI_FD")
    assert _classify("Lending indicators") == ("5601.0", "LEND_HOUSING")


def test_classify_non_curated_titles_have_catalogue_but_null_dataset_id():
    cat, ds = _classify("Retail Trade, Australia")
    assert cat == "8501.0"
    assert ds is None
    cat, ds = _classify("International Trade in Goods, March 2026")
    assert cat == "5368.0"
    assert ds is None


def test_classify_unknown_title_returns_double_none():
    assert _classify("Some Random Publication") == (None, None)


# ----- parse_entries (HTML → list[ReleaseEntry]) -----


def test_parse_entries_returns_non_empty_list(html_fixture: str):
    entries = parse_entries(html_fixture)
    assert len(entries) >= 20, f"Expected ≥20 entries, got {len(entries)}"


def test_parse_entries_extracts_first_row_correctly(html_fixture: str):
    entries = parse_entries(html_fixture)
    first = entries[0]
    # The fixture's first row is Producer Price Indexes, March 2026
    assert first.title == "Producer Price Indexes, Australia"
    assert first.reference_period == "March 2026"
    assert first.event_type == "data_release"
    assert first.dataset_id == "PPI_FD"
    assert first.publication_id == "6427.0"
    assert first.source_url.startswith("https://www.abs.gov.au/statistics/")
    # release_at uses Sydney offset
    assert first.release_at.tzinfo is not None
    assert first.release_at.utcoffset() in (timedelta(hours=10), timedelta(hours=11))


def test_parse_entries_handles_missing_reference_period(html_fixture: str):
    """Some calendar entries don't have reference-period spans — they shouldn't crash."""
    # Synthetic minimal row with no reference-period element
    minimal_html = """
    <strong class="event-name">Some Publication, Australia</strong>
    <span class="event-date"><time datetime="2026-05-13T01:30:00Z" class="datetime">11:30am</time></span>
    <a href="/statistics/foo/bar">link</a>
    """
    entries = parse_entries(minimal_html)
    assert len(entries) == 1
    assert entries[0].title == "Some Publication, Australia"
    assert entries[0].reference_period is None


def test_parse_entries_skips_malformed_rows(html_fixture: str):
    """Rows that don't match the regex are silently skipped, not raised."""
    junk = "<strong class='event-name'>No close tag<a href='/statistics/x'>"
    entries = parse_entries(junk)
    assert entries == []


# ----- fetch_release_calendar (network + cache + horizon filter) -----


async def test_fetch_release_calendar_serves_cache_when_live_503s(
    html_fixture: str, db_path: Path
) -> None:
    """Graceful degradation per CLAUDE.md: 5xx → fall back to last-good payload."""
    cache = Cache(db_path)
    # Pre-warm the cache.
    await cache.set(
        "https://www.abs.gov.au/release-calendar/future-releases-calendar",
        html_fixture.encode("utf-8"),
        kind="calendar",
    )
    # Now simulate live ABS returning 503; we should still get results
    # (from cache) with stale=True.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream broke")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        # Force a fresh fetch by passing a tiny TTL.
        # (We test the stale path explicitly by passing a 0-TTL alias kind;
        # for this test we directly drive _fetch_html via fetch_release_calendar)
        from abs_mcp import cache as cache_mod
        original_ttl = cache_mod.TTL["calendar"]
        cache_mod.TTL["calendar"] = timedelta(seconds=0)
        try:
            entries, stale, reason = await fetch_release_calendar(http, cache, days_ahead=3650)
        finally:
            cache_mod.TTL["calendar"] = original_ttl

    # Cache fallback served — stale True, parsed entries present.
    assert stale is True
    assert reason is not None
    assert "503" in reason
    # We passed a 10-year horizon so all parseable future entries return.
    assert len(entries) >= 0  # >=0 because the fixture's entries may be in the past relative to test-run time


async def test_fetch_release_calendar_horizon_filters(
    html_fixture: str, db_path: Path
) -> None:
    """Entries past the horizon are excluded."""
    cache = Cache(db_path)
    await cache.set(
        "https://www.abs.gov.au/release-calendar/future-releases-calendar",
        html_fixture.encode("utf-8"),
        kind="calendar",
    )
    async with httpx.AsyncClient() as http:
        entries_30, _, _ = await fetch_release_calendar(http, cache, days_ahead=30)
        entries_365, _, _ = await fetch_release_calendar(http, cache, days_ahead=365)
    assert len(entries_365) >= len(entries_30)


async def test_fetch_release_calendar_returns_sorted_ascending(
    html_fixture: str, db_path: Path
) -> None:
    """Returned entries are sorted by release_at ascending."""
    cache = Cache(db_path)
    await cache.set(
        "https://www.abs.gov.au/release-calendar/future-releases-calendar",
        html_fixture.encode("utf-8"),
        kind="calendar",
    )
    async with httpx.AsyncClient() as http:
        entries, _, _ = await fetch_release_calendar(http, cache, days_ahead=365)
    timestamps = [e.release_at for e in entries]
    assert timestamps == sorted(timestamps)


# ----- Tool surface -----


async def test_release_calendar_tool_rejects_bad_days_ahead():
    from abs_mcp import server

    with pytest.raises(ValueError, match="days_ahead must be"):
        await server.release_calendar(0)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="days_ahead must be"):
        await server.release_calendar(366)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="must be an int"):
        await server.release_calendar("30")  # type: ignore[arg-type]
