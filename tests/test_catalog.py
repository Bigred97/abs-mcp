from io import BytesIO
from pathlib import Path

import httpx
import pytest
import sdmx

from abs_mcp.cache import Cache
from abs_mcp.catalog import (
    describe_from_dsd,
    list_dataflows,
    search,
    search_in_memory,
)
from abs_mcp.client import ABSClient
from abs_mcp.models import DatasetSummary


FIXTURES = Path(__file__).parent / "fixtures"


def _client_with_fixture(db_path: Path, *, dataflows_xml: bytes) -> ABSClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=dataflows_xml)

    return ABSClient(cache=Cache(db_path), transport=httpx.MockTransport(handler))


async def test_list_dataflows_returns_all(tmp_path: Path) -> None:
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        summaries = await list_dataflows(client)
    assert len(summaries) > 100, "ABS catalogue has 1000+ dataflows"
    ids = {s.id for s in summaries}
    assert "LF" in ids and "CPI" in ids
    assert "BA_GCCSA" in ids
    assert "LEND_HOUSING" in ids


async def test_list_dataflows_marks_curated(tmp_path: Path) -> None:
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        summaries = await list_dataflows(client, curated_ids={"LF", "CPI"})
    by_id = {s.id: s for s in summaries}
    assert by_id["LF"].is_curated is True
    assert by_id["CPI"].is_curated is True
    assert by_id["BA_GCCSA"].is_curated is False


async def test_list_dataflows_indirection_remaps_sdmx_target(tmp_path: Path) -> None:
    """0.10.0: curated `CPI` points to SDMX `CPI_Q`. list_dataflows must emit
    a single entry with display id `CPI` (not the raw SDMX `CPI_Q`) and must
    NOT also emit the legacy SDMX `CPI` combined product (which we hide so
    customers calling get_data('CPI') hit the curated indirection path)."""
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        summaries = await list_dataflows(client, curated_ids={"LF", "CPI", "CPI_MONTHLY"})
    ids = [s.id for s in summaries]
    by_id = {s.id: s for s in summaries}
    # Display id `CPI` is curated and surfaces once
    assert ids.count("CPI") == 1
    assert by_id["CPI"].is_curated is True
    # SDMX `CPI_Q` was remapped to display id `CPI` — no raw CPI_Q entry
    assert "CPI_Q" not in by_id
    # CPI_MONTHLY also indirects (to SDMX CPI_M) → display id surfaces once
    assert ids.count("CPI_MONTHLY") == 1
    assert by_id["CPI_MONTHLY"].is_curated is True
    assert "CPI_M" not in by_id


def test_search_in_memory_finds_unemployment_lf() -> None:
    sums = [
        DatasetSummary(id="LF", name="Labour Force Survey", description="employment, unemployment, participation"),
        DatasetSummary(id="CPI", name="Consumer Price Index", description="inflation"),
        DatasetSummary(id="OTHER", name="Other", description="unrelated stuff"),
    ]
    matches = search_in_memory(sums, "unemployment", limit=3)
    assert matches[0].id == "LF"


def test_search_in_memory_empty_query_raises() -> None:
    with pytest.raises(ValueError):
        search_in_memory([], "   ", limit=5)


async def test_search_unemployment_finds_lf_in_real_catalogue(tmp_path: Path) -> None:
    """Brief-required test: fuzzy 'unemployment' -> LF in top results.

    Passes `curated_ids` so the high-signal ranker has the keyword overlay
    + curated-bonus context. Without it, Census tables that mention
    'unemployment' in their long descriptions outrank LF (the description
    pool is intentionally capped to stop verbose prose dominating, but
    that cap is only meaningful when curated keywords compete in the high
    pool — exactly the production wiring `server.search_datasets` uses)."""
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        results = await search(client, "unemployment", limit=10, curated_ids={"LF", "CPI"})
    top_ids = [r.id for r in results]
    assert "LF" in top_ids, f"LF should appear in top 10 for 'unemployment', got: {top_ids}"


# ---------- 0.10.0: canonical-query ranking suite (brief acceptance set) ----------
#
# These pin the top-N for the 7 most-asked customer queries. They guard the
# high/low-signal split + token_set_ratio scorer + deprecation penalty against
# regressions like:
#   * `retail` → ['C21_G01_POA', ...] (long Census descriptions dominating)
#   * `retail` → ['RT', 'HSI_M', ...] (ceased dataset outranking active replacement)
#   * `lending` → ['ABS_CENSUS2011_B27', ...] (WRatio substring 'lending' inside 'Blending')
#
# Each test uses the real fixture catalogue + full curated set so the assertions
# travel through the same `search()` orchestration `server.search_datasets` uses.

