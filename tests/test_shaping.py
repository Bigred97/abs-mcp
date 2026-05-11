from io import BytesIO
from pathlib import Path

import pytest
import sdmx

from abs_mcp import curated as curated_mod
from abs_mcp.shaping import build_response, to_csv, to_records, to_series

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def reset_registry():
    curated_mod.reset_registry()
    yield
    curated_mod.reset_registry()


def _load_lf_msgs():
    dsd_msg = sdmx.read_sdmx(BytesIO((FIXTURES / "lf_dsd.xml").read_bytes()))
    data_msg = sdmx.read_sdmx(BytesIO((FIXTURES / "lf_one_obs.xml").read_bytes()))
    return data_msg, dsd_msg


def test_to_records_returns_observations_with_human_labels():
    data_msg, dsd_msg = _load_lf_msgs()
    lf = curated_mod.get("LF")
    records = to_records(data_msg, dsd_msg, "LF", curated=lf)
    assert len(records) > 100
    sample = records[0]
    assert sample.period
    assert sample.value is None or isinstance(sample.value, float)
    # Curated dim display names ('measure', 'sex', 'region') overlay raw IDs.
    assert "measure" in sample.dimensions
    assert "region" in sample.dimensions
    # Codes resolved to labels (not raw codes like "M1" or "1")
    measure_labels = {r.dimensions["measure"] for r in records}
    assert any("Employed" in l or "Unemployed" in l or "Labour" in l for l in measure_labels)
    region_labels = {r.dimensions["region"] for r in records}
    assert any(l in ("New South Wales", "Victoria", "Queensland", "Australia") for l in region_labels)


def test_to_records_non_curated_uses_raw_dim_names():
    data_msg, dsd_msg = _load_lf_msgs()
    records = to_records(data_msg, dsd_msg, "LF", curated=None)
    # Without curation, dim names are lowercased SDMX IDs (measure, sex, age...)
    assert "measure" in records[0].dimensions
    assert "tsest" in records[0].dimensions  # exposed when not curated


def test_to_csv_returns_string_with_header():
    data_msg, dsd_msg = _load_lf_msgs()
    csv = to_csv(data_msg)
    lines = csv.splitlines()
    assert "TIME_PERIOD" in lines[0]
    assert len(lines) > 100


def test_to_series_groups_by_dimensions():
    data_msg, dsd_msg = _load_lf_msgs()
    lf = curated_mod.get("LF")
    series_list = to_series(data_msg, dsd_msg, "LF", curated=lf)
    assert len(series_list) > 0
    sample = series_list[0]
    assert "dimensions" in sample
    assert "observations" in sample
    assert isinstance(sample["observations"], list)


def test_build_response_records_format_populates_period_bounds():
    data_msg, dsd_msg = _load_lf_msgs()
    lf = curated_mod.get("LF")
    resp = build_response(
        dataset_id="LF",
        msg=data_msg,
        dsd_msg=dsd_msg,
        user_query={"region": "nsw"},
        fmt="records",
        abs_url="https://abs.gov.au/...",
        curated=lf,
    )
    assert resp.dataset_id == "LF"
    assert resp.dataset_name == "Labour Force"
    assert resp.query == {"region": "nsw"}
    assert resp.period["start"] is not None
    assert resp.period["end"] is not None
    assert len(resp.records) > 0
    assert resp.csv is None


def test_build_response_csv_format_returns_csv_string():
    data_msg, dsd_msg = _load_lf_msgs()
    lf = curated_mod.get("LF")
    resp = build_response(
        dataset_id="LF",
        msg=data_msg,
        dsd_msg=dsd_msg,
        user_query={},
        fmt="csv",
        abs_url="https://abs.gov.au/...",
        curated=lf,
    )
    assert resp.csv is not None
    assert "TIME_PERIOD" in resp.csv
    assert resp.records == []
