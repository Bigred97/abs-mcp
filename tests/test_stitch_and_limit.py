"""Regression tests for two server.py bugs (fixed together):

1. The CPI_MONTHLY historical-stitch merge used attribute assignment
   (`resp.period.start = ...`) against `DataResponse.period`, which is a
   plain `dict[str, str | None]`, not an object. That raised AttributeError
   every time the stitch triggered, and the surrounding bare
   `except Exception: pass` silently swallowed it — so stitched responses
   shipped with a period range that did not span the merged records.
   `test_cpi_monthly_stitch_period_spans_full_merged_window` below fails
   against the old attribute-assignment code and passes with the dict-item
   fix.

2. `latest()` had no row cap: a register-shaped dataflow could return
   thousands of records with no truncation signal.
   `test_latest_caps_at_limit_and_sets_truncated_at` exercises the new
   `limit` parameter + `DataResponse.truncated_at`.

Both tests run fully offline: no real ABS API traffic. The stitch test
monkeypatches `server._get_client` (a lightweight fake, not a real
ABSClient) and `server.build_response` (returns canned DataResponse
objects keyed off the `sdmx_id` kwarg) so it exercises exactly the
merge/period-update logic in `server.py` without needing real SDMX-XML
fixtures for CPI / CPI_M. The limit test reuses the existing LF fixture +
httpx.MockTransport pattern from test_top_n.py.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from abs_mcp import server
from abs_mcp.cache import Cache
from abs_mcp.client import ABSClient
from abs_mcp.models import DataResponse, Observation

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Bug 1: CPI_MONTHLY stitch period-envelope update
# ---------------------------------------------------------------------------


def _obs(period: str, value: float = 1.0) -> Observation:
    return Observation(period=period, value=value, dimensions={}, unit="Percent")


# Primary (new `CPI` FREQ=M dataflow) records: 2025-09 .. 2026-01. Overlaps
# the legacy series at 2025-09; primary wins on overlap.
_PRIMARY_PERIODS = ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01"]
# Legacy (frozen `CPI_M`, cat 6484.0) records: 2024-11 .. 2025-09.
_LEGACY_PERIODS = [
    "2024-11", "2024-12", "2025-01", "2025-02", "2025-03",
    "2025-04", "2025-05", "2025-06", "2025-07", "2025-08", "2025-09",
]


class _FakeClient:
    """Stand-in for ABSClient. Neither get_datastructure nor get_data need to
    return anything meaningful because `server.build_response` is
    monkeypatched below — this fake only has to satisfy the
    `sdmx_id in dsd_msg.structure` membership check in `_get_data_impl`.
    """

    class _FakeDim:
        id = "TIME_PERIOD"

    class _FakeDims:
        components: list = []

    class _FakeStructureDef:
        dimensions = None

        def __init__(self):
            self.dimensions = _FakeClient._FakeDims()

    class _FakeDSD:
        def __init__(self, sdmx_id: str):
            self.structure = {sdmx_id: _FakeClient._FakeStructureDef()}

    async def get_datastructure(self, sdmx_id: str, version: str | None = None):
        return _FakeClient._FakeDSD(sdmx_id)

    async def get_data(self, sdmx_id: str, **kwargs):
        return f"data-msg-for-{sdmx_id}"


def _fake_build_response(
    *,
    dataset_id,
    msg,
    dsd_msg,
    user_query,
    fmt,
    abs_url,
    curated,
    start_period,
    end_period,
    sdmx_id,
):
    """Canned DataResponse, branching only on `sdmx_id` (primary vs legacy).

    The primary response deliberately reports a period bounded to ONLY its
    own records (2025-09 .. 2026-01) — i.e. what the response would look
    like if the stitch's period update never ran. If the stitch fix is
    working, `_get_data_impl` overwrites `resp.period` to span the full
    merged window (2024-11 .. 2026-01) after this fake is called.
    """
    if sdmx_id == "CPI_M":
        records = [_obs(p) for p in _LEGACY_PERIODS]
        period = {"start": _LEGACY_PERIODS[0], "end": _LEGACY_PERIODS[-1]}
    else:
        records = [_obs(p) for p in _PRIMARY_PERIODS]
        period = {"start": _PRIMARY_PERIODS[0], "end": _PRIMARY_PERIODS[-1]}
    from datetime import UTC, datetime

    return DataResponse(
        dataset_id=dataset_id,
        dataset_name="Consumer Price Index (Monthly)",
        query=dict(user_query or {}),
        period=period,
        records=records,
        row_count=len(records),
        source_url=abs_url,
        abs_url=abs_url,
        retrieved_at=datetime.now(UTC),
    )


@pytest.fixture
def fake_stitch_client(monkeypatch):
    fake_client = _FakeClient()

    async def _get_client():
        return fake_client

    monkeypatch.setattr(server, "_get_client", _get_client)
    monkeypatch.setattr(server, "build_response", _fake_build_response)
    yield fake_client


async def test_cpi_monthly_stitch_period_spans_full_merged_window(fake_stitch_client):
    """The stitched CPI_MONTHLY response's period must span the FULL
    merged window (earliest legacy period -> latest primary period), not
    just the primary fetch's own bounds.

    Regression guard for the AttributeError bug: `resp.period` is a plain
    dict, and the old code did `resp.period.start = first_p` /
    `resp.period.end = last_p`, which raises AttributeError on a dict every
    time — silently swallowed by the enclosing bare `except Exception:
    pass`. That left `resp.period` at the primary-only bounds
    (2025-09..2026-01) even though `resp.records`/`resp.row_count` had
    already been correctly merged to span 2024-11..2026-01. This test
    fails against that old code and passes with the dict-item-assignment
    fix.
    """
    resp = await server._get_data_impl(
        "CPI_MONTHLY",
        None,
        "2025-06",
        None,
        "records",
        raw_sdmx_key="M13.3.999901.50.10.M",
    )

    merged_periods = sorted(r.period for r in resp.records)
    assert merged_periods[0] == "2024-11"
    assert merged_periods[-1] == "2026-01"
    # Dedup: 2025-09 appears in both legacy and primary; primary wins, so it
    # is not duplicated. 10 unique legacy + 5 primary = 15.
    assert resp.row_count == 15
    assert len(resp.records) == 15

    # The actual regression assertion: period envelope spans the merge, not
    # just the primary fetch's own bounds.
    assert resp.period == {"start": "2024-11", "end": "2026-01"}, (
        f"resp.period {resp.period!r} does not span the full stitched "
        "window — the period-envelope update likely silently failed "
        "(dict vs attribute assignment bug)."
    )


# ---------------------------------------------------------------------------
# Bug 2: latest() row cap + truncated_at
# ---------------------------------------------------------------------------


def _fixture_for(url: str) -> bytes:
    if "/datastructure/ABS/LF" in url:
        return (FIXTURES / "lf_dsd.xml").read_bytes()
    if "/data/ABS,LF/" in url:
        return (FIXTURES / "lf_one_obs.xml").read_bytes()
    raise AssertionError(f"no fixture for {url}")


def _handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=_fixture_for(str(request.url)))


@pytest.fixture
async def mocked_lf_client(tmp_path: Path, monkeypatch):
    """Same offline LF-fixture pattern as test_top_n.py's mocked_client."""
    transport = httpx.MockTransport(_handler)
    client = ABSClient(cache=Cache(tmp_path / "cache.db"), transport=transport)

    async def _get_client() -> ABSClient:
        return client

    monkeypatch.setattr(server, "_get_client", _get_client)
    yield client
    await client.aclose()


