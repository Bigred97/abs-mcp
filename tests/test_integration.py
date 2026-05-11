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
    # Hidden dims like 'frequency' and 'adjustment' should NOT be exposed
    assert "frequency" not in dim_names


async def test_list_curated_returns_seven():
    ids = server.list_curated()
    assert set(ids) == {
        "LF", "CPI", "ABS_ANNUAL_ERP_ASGS2021", "BA_GCCSA", "LEND_HOUSING",
        "WPI", "JV",
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
])
async def test_every_curated_dataflow_returns_useful_record(dataset_id, filters, expect_unit):
    """Every curated dataflow must answer a minimal sensible query — the kind a
    user would actually ask. Catches missing/wrong YAML defaults."""
    resp = await server.latest(dataset_id=dataset_id, filters=filters)
    assert len(resp.records) >= 1, f"{dataset_id} returned no records for {filters}"
    obs = resp.records[0]
    assert obs.value is not None, f"{dataset_id} value is None for {filters}"
    assert obs.unit == expect_unit, f"{dataset_id} unit was {obs.unit!r}, expected {expect_unit!r}"
