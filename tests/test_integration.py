"""Live integration tests against the real ABS Data API.

Marked `live` so they only run with `pytest -m live`. Default invocation skips
them. Run locally before tagging a release; CI runs unit tests only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from abs_mcp import server
from abs_mcp.cache import Cache
from abs_mcp.client import ABSClient

pytestmark = pytest.mark.live


@pytest.fixture
async def live_client(tmp_path: Path):
    """Live ABSClient with a per-test SQLite cache (so caching doesn't leak)."""
    client = ABSClient(cache=Cache(tmp_path / "live_cache.db"))
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
def patch_server_client(live_client, monkeypatch):
    """Force the server module to use our isolated live client."""
    async def _get_live_client():
        return live_client

    monkeypatch.setattr(server, "_get_client", _get_live_client)


async def test_search_unemployment_finds_lf():
    """Brief-required: fuzzy 'unemployment' finds the Labour Force dataflow."""
    results = await server.search_datasets("unemployment", limit=10)
    ids = [r.id for r in results]
    assert "LF" in ids, f"LF should be in top 10 for 'unemployment', got {ids}"


async def test_get_data_lf_nsw_unemployment_returns_records():
    """Brief-required: get_data for NSW unemployment returns 10+ records."""
    resp = await server.get_data(
        dataset_id="LF",
        filters={"region": "nsw", "measure": "unemployment_rate"},
        start_period="2020",
    )
    assert len(resp.records) >= 10, f"Expected 10+ records, got {len(resp.records)}"
    sample = resp.records[0]
    assert sample.period
    assert sample.value is not None
    # Curated dim labels resolved
    assert sample.dimensions["region"] == "New South Wales"


async def test_latest_cpi_returns_one_observation():
    """Brief-required: latest('CPI') returns at least one observation."""
    resp = await server.latest(dataset_id="CPI")
    assert len(resp.records) >= 1
    sample = resp.records[0]
    assert sample.period
    assert sample.value is not None


async def test_describe_non_curated_dataflow_returns_translated_metadata():
    """Brief-required: a non-curated dataflow returns valid translated metadata."""
    # Pick a dataflow that exists but isn't in our curated set.
    detail = await server.describe_dataset("ALC")  # Apparent Consumption of Alcohol
    assert detail.id == "ALC"
    assert detail.is_curated is False
    assert len(detail.dimensions) >= 1
    assert all(d.values for d in detail.dimensions)


async def test_latest_lf_unemployment_nsw_returns_clean_response():
    """Real-world end-to-end: ask for the latest unemployment rate in NSW."""
    resp = await server.latest(
        dataset_id="LF",
        filters={"region": "nsw", "measure": "unemployment_rate"},
    )
    assert resp.dataset_id == "LF"
    assert resp.dataset_name == "Labour Force"
    assert len(resp.records) >= 1
    obs = resp.records[0]
    assert obs.period
    assert obs.value is not None
    assert obs.dimensions["region"] == "New South Wales"
    # Should be a percentage value plausibly in [0, 30]
    assert 0 < obs.value < 30, f"unemployment rate {obs.value} out of plausible range"


async def test_describe_curated_returns_plain_english_dims():
    detail = await server.describe_dataset("LF")
    assert detail.is_curated is True
    assert detail.name == "Labour Force"
    dim_names = {d.name for d in detail.dimensions}
    assert {"measure", "region", "sex"} <= dim_names
    # Hidden dims like 'frequency' and 'adjustment' should NOT be in the
    # main dimensions list — they appear in `hidden_defaults` instead.
    assert "frequency" not in dim_names


async def test_get_data_accepts_int_year_for_periods():
    """0.2.12: MCP / LLM clients often send a year as a JSON number
    (`start_period=2024`) rather than a string. Pre-0.2.12 this errored
    at the Pydantic boundary with a verbose validation message. Now:
    int → str coercion handled transparently. Parity with rba-mcp 0.1.8."""
    resp = await server.get_data(
        dataset_id="LF",
        filters={"region": "nsw", "measure": "unemployment_rate"},
        start_period=2024,  # type: ignore[arg-type]
        end_period=2024,    # type: ignore[arg-type]
    )
    # 2024 should yield ≥12 monthly observations for unemployment_rate
    assert len(resp.records) >= 6
    # query echo should show the coerced string form
    assert resp.query.get("start_period") in (2024, "2024", None)


async def test_describe_curated_surfaces_hidden_defaults():
    """0.2.12: hidden dims with defaults are surfaced in `hidden_defaults`
    so the LLM can reason about what's being auto-applied (e.g. AGE=15+,
    TSEST=seasonally adjusted, FREQ=monthly for LF). Pre-0.2.12 these
    were silently filtered out, leaving the LLM blind to query assumptions."""
    detail = await server.describe_dataset("LF")
    hidden_names = {d.name for d in detail.hidden_defaults}
    assert {"adjustment", "age", "frequency"} <= hidden_names, (
        f"expected adjustment/age/frequency in hidden_defaults, got {hidden_names}"
    )
    # Each hidden default carries exactly one value: the auto-applied default
    for hd in detail.hidden_defaults:
        assert len(hd.values) == 1
        v = hd.values[0]
        assert v.key == "<default>"
        assert v.sdmx_code  # always populated
    # Specific defaults from LF.yaml
    by_name = {hd.name: hd for hd in detail.hidden_defaults}
    assert by_name["adjustment"].values[0].sdmx_code == "20"  # seasonally adjusted
    assert by_name["frequency"].values[0].sdmx_code == "M"


async def test_list_curated_returns_ten():
    ids = server.list_curated()
    assert set(ids) == {
        "LF", "CPI", "ABS_ANNUAL_ERP_ASGS2021", "BA_GCCSA", "LEND_HOUSING",
        "WPI", "JV", "ANA_AGG", "AWE", "ERP_Q",
    }


async def test_latest_wpi_annual_wage_growth_returns_observation():
    resp = await server.latest(
        dataset_id="WPI",
        filters={"region": "australia", "measure": "change_year"},
    )
    assert len(resp.records) >= 1
    obs = resp.records[0]
    assert obs.value is not None
    assert -5 < obs.value < 15, f"annual wage growth {obs.value} out of plausible range"


async def test_latest_jv_total_vacancies_nsw():
    resp = await server.latest(
        dataset_id="JV",
        filters={"region": "nsw", "measure": "vacancies"},
    )
    assert len(resp.records) >= 1
    obs = resp.records[0]
    assert obs.value is not None
    assert obs.value > 0
    assert obs.dimensions["region"] == "New South Wales"
    # JV is published in thousands; the unit fix should multiply through
    assert obs.value > 1000, f"JV scaled value should be in raw count: {obs.value}"
    assert obs.unit == "Number"


@pytest.mark.parametrize("dataset_id, filters, expect_unit", [
    ("LF", {"region": "nsw", "measure": "unemployment_rate"}, "Percent"),
    ("LF", {"region": "nsw", "measure": "employed_persons"}, "Number"),
    ("CPI", {"region": "australia", "measure": "change_year"}, "Percent"),
    ("ABS_ANNUAL_ERP_ASGS2021", {"region": "nsw", "region_type": "states"}, "Persons"),
    ("BA_GCCSA", {"region": "nsw", "measure": "dwelling_units"}, "Number"),
    ("LEND_HOUSING", {"region": "nsw", "measure": "value"}, "Australian Dollars"),
    ("WPI", {"region": "australia", "measure": "change_year"}, "Percent"),
    ("JV", {"region": "australia", "measure": "vacancies"}, "Number"),
    ("ANA_AGG", {"series": "gdp", "measure": "change"}, "Percent"),
    ("AWE", {"region": "australia"}, "Australian Dollars"),
    ("ERP_Q", {"region": "nsw"}, "Persons"),
])
async def test_every_curated_dataflow_returns_useful_record(dataset_id, filters, expect_unit):
    """Every curated dataflow must answer a minimal sensible query — the kind a
    user would actually ask. Catches missing/wrong YAML defaults."""
    resp = await server.latest(dataset_id=dataset_id, filters=filters)
    assert len(resp.records) >= 1, f"{dataset_id} returned no records for {filters}"
    obs = resp.records[0]
    assert obs.value is not None, f"{dataset_id} value is None for {filters}"
    assert obs.unit == expect_unit, f"{dataset_id} unit was {obs.unit!r}, expected {expect_unit!r}"
    # Hidden curated dims must not leak into record dimensions
    for ugly in ("age", "tsest", "frequency", "freq", "value_band", "work_type",
                 "data_item", "loan_type", "loan_purpose"):
        assert ugly not in obs.dimensions, (
            f"hidden dim {ugly!r} leaked into {dataset_id} record dimensions: {obs.dimensions}"
        )
    # Response-level unit echoes observation unit when homogeneous
    assert resp.unit == expect_unit


async def test_get_data_csv_format_returns_csv_with_period():
    resp = await server.get_data(
        dataset_id="LF",
        filters={"region": "nsw", "measure": "unemployment_rate", "sex": "persons"},
        start_period="2024",
        format="csv",
    )
    assert resp.csv is not None
    assert "TIME_PERIOD" in resp.csv
    assert resp.records == []
    # csv format still populates period bounds (was None before fix)
    assert resp.period["start"] is not None
    assert resp.period["end"] is not None
    assert resp.unit == "Percent"


async def test_get_data_rejects_invalid_format():
    """format='JSON' (or any non-{records,series,csv}) should error cleanly, not silently fall through."""
    with pytest.raises(ValueError, match="Unknown format"):
        await server.get_data(
            dataset_id="LF",
            filters={"region": "nsw", "measure": "unemployment_rate"},
            format="JSON",  # type: ignore[arg-type]
        )


async def test_get_data_normalises_format_case():
    """format='CSV' (uppercase) should work like 'csv'."""
    resp = await server.get_data(
        dataset_id="LF",
        filters={"region": "nsw", "measure": "unemployment_rate", "sex": "persons"},
        start_period="2024",
        format="CSV",  # type: ignore[arg-type]
    )
    assert resp.csv is not None
    assert "TIME_PERIOD" in resp.csv


async def test_get_data_rejects_end_before_start():
    """Catch reversed periods client-side rather than letting ABS return a confusing 4xx."""
    with pytest.raises(ValueError, match="end_period .* is before start_period"):
        await server.get_data(
            dataset_id="LF",
            filters={"region": "nsw", "measure": "unemployment_rate"},
            start_period="2025",
            end_period="2020",
        )


@pytest.mark.parametrize("query,expected_top_id", [
    ("unemployment", "LF"),
    ("labour force", "LF"),
    ("inflation", "CPI"),
    ("consumer prices", "CPI"),
    ("wage growth", "WPI"),
    ("earnings", "AWE"),
    ("job vacancies", "JV"),
    ("gdp", "ANA_AGG"),
    ("national accounts", "ANA_AGG"),
    ("building approvals", "BA_GCCSA"),
    ("housing finance", "LEND_HOUSING"),
    ("mortgage", "LEND_HOUSING"),
])
async def test_common_search_query_finds_curated_at_top(query, expected_top_id):
    """Common AU economic search terms must hit a curated dataflow at rank #1
    or #2. Without keywords + boost, ABS's ~800 census tables drown out
    every common query."""
    results = await server.search_datasets(query=query, limit=5)
    top_ids = [r.id for r in results]
    assert expected_top_id in top_ids[:2], (
        f"'{query}' should find {expected_top_id} in top 2; got {top_ids}"
    )


async def test_lend_housing_no_measure_returns_dollar_value():
    """Regression: LEND_HOUSING without measure filter used to return loan
    counts (Number) — surprising for an LLM asking 'NSW housing loans?'.
    Default is now value (dollars)."""
    resp = await server.latest(dataset_id="LEND_HOUSING", filters={"region": "nsw"})
    assert resp.records[0].unit == "Australian Dollars"
    # NSW quarterly housing loan commitments are in the billions
    assert resp.records[0].value > 1_000_000_000


async def test_wpi_state_level_works():
    """Regression: WPI state-level query 404'd because default TSEST=20 isn't published per-state."""
    resp = await server.latest(
        dataset_id="WPI",
        filters={"region": "nsw"},
    )
    assert len(resp.records) >= 1
    assert resp.records[0].value is not None
    assert resp.records[0].dimensions["region"] == "New South Wales"


async def test_get_data_rejects_unknown_filter_key_on_non_curated():
    """Used to silently drop unknown keys via build_sdmx_key and return
    unfiltered data — a dangerous correctness footgun. Now: ValueError
    listing the valid SDMX dimensions for the dataflow."""
    with pytest.raises(ValueError, match="Unknown filter key"):
        await server.get_data("ALC", filters={"TOTALLY_NOT_A_DIM": "X"})


async def test_query_echo_does_not_include_default_noise():
    """Query echo should reflect what the user asked, not internal default markers."""
    resp = await server.latest(
        dataset_id="LF",
        filters={"region": "nsw", "measure": "unemployment_rate", "sex": "persons"},
    )
    assert resp.query == {"region": "nsw", "measure": "unemployment_rate", "sex": "persons"}
    for k in resp.query:
        assert not k.startswith("_default"), f"default-noise key leaked: {k}"
