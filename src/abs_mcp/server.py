"""FastMCP server entrypoint.

Five tools, all thin orchestrators over `client`, `catalog`, `curated`,
and `shaping`. The shared `ABSClient` is created lazily so importing this
module doesn't open the SQLite cache.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from fastmcp import FastMCP

from . import catalog, curated
from .catalog import describe_from_dsd, list_dataflows, search_in_memory
from .client import ABSAPIError, ABSClient
from .models import (
    CuratedFilter,
    CuratedFilterValue,
    DatasetDetail,
    DatasetSummary,
    DataResponse,
)
from .shaping import build_response

# ABS dataflow IDs are uppercase letters, digits, and underscores (e.g. LF, BA_GCCSA,
# ABS_ANNUAL_ERP_ASGS2021). We validate so unencoded user input never reaches a URL.
_DATASET_ID_PATTERN = re.compile(r"^[A-Z0-9_]+$")

# Non-curated filter values land directly in the SDMX key (URL path). SDMX codes
# per ABS convention are alphanumeric + underscore (e.g. "TOT", "DV5167_FHB").
# Allow hyphen for future-proofing. Anything else risks URL injection: '?'/'&'/'='
# alter query parameters, '/' adds path segments, '#' truncates, '+' is the multi-
# value separator we own, '.' is the dim separator we own.
_SDMX_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")

# Period codes land in the URL query string. ABS publishes 'YYYY', 'YYYY-MM',
# 'YYYY-Q[1-4]', 'YYYY-S[12]'. We allow any of those plus daily 'YYYY-MM-DD' as
# digits + dash + letters. The regex is permissive on shape (ABS will 4xx on
# semantic garbage) but strict on URL safety.
_PERIOD_SAFE_PATTERN = re.compile(r"^[A-Za-z0-9\-]+$")

mcp = FastMCP("abs-mcp")

_client: ABSClient | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> ABSClient:
    global _client
    async with _client_lock:
        if _client is None:
            _client = ABSClient()
        return _client


async def reset_client_for_tests() -> None:
    """Drop the cached client. The server reuses one ABSClient across all tool
    calls; tests that span multiple event loops must clear it between loops or
    httpx will trip on a closed loop."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None


def _abs_url(dataset_id: str) -> str:
    return f"https://explore.data.abs.gov.au/?fs[0]=Topic&pg=0&df[id]={dataset_id}&df[ag]=ABS&dq=all"


def _normalize_dataset_id(dataset_id: Any) -> str:
    if not isinstance(dataset_id, str):
        raise ValueError(
            f"dataset_id must be a string, got {type(dataset_id).__name__}. "
            "Try search_datasets() to discover valid IDs like 'LF', 'CPI', or 'ANA_AGG'."
        )
    normalized = dataset_id.strip().upper()
    if not normalized:
        raise ValueError(
            "dataset_id is empty. Try search_datasets() to discover IDs like 'LF', 'CPI', or 'ANA_AGG'."
        )
    if not _DATASET_ID_PATTERN.match(normalized):
        raise ValueError(
            f"dataset_id {dataset_id!r} contains invalid characters — "
            "ABS dataflow IDs use only letters, digits, and underscores. "
            "Try search_datasets() to discover valid IDs."
        )
    return normalized


def _validate_filters(filters: Any) -> dict[str, Any]:
    if filters is None:
        return {}
    if not isinstance(filters, dict):
        raise ValueError(
            f"filters must be a dict mapping dimension to value, got {type(filters).__name__}. "
            "Example: {'region': 'nsw', 'measure': 'unemployment_rate'}."
        )
    return filters


async def _resolve_filters(
    dataset_id: str, filters: dict[str, Any] | None
) -> tuple[curated.CuratedDataflow | None, dict[str, list[str]], dict[str, Any]]:
    """Translate user filters to SDMX codes; return (curated, sdmx_filters, query_for_response)."""
    cd = curated.get(dataset_id)
    user = filters or {}
    if cd is None:
        sdmx_filters: dict[str, list[str]] = {}
        for k, v in user.items():
            # Mirror the curated path's contract: coerce to str, strip whitespace,
            # reject empty lists / empty values. build_sdmx_key joins with "+",
            # so a bare non-string would raise TypeError downstream.
            if isinstance(v, list):
                if not v:
                    raise ValueError(
                        f"Filter {k!r} has an empty list. "
                        "Pass at least one value, or omit the filter to query all values."
                    )
                cleaned = [str(x).strip() for x in v]
            else:
                cleaned = [str(v).strip()]
            for c in cleaned:
                if not c:
                    raise ValueError(
                        f"Filter {k!r} has an empty value. "
                        "Pass a non-empty SDMX code, or omit the filter."
                    )
                if not _SDMX_VALUE_PATTERN.match(c):
                    raise ValueError(
                        f"Filter value {c!r} for {k!r} contains invalid characters. "
                        "SDMX codes are alphanumeric + underscore/hyphen (e.g. 'TOT', "
                        "'DV5167_FHB'). For multiple values, pass a list."
                    )
            sdmx_filters[k] = cleaned
        return None, sdmx_filters, dict(user)
    sdmx_filters = curated.translate_filters(cd, user)
    sdmx_filters = curated.apply_defaults(cd, sdmx_filters)
    return cd, sdmx_filters, dict(user)


