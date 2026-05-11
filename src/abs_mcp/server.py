"""FastMCP server entrypoint.

Five tools, all thin orchestrators over `client`, `catalog`, `curated`,
and `shaping`. The shared `ABSClient` is created lazily so importing this
module doesn't open the SQLite cache.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastmcp import FastMCP

from . import catalog, curated
from .catalog import describe_from_dsd, list_dataflows, search_in_memory
from .client import ABSAPIError, ABSClient
from .models import DatasetDetail, DatasetSummary, DataResponse
from .shaping import build_response

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


async def _resolve_filters(
    dataset_id: str, filters: dict[str, Any] | None
) -> tuple[curated.CuratedDataflow | None, dict[str, list[str]], dict[str, Any]]:
    """Translate user filters to SDMX codes; return (curated, sdmx_filters, query_for_response)."""
    cd = curated.get(dataset_id)
    user = filters or {}
    if cd is None:
        # Non-curated: caller passes raw SDMX codes; we still wrap into list form
        sdmx_filters: dict[str, list[str]] = {}
        for k, v in user.items():
            sdmx_filters[k] = v if isinstance(v, list) else [str(v)]
        return None, sdmx_filters, dict(user)
    sdmx_filters = curated.translate_filters(cd, user)
    sdmx_filters = curated.apply_defaults(cd, sdmx_filters)
    # Echo back the user's original query plus any auto-applied hidden defaults
    query_for_response = dict(user)
    for dim in cd.dimensions.values():
        if dim.hidden and dim.default is not None and dim.sdmx_id in sdmx_filters:
            # Surface that we applied a default
            query_for_response.setdefault(f"_default_{dim.sdmx_id.lower()}", dim.default)
    return cd, sdmx_filters, query_for_response


@mcp.tool
async def search_datasets(query: str, limit: int = 10) -> list[DatasetSummary]:
    """Fuzzy-search ABS dataflow names and descriptions.

    Returns the top matching dataflows ranked by relevance. Use this when you
    don't know the exact dataset ID — for example, search "unemployment" or
    "house prices".
    """
    if not query or not query.strip():
        raise ValueError("query is required")
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
    dataset_id = dataset_id.upper()
    cd = curated.get(dataset_id)
    if cd is not None:
        from .models import CuratedFilter, CuratedFilterValue
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


async def _get_data_impl(
    dataset_id: str,
    filters: dict[str, Any] | None,
    start_period: str | None,
    end_period: str | None,
    fmt: str,
    last_n: int | None = None,
) -> DataResponse:
    dataset_id = dataset_id.upper()
    client = await _get_client()
    cd, sdmx_filters, user_query_echo = await _resolve_filters(dataset_id, filters)

    # Most calls hit a cached DSD (7-day TTL), so the parallelism mainly helps
    # cold first-uses; either way it costs nothing to gather.
    try:
        dsd_msg = await client.get_datastructure(dataset_id)
    except ABSAPIError as e:
        raise ValueError(
            f"Dataset '{dataset_id}' not found. Try search_datasets to discover valid IDs. ({e})"
        ) from e

    if dataset_id not in dsd_msg.structure:
        raise ValueError(f"DSD for '{dataset_id}' missing in API response")
    dim_order = [d.id for d in dsd_msg.structure[dataset_id].dimensions.components]
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
        fmt=fmt,
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