import pytest as _pytest_for_ranker

from abs_mcp import curated as _curated_mod_for_ranker


@_pytest_for_ranker.mark.parametrize(
    "query, expected_id, position",
    [
        ("unemployment", "LF", 0),       # canonical labour-force series
        ("retail", "HSI_M", 0),          # active replacement must beat ceased RT
        ("GDP", "ANA_AGG", 0),           # national accounts headline
        ("lending", "LEND_HOUSING", 0),  # token-strict scorer beats 'Blending' substring
    ],
)
async def test_canonical_query_top_result(
    tmp_path: Path, query: str, expected_id: str, position: int
) -> None:
    """Top-1 ranking for queries with a single canonical answer."""
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        results = await search(
            client, query, limit=5,
            curated_ids=set(_curated_mod_for_ranker.list_ids()),
        )
    ids = [r.id for r in results]
    assert ids[position] == expected_id, (
        f"{query!r} expected {expected_id} at position {position}, got {ids}"
    )


async def test_canonical_query_inflation_surfaces_cpi(tmp_path: Path) -> None:
    """`inflation` → CPI in top 2. CPI_MONTHLY (the monthly indicator sibling
    added in 0.10.0) is the other valid hit; either may take the top slot, but
    a query of 'inflation' without qualification must surface canonical CPI."""
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        results = await search(
            client, "inflation", limit=5,
            curated_ids=set(_curated_mod_for_ranker.list_ids()),
        )
    ids = [r.id for r in results]
    assert "CPI" in ids[:2], f"CPI must be in top 2 for 'inflation', got {ids}"


async def test_canonical_query_wages_surfaces_wpi_or_awe(tmp_path: Path) -> None:
    """`wages` → WPI or AWE at #1. WPI is the canonical headline; AWE (Average
    Weekly Earnings) is also accepted because brief notes either is correct."""
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        results = await search(
            client, "wages", limit=5,
            curated_ids=set(_curated_mod_for_ranker.list_ids()),
        )
    ids = [r.id for r in results]
    assert ids[0] in {"WPI", "AWE"}, (
        f"'wages' top result must be WPI or AWE, got {ids}"
    )
    assert "WPI" in ids[:3], f"WPI must be in top 3 for 'wages', got {ids}"


async def test_canonical_query_population_surfaces_erp(tmp_path: Path) -> None:
    """`population` → at least one of ERP_Q / ABS_ANNUAL_ERP_ASGS2021 in top 2.
    Both are legitimately 'the population dataset' (quarterly vs annual); the
    Census POA tables that previously dominated this query must drop below."""
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        results = await search(
            client, "population", limit=5,
            curated_ids=set(_curated_mod_for_ranker.list_ids()),
        )
    ids = [r.id for r in results]
    erp_ids = {"ERP_Q", "ABS_ANNUAL_ERP_ASGS2021"}
    assert erp_ids & set(ids[:2]), (
        f"'population' top 2 must include ERP_Q or ABS_ANNUAL_ERP_ASGS2021, got {ids}"
    )


async def test_canonical_query_retail_beats_ceased_rt(tmp_path: Path) -> None:
    """Regression guard: ceased datasets must rank below their active
    replacements. RT was ceased Jul 2025; HSI_M is its blessed replacement.
    A query of 'retail' must put HSI_M ahead of RT even though both have rich
    retail-themed `search_keywords`."""
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        results = await search(
            client, "retail", limit=5,
            curated_ids=set(_curated_mod_for_ranker.list_ids()),
        )
    ids = [r.id for r in results]
    assert ids.index("HSI_M") < ids.index("RT"), (
        f"HSI_M (active) must outrank RT (ceased) for 'retail', got {ids}"
    )


def test_describe_from_dsd_lf_returns_dimensions() -> None:
    xml = (FIXTURES / "lf_dsd.xml").read_bytes()
    msg = sdmx.read_sdmx(BytesIO(xml))
    detail = describe_from_dsd("LF", msg)
    assert detail.id == "LF"
    assert detail.is_curated is False
    dim_ids = {d.sdmx_id for d in detail.dimensions}
    assert {"MEASURE", "REGION", "SEX", "TSEST"} <= dim_ids
    measure = next(d for d in detail.dimensions if d.sdmx_id == "MEASURE")
    assert len(measure.values) > 0