async def test_latest_caps_at_limit_and_sets_truncated_at(mocked_lf_client):
    """latest() must cap records at `limit` and set truncated_at to the
    original (pre-cap) row count.

    Regression guard for the missing-cap bug: previously `latest()` took no
    `limit` parameter at all and could return every row unfiltered (e.g.
    ~2,400 SA2 areas x 8 measures for C21_G02_SA2), with `truncated_at`
    never assigned anywhere in the codebase.
    """
    # Uncapped baseline: the LF fixture has well over 100 series/records.
    full = await server.latest(dataset_id="LF", limit=10000)
    pre_cap_count = full.row_count
    assert pre_cap_count > 20, (
        f"expected the LF fixture to yield >20 records uncapped, got {pre_cap_count}"
    )
    assert full.truncated_at is None

    capped = await server.latest(dataset_id="LF", limit=5)
    assert len(capped.records) == 5
    assert capped.row_count == 5
    assert capped.truncated_at == pre_cap_count, (
        f"truncated_at ({capped.truncated_at!r}) should equal the original "
        f"pre-cap row count ({pre_cap_count})"
    )


async def test_latest_default_limit_is_fifty(mocked_lf_client):
    """latest() with no explicit `limit` defaults to 50, mirroring the
    ato-mcp pattern this fix was modeled on."""
    resp = await server.latest(dataset_id="LF")
    assert len(resp.records) <= 50
    if resp.truncated_at is not None:
        assert resp.truncated_at > 50