@mcp.tool
async def search_datasets(query: str, limit: int = 10) -> list[DatasetSummary]:
    """Fuzzy-search ABS dataflow names and descriptions.

    Returns the top matching dataflows ranked by relevance. Use this when you
    don't know the exact dataset ID — for example, search "unemployment" or
    "house prices".
    """
    if not isinstance(query, str):
        raise ValueError(
            f"query must be a string, got {type(query).__name__}. "
            "Try 'unemployment', 'inflation', 'gdp', 'wages', 'population', or 'housing'."
        )
    if not query.strip():
        raise ValueError(
            "query is required. Try 'unemployment', 'inflation', 'gdp', "
            "'wages', 'population', 'housing', or any other ABS topic."
        )
    # bool is a subclass of int — reject explicitly so True/False don't silently coerce.
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(
            f"limit must be a positive integer, got {limit!r} ({type(limit).__name__})."
        )
    if limit < 1:
        raise ValueError(
            f"limit must be >= 1, got {limit}. Use a value between 1 and 100."
        )
    client = await _get_client()
    try:
        summaries = await list_dataflows(client, curated_ids=set(curated.list_ids()))
    except ABSAPIError as e:
        raise ValueError(f"Could not fetch ABS dataflow catalogue: {e}") from e
    return search_in_memory(summaries, query, limit=limit)


@mcp.tool
async def describe_dataset(dataset_id: str) -> DatasetDetail:
    """Describe an ABS dataflow's dimensions, measures, and source.

    For curated datasets (LF, CPI, ABS_ANNUAL_ERP_ASGS2021, BA_GCCSA, LEND_HOUSING),
    returns plain-English dimension names and value mappings. For other dataflows,
    returns raw SDMX dimensions translated to a uniform shape.
    """
    dataset_id = _normalize_dataset_id(dataset_id)
    cd = curated.get(dataset_id)
    if cd is not None:
        dims = []
        for human_name, dim in cd.dimensions.items():
            if dim.hidden:
                continue
            dims.append(
                CuratedFilter(
                    name=human_name,
                    sdmx_id=dim.sdmx_id,
                    description=dim.description,
                    values=[
                        CuratedFilterValue(key=k, sdmx_code=v.sdmx_code, label=v.description)
                        for k, v in dim.values.items()
                    ],
                )
            )
        return DatasetDetail(
            id=cd.id,
            name=cd.name,
            description=cd.description,
            is_curated=True,
            dimensions=dims,
            abs_url=cd.source_url or _abs_url(cd.id),
        )
    client = await _get_client()
    try:
        dsd_msg = await client.get_datastructure(dataset_id)
    except ABSAPIError as e:
        raise ValueError(
            f"Dataset '{dataset_id}' not found. Try search_datasets to discover valid IDs. ({e})"
        ) from e
    return describe_from_dsd(dataset_id, dsd_msg)


_VALID_FORMATS = {"records", "series", "csv"}


