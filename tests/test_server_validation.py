"""Server-tool input validation.

Bad inputs (wrong types, malformed IDs, ill-formed filters) must raise
ValueError with an actionable hint *before* any HTTP call. These tests are
unit tests, not live — validation fires before `_get_client()` is reached.
"""
from __future__ import annotations

import pytest

from abs_mcp import server


# ---------- _normalize_dataset_id ----------

def test_normalize_dataset_id_strips_and_uppercases():
    assert server._normalize_dataset_id("  lf  ") == "LF"
    assert server._normalize_dataset_id("CPI") == "CPI"
    assert server._normalize_dataset_id("ana_agg") == "ANA_AGG"


def test_normalize_dataset_id_rejects_non_string():
    for bad in (123, None, ["LF"], True, 4.5, {"id": "LF"}):
        with pytest.raises(ValueError, match="dataset_id must be a string"):
            server._normalize_dataset_id(bad)


def test_normalize_dataset_id_rejects_empty():
    with pytest.raises(ValueError, match="dataset_id is empty"):
        server._normalize_dataset_id("")
    with pytest.raises(ValueError, match="dataset_id is empty"):
        server._normalize_dataset_id("   ")


def test_normalize_dataset_id_rejects_url_unsafe_characters():
    """Stops unencoded user input reaching the URL builder."""
    for bad in ("ABS/EVIL", "ABS DATAFLOW", "ABS?injection", "ABS#frag", "LF;"):
        with pytest.raises(ValueError, match="invalid characters"):
            server._normalize_dataset_id(bad)


# ---------- _validate_filters ----------

def test_validate_filters_accepts_none():
    assert server._validate_filters(None) == {}


def test_validate_filters_accepts_dict():
    assert server._validate_filters({"region": "nsw"}) == {"region": "nsw"}


def test_validate_filters_rejects_string():
    """A common LLM mistake: pass a query string instead of a dict."""
    with pytest.raises(ValueError, match="filters must be a dict"):
        server._validate_filters("region=nsw")


def test_validate_filters_rejects_list():
    with pytest.raises(ValueError, match="filters must be a dict"):
        server._validate_filters(["nsw", "vic"])


def test_validate_filters_rejects_int():
    with pytest.raises(ValueError, match="filters must be a dict"):
        server._validate_filters(42)


# ---------- search_datasets ----------

async def test_search_datasets_rejects_non_string_query():
    with pytest.raises(ValueError, match="query must be a string"):
        await server.search_datasets(123)  # type: ignore[arg-type]


async def test_search_datasets_rejects_list_query():
    with pytest.raises(ValueError, match="query must be a string"):
        await server.search_datasets(["unemployment"])  # type: ignore[arg-type]


async def test_search_datasets_rejects_empty_query():
    with pytest.raises(ValueError, match="query is required"):
        await server.search_datasets("")


async def test_search_datasets_rejects_whitespace_query():
    with pytest.raises(ValueError, match="query is required"):
        await server.search_datasets("   ")


async def test_search_datasets_rejects_negative_limit():
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await server.search_datasets("cpi", limit=-1)


async def test_search_datasets_rejects_zero_limit():
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await server.search_datasets("cpi", limit=0)


async def test_search_datasets_rejects_string_limit():
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        await server.search_datasets("cpi", limit="ten")  # type: ignore[arg-type]


async def test_search_datasets_rejects_bool_limit():
    """bool is an int subclass; reject so True/False don't silently coerce to 1/0."""
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        await server.search_datasets("cpi", limit=True)  # type: ignore[arg-type]


# ---------- describe_dataset ----------

async def test_describe_dataset_rejects_non_string_id():
    with pytest.raises(ValueError, match="dataset_id must be a string"):
        await server.describe_dataset(123)  # type: ignore[arg-type]


async def test_describe_dataset_rejects_none_id():
    with pytest.raises(ValueError, match="dataset_id must be a string"):
        await server.describe_dataset(None)  # type: ignore[arg-type]


async def test_describe_dataset_rejects_empty_id():
    with pytest.raises(ValueError, match="dataset_id is empty"):
        await server.describe_dataset("   ")