async def test_latest_below_limit_leaves_truncated_at_none(mocked_lf_client):
    """When the result set fits under `limit`, truncated_at must stay None
    (no false-positive truncation signal)."""
    resp = await server.latest(
        dataset_id="LF",
        filters={"region": "nsw", "measure": "unemployment_rate"},
        limit=10000,
    )
    assert resp.truncated_at is None


# ---------------------------------------------------------------------------
# Bug 3: latest() truncation DIRECTION -- head-slice kept the STALEST series
# ---------------------------------------------------------------------------
#
# The LF fixture used above (`lf_one_obs.xml`) happens to carry a SINGLE
# TIME_PERIOD ("2026-03") across every series, so it cannot distinguish a
# head-slice from a tail-slice -- both keep rows from the same period. The
# real defect only shows up when different matched series have DIFFERENT
# last periods, which is exactly what `lastNObservations=1` produces once a
# filter matches multiple series (e.g. multiple regions): each series' own
# most-recent observation, not a single shared reference period. This
# fixture forces that mixed-period shape.

_MIXED_SERIES_PERIODS = ["2024-01", "2024-06", "2025-01", "2025-06", "2026-01"]


def _fake_build_response_mixed_periods(
    *,
    dataset_id,
    msg,
    dsd_msg,
    user_query,
    fmt,
    abs_url,
    curated,
    start_period,
    end_period,
    sdmx_id,
):
    """Canned DataResponse: one record per series (mimicking what
    `lastNObservations=1` yields when a filter matches several series),
    each with a DIFFERENT period, already sorted ASCENDING exactly as the
    real `build_response`'s `underlying.sort(...)` call would leave them.
    """
    records = [
        Observation(period=p, value=1.0, dimensions={"region": f"region_{i}"}, unit="Percent")
        for i, p in enumerate(_MIXED_SERIES_PERIODS)
    ]
    from datetime import UTC, datetime

    return DataResponse(
        dataset_id=dataset_id,
        dataset_name="Labour Force",
        query=dict(user_query or {}),
        period={"start": _MIXED_SERIES_PERIODS[0], "end": _MIXED_SERIES_PERIODS[-1]},
        records=records,
        row_count=len(records),
        source_url=abs_url,
        abs_url=abs_url,
        retrieved_at=datetime.now(UTC),
    )


@pytest.fixture
def fake_mixed_period_client(monkeypatch):
    fake_client = _FakeClient()

    async def _get_client():
        return fake_client

    monkeypatch.setattr(server, "_get_client", _get_client)
    monkeypatch.setattr(server, "build_response", _fake_build_response_mixed_periods)
    yield fake_client


async def test_latest_cap_keeps_newest_series_not_oldest(fake_mixed_period_client):
    """latest()'s row cap must keep the MOST RECENT series when different
    matched series have different last periods -- not head-slice the
    ascending-sorted list and keep the stalest ones.

    Regression guard: the original cap did `resp.records = resp.records[:limit]`.
    Records are sorted ASCENDING by period (oldest first), so with 5 series
    spanning 2024-01 .. 2026-01 and a cap of 3, that naive head-slice kept
    2024-01 / 2024-06 / 2025-01 (the three OLDEST series) and silently
    dropped 2025-06 and 2026-01 (the two freshest). This exercises the
    PUBLIC `server.latest()` tool end-to-end (not the shaping helper
    directly) -- it fails against the old head-slice and passes once the
    cap tail-slices periodic records via `truncate_records`.
    """
    resp = await server.latest(dataset_id="LF", filters={"region": "nsw"}, limit=3)

    assert resp.truncated_at == 5
    assert len(resp.records) == 3
    kept_periods = sorted(r.period for r in resp.records)
    assert kept_periods == ["2025-01", "2025-06", "2026-01"], (
        f"latest() kept {kept_periods}, expected the three MOST RECENT "
        "periods (2025-01, 2025-06, 2026-01) -- got stale series instead, "
        "meaning the cap is still head-slicing an ascending list instead "
        "of tail-slicing it."
    )
    assert "2024-01" not in kept_periods
    assert "2024-06" not in kept_periods