async def _get_data_impl(
    dataset_id: str,
    filters: dict[str, Any] | None,
    start_period: str | None,
    end_period: str | None,
    fmt: str,
    last_n: int | None = None,
) -> DataResponse:
    dataset_id = _normalize_dataset_id(dataset_id)
    filters = _validate_filters(filters)
    if fmt is not None and not isinstance(fmt, str):
        raise ValueError(
            f"format must be a string, got {type(fmt).__name__}. "
            f"Valid options: {sorted(_VALID_FORMATS)}."
        )
    fmt_norm = (fmt or "records").lower()
    if fmt_norm not in _VALID_FORMATS:
        raise ValueError(
            f"Unknown format '{fmt}'. Valid options: {sorted(_VALID_FORMATS)}"
        )
    for _name, _v in (("start_period", start_period), ("end_period", end_period)):
        if _v is not None and not isinstance(_v, str):
            raise ValueError(
                f"{_name} must be a string like '2024', '2024-Q1', '2024-03', "
                f"or '2024-S1', got {type(_v).__name__}."
            )
        if _v and not _PERIOD_SAFE_PATTERN.match(_v):
            raise ValueError(
                f"{_name} {_v!r} contains invalid characters. "
                "Period formats: 'YYYY' (annual), 'YYYY-MM' (monthly), "
                "'YYYY-Q1' (quarterly), 'YYYY-S1' (half-yearly)."
            )
    if start_period and end_period and start_period > end_period:
        raise ValueError(
            f"end_period ({end_period}) is before start_period ({start_period}). "
            "Try swapping them. Period formats: monthly 'YYYY-MM', "
            "quarterly 'YYYY-Q*', half-yearly 'YYYY-S*', annual 'YYYY'."
        )
    client = await _get_client()
    cd, sdmx_filters, user_query_echo = await _resolve_filters(dataset_id, filters)

    try:
        dsd_msg = await client.get_datastructure(dataset_id)
    except ABSAPIError as e:
        raise ValueError(
            f"Dataset '{dataset_id}' not found. Try search_datasets to discover valid IDs. ({e})"
        ) from e

    if dataset_id not in dsd_msg.structure:
        raise ValueError(f"DSD for '{dataset_id}' missing in API response")
    dim_order = [d.id for d in dsd_msg.structure[dataset_id].dimensions.components]
    # For non-curated dataflows, validate user keys against the DSD here —
    # build_sdmx_key silently drops keys not in dim_order, which previously
    # let a typoed dim name return unfiltered data while the response echoed
    # the typo. Curated path already validates inside translate_filters.
    if cd is None and sdmx_filters:
        valid_dims = [d for d in dim_order if d != "TIME_PERIOD"]
        unknown = [k for k in sdmx_filters if k not in dim_order]
        if unknown:
            raise ValueError(
                f"Unknown filter key(s) {unknown} for dataset '{dataset_id}'. "
                f"Valid SDMX dimensions: {valid_dims}. "
                f"Try describe_dataset('{dataset_id}') to see filter shapes."
            )
    sdmx_key = curated.build_sdmx_key(dim_order, sdmx_filters) or "all"

    try:
        data_msg = await client.get_data(
            dataset_id,
            key=sdmx_key,
            start_period=start_period,
            end_period=end_period,
            last_n=last_n,
        )
    except ABSAPIError as e:
        raise ValueError(
            f"Query failed for {dataset_id} with key '{sdmx_key}'. "
            f"Try describe_dataset('{dataset_id}') to see valid filter values. ({e})"
        ) from e

    return build_response(
        dataset_id=dataset_id,
        msg=data_msg,
        dsd_msg=dsd_msg,
        user_query=user_query_echo,
        fmt=fmt_norm,
        abs_url=cd.source_url if (cd and cd.source_url) else _abs_url(dataset_id),
        curated=cd,
        start_period=start_period,
        end_period=end_period,
    )


@mcp.tool
async def get_data(
    dataset_id: str,
    filters: dict[str, Any] | None = None,
    start_period: str | None = None,
    end_period: str | None = None,
    format: Literal["records", "series", "csv"] = "records",
) -> DataResponse:
    """Query an ABS dataflow.

    For curated datasets, `filters` accepts plain-English keys and values
    (e.g. `{"region": "nsw", "measure": "unemployment_rate"}`). For other
    dataflows, pass raw SDMX dimension IDs and codes.

    `start_period` / `end_period` accept the dataflow's native period format:
    monthly = 'YYYY-MM', quarterly = 'YYYY-Q1', annual = 'YYYY'. Always pass
    filters or a period range — unfiltered queries can return tens of thousands
    of observations.

    `format`: 'records' (default; flat list), 'series' (grouped by dimensions),
    or 'csv' (returns the table as a CSV string in the `csv` field).
    """
    return await _get_data_impl(dataset_id, filters, start_period, end_period, format)


@mcp.tool
async def latest(
    dataset_id: str,
    filters: dict[str, Any] | None = None,
) -> DataResponse:
    """Return the most recent observation(s) for a dataflow.

    Wraps `get_data` with `lastNObservations=1` and a shorter cache TTL.
    Pass filters to narrow the result — without filters, expect one
    observation per dimension combination (often hundreds).
    """
    return await _get_data_impl(dataset_id, filters, None, None, "records", last_n=1)


@mcp.tool
def list_curated() -> list[str]:
    """List dataflow IDs that have hand-curated plain-English support.

    For these IDs, `describe_dataset` returns a rich human-readable description
    and `get_data` accepts plain-English filter values.
    """
    return curated.list_ids()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