async def test_describe_dataset_rejects_id_with_spaces():
    """Used to leak an unencoded space to the URL and surface a malformed
    request URL in the error message."""
    with pytest.raises(ValueError, match="invalid characters"):
        await server.describe_dataset(" CPI WITH SPACE")


# ---------- get_data ----------

async def test_get_data_rejects_non_string_id():
    with pytest.raises(ValueError, match="dataset_id must be a string"):
        await server.get_data(123)  # type: ignore[arg-type]


async def test_get_data_rejects_non_dict_filters():
    with pytest.raises(ValueError, match="filters must be a dict"):
        await server.get_data("LF", filters="region=nsw")  # type: ignore[arg-type]


async def test_get_data_rejects_list_filters():
    with pytest.raises(ValueError, match="filters must be a dict"):
        await server.get_data("LF", filters=["nsw"])  # type: ignore[arg-type]


async def test_get_data_rejects_int_filters():
    with pytest.raises(ValueError, match="filters must be a dict"):
        await server.get_data("LF", filters=42)  # type: ignore[arg-type]


# ---------- latest ----------

async def test_latest_rejects_non_string_id():
    with pytest.raises(ValueError, match="dataset_id must be a string"):
        await server.latest(123)  # type: ignore[arg-type]


async def test_latest_rejects_non_dict_filters():
    with pytest.raises(ValueError, match="filters must be a dict"):
        await server.latest("LF", filters="region=nsw")  # type: ignore[arg-type]


# ---------- get_data: format / period type guards ----------

async def test_get_data_rejects_non_string_format():
    """format used to crash with raw AttributeError on int/bool/list."""
    for bad in (1, True, ["records"], {"fmt": "records"}):
        with pytest.raises(ValueError, match="format must be a string"):
            await server.get_data("LF", format=bad)  # type: ignore[arg-type]


async def test_get_data_rejects_non_string_start_period():
    """start_period=2024 (int) used to crash on `start > end` comparison."""
    with pytest.raises(ValueError, match="start_period must be a string"):
        await server.get_data("LF", start_period=2024)  # type: ignore[arg-type]


async def test_get_data_rejects_non_string_end_period():
    with pytest.raises(ValueError, match="end_period must be a string"):
        await server.get_data("LF", end_period=["2024"])  # type: ignore[arg-type]


# ---------- _resolve_filters: non-curated list coercion ----------

async def test_resolve_filters_non_curated_coerces_list_to_str():
    """Non-curated dataflows accept raw filters. List elements that are not
    strings used to survive into build_sdmx_key and raise a bare TypeError
    from '+'.join(non_strings)."""
    # 'ALC' is a real ABS dataflow (Alcohol consumption); not in our curated set.
    _, sdmx_filters, _ = await server._resolve_filters("ALC", {"REGION": [1, 2]})
    assert sdmx_filters == {"REGION": ["1", "2"]}


async def test_resolve_filters_non_curated_coerces_scalar_to_str():
    _, sdmx_filters, _ = await server._resolve_filters("ALC", {"REGION": 1})
    assert sdmx_filters == {"REGION": ["1"]}


async def test_resolve_filters_non_curated_strips_whitespace():
    """Path-asymmetry fix: non-curated path now strips like the curated path."""
    _, sdmx_filters, _ = await server._resolve_filters("ALC", {"REGION": "  AUS  "})
    assert sdmx_filters == {"REGION": ["AUS"]}


async def test_resolve_filters_non_curated_strips_inside_list():
    _, sdmx_filters, _ = await server._resolve_filters("ALC", {"REGION": [" AUS ", "VIC"]})
    assert sdmx_filters == {"REGION": ["AUS", "VIC"]}


async def test_resolve_filters_non_curated_rejects_empty_list():
    with pytest.raises(ValueError, match="empty list"):
        await server._resolve_filters("ALC", {"REGION": []})


async def test_resolve_filters_non_curated_rejects_empty_value():
    with pytest.raises(ValueError, match="empty value"):
        await server._resolve_filters("ALC", {"REGION": "   "})


async def test_resolve_filters_non_curated_rejects_empty_in_list():
    with pytest.raises(ValueError, match="empty value"):
        await server._resolve_filters("ALC", {"REGION": ["AUS", "  "]})
