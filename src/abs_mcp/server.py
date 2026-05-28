"""FastMCP server entrypoint.

Five tools, all thin orchestrators over `client`, `catalog`, `curated`,
and `shaping`. The shared `ABSClient` is created lazily so importing this
module doesn't open the SQLite cache.
"""
from __future__ import annotations

import asyncio
import re
import threading
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from . import curated
from .catalog import _ranker_context, describe_from_dsd, list_dataflows, search_in_memory
from .client import ABSAPIError, ABSClient, get_stale_signal, reset_stale_signal
from .release_calendar import fetch_release_calendar
from .models import (
    CuratedFilter,
    CuratedFilterValue,
    DatasetDetail,
    DatasetSummary,
    DataResponse,
    ReleaseCalendarResponse,
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

# Per-thread client cache. We deliberately avoid a module-global singleton:
# gateways like ausdata-api invoke us from worker threads with fresh asyncio
# loops per call (`asyncio.run()` in a ThreadPoolExecutor worker). A
# module-global client (and an `asyncio.Lock()` alongside it) binds to the
# FIRST loop that creates it; subsequent calls from other threads then trip
# httpx's internal asyncio resources with `RuntimeError: Event loop is
# closed`. A thread-local cache gives each worker its own client bound to
# its own loop while preserving the connection-pool win per-thread.
_thread_local = threading.local()


async def _get_client() -> ABSClient:
    """Return the per-thread client, constructing it on first use.

    Each worker thread gets its own ABSClient bound to its own event loop.
    Required because gateways like ausdata-api invoke us from worker threads
    with fresh asyncio loops per call (`asyncio.run()`); a module-global
    client would bind to the first loop and fail on subsequent calls with
    `RuntimeError: Event loop is closed`.
    """
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = ABSClient()
        _thread_local.client = client
    return client


async def reset_client_for_tests() -> None:
    """Drop the current thread's cached client.

    The server keeps one ABSClient per worker thread (see `_get_client`).
    Tests that span multiple event loops on the same thread must clear it
    between loops or httpx will trip on a closed loop. Resets ONLY the
    current thread's client; other threads are untouched.
    """
    client = getattr(_thread_local, "client", None)
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            pass
        try:
            del _thread_local.client
        except AttributeError:
            pass


def _abs_url(dataset_id: str) -> str:
    return f"https://explore.data.abs.gov.au/?fs[0]=Topic&pg=0&df[id]={dataset_id}&df[ag]=ABS&dq=all"


def _normalize_dataset_id(dataset_id: Any) -> str:
    if not isinstance(dataset_id, str):
        raise ValueError(
            f"dataset_id must be a string, got {type(dataset_id).__name__}. "
            "Search by keyword to discover valid IDs like 'LF', 'CPI', or 'ANA_AGG'."
        )
    normalized = dataset_id.strip().upper()
    if not normalized:
        raise ValueError(
            "dataset_id is empty. Search by keyword to discover IDs like 'LF', 'CPI', or 'ANA_AGG'."
        )
    if not _DATASET_ID_PATTERN.match(normalized):
        raise ValueError(
            f"dataset_id {dataset_id!r} contains invalid characters — "
            "ABS dataflow IDs use only letters, digits, and underscores. "
            "Search by keyword to discover valid IDs."
        )
    return normalized


def _validate_filters(filters: Any) -> dict[str, Any]:
    if filters is None:
        return {}
    if isinstance(filters, str):
        import json as _json
        try:
            filters = _json.loads(filters)
        except _json.JSONDecodeError as exc:
            raise ValueError(
                f"filters must be a JSON object, got invalid JSON string: {exc}. "
                "Example: {\"region\": \"nsw\", \"measure\": \"unemployment_rate\"}."
            ) from exc
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
    # Pre-flight: ABS quarterly CPI (CPI_Q) publishes Seasonally Adjusted
    # only — Original (NSA) and Trend rows simply don't exist in the
    # Data API for the quarterly product. Without this check, a user
    # filtering `CPI` with `adjustment=original` would hit a confusing
    # 404 from ABS. Surface the methodology gap explicitly and point
    # them at CPI_MONTHLY (which DOES publish Original / Trend).
    if cd.id == "CPI" and "TSEST" in sdmx_filters:
        tsest_values = sdmx_filters["TSEST"]
        # SDMX code "20" = Seasonally Adjusted; anything else is Original/Trend/etc.
        bad = [v for v in tsest_values if v != "20"]
        if bad:
            user_adjustment = user.get("adjustment", bad[0])
            raise ValueError(
                f"abs.CPI (quarterly, cat 6401.0) publishes the Seasonally "
                f"Adjusted series only — `adjustment={user_adjustment!r}` "
                f"is not available in ABS's Data API for the quarterly "
                f"product. For Original (NSA) or Trend values, query "
                f"`CPI_MONTHLY` (cat 6484.0) which publishes all three "
                f"time-series treatments at monthly cadence. Alternatively, "
                f"omit the `adjustment` filter on CPI to get the SA "
                f"headline (the figure RBA + Treasury cite as 'CPI')."
            )
    return cd, sdmx_filters, dict(user)


@mcp.tool
async def search_datasets(
    query: Annotated[
        str,
        Field(
            description=(
                "Free-text search query. Matches against dataflow IDs, names, "
                "descriptions, and each curated YAML's search_keywords. "
                "Case-insensitive."
            ),
            examples=["unemployment", "inflation", "gdp", "housing finance", "wage growth"],
        ),
    ],
    limit: Annotated[
        int,
        Field(
            description=(
                "Maximum number of results to return, ranked by relevance. "
                "Curated dataflows get a +25 score bonus so they surface above "
                "ABS's ~800 census tables for common queries."
            ),
            examples=[5, 10, 20],
            ge=1,
            le=100,
        ),
    ] = 10,
) -> list[DatasetSummary]:
    """Fuzzy-search ABS dataflow names, descriptions, and keywords.

    Use this when you don't know the exact dataset ID. The 10 curated dataflows
    (LF, CPI, ANA_AGG, etc.) get a relevance boost so common queries like
    "unemployment" or "gdp" return the right dataset at rank #1 — not one of
    ABS's 800+ census tables that mention these keywords incidentally.

    Examples:
        # Discover which dataflow answers "what's NSW unemployment?"
        results = await search_datasets("unemployment")
        # → [{id: 'LF', name: 'Labour Force', is_curated: True}, ...]

        # Broader topic exploration
        results = await search_datasets("housing", limit=5)
        # → top 5 housing-related dataflows, curated first

    When to use:
        - You have a natural-language question and need to identify the dataset
        - You want to discover what ABS publishes on a topic
        - You're not sure if a topic has a plain-English (curated) mapping yet

    Returns:
        List of DatasetSummary (id, name, description, is_curated), ranked
        by relevance. Curated dataflows surface above raw SDMX dataflows.
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
            f"limit must be a positive integer, got {limit!r} ({type(limit).__name__}). "
            "Use a value between 1 and 100, e.g. limit=10."
        )
    if limit < 1:
        raise ValueError(
            f"limit must be >= 1, got {limit}. Use a value between 1 and 100."
        )
    client = await _get_client()
    curated_id_set = set(curated.list_ids())
    try:
        summaries = await list_dataflows(client, curated_ids=curated_id_set)
    except ABSAPIError as e:
        raise ValueError(
            f"Could not fetch ABS dataflow catalogue: {e}. "
            "The ABS Data API may be temporarily unreachable — retry in a moment. "
            "Meanwhile, the curated dataflow IDs "
            "(LF, CPI, CPI_MONTHLY, WPI, PPI_FD, JV, AWE, ANA_AGG, BA_GCCSA, "
            "LEND_HOUSING, ERP_Q, ABS_ANNUAL_ERP_ASGS2021, HSI_M, RT, "
            "C21_G01_POA, C21_G02_POA, C21_G02_SA2) can still be queried directly."
        ) from e
    # The new two-pool ranker needs the keyword overlay + deprecation set to
    # apply the curated bonus and ceased-dataset penalty. Without these the
    # fuzzy match falls back to id+name only and long Census descriptions
    # drown out curated entries for natural-language queries.
    overlay, deprecated = _ranker_context(curated_id_set)
    return search_in_memory(
        summaries,
        query,
        limit=limit,
        keyword_overlay=overlay,
        deprecated_ids=deprecated,
    )


@mcp.tool
async def describe_dataset(
    dataset_id: Annotated[
        str,
        Field(
            description=(
                "ABS dataflow ID. Use search_datasets to discover, or list_curated "
                "to enumerate the 10 dataflows with plain-English support. "
                "Case-insensitive — 'lf', 'LF', and ' LF ' all resolve to 'LF'."
            ),
            examples=["LF", "CPI", "LEND_HOUSING", "ANA_AGG", "ABS_ANNUAL_ERP_ASGS2021"],
        ),
    ],
) -> DatasetDetail:
    """Describe an ABS dataflow's filter dimensions, value codes, and source.

    For curated dataflows (LF, CPI, ANA_AGG, AWE, BA_GCCSA, ERP_Q, JV,
    LEND_HOUSING, WPI, ABS_ANNUAL_ERP_ASGS2021), returns plain-English
    dimension names + curated value keys + the ABS source URL.

    For other dataflows (~1,200 in total), returns raw SDMX dimensions
    and codelists translated to the same response shape — pass raw SDMX
    codes to get_data when querying these.

    Examples:
        # Curated path — plain-English values
        detail = await describe_dataset("LF")
        # detail.dimensions = [{'name': 'region', 'values': [{'key': 'nsw',
        #   'sdmx_code': '1'}, {'key': 'vic', 'sdmx_code': '2'}, ...]}, ...]

        # Raw path — full SDMX codelist
        detail = await describe_dataset("ALC")  # Apparent Consumption of Alcohol
        # detail.is_curated == False; values are raw SDMX codes

    When to use:
        - Before calling get_data on an unfamiliar dataflow — to discover
          valid filter dim names and value keys
        - To get the canonical source URL on the ABS site
        - To see whether a dataflow is curated (plain-English) or raw SDMX

    Returns:
        DatasetDetail with id, name, description, is_curated flag, the list
        of filter dimensions (name, sdmx_id, values), and abs_url.
    """
    dataset_id = _normalize_dataset_id(dataset_id)
    cd = curated.get(dataset_id)
    if cd is not None:
        dims = []
        hidden_defaults = []
        for human_name, dim in cd.dimensions.items():
            if dim.hidden:
                # Surface what's auto-applied so the LLM can reason about
                # query assumptions (and tell the end user "I assumed
                # 15+ / seasonally adjusted / monthly"). 0.2.12 addition.
                if dim.default is not None:
                    # Look up a human label for the default if one exists
                    # in the values map; otherwise expose just the SDMX code.
                    default_label = None
                    for v in dim.values.values():
                        if v.sdmx_code == dim.default:
                            default_label = v.description
                            break
                    hidden_defaults.append(
                        CuratedFilter(
                            name=human_name,
                            sdmx_id=dim.sdmx_id,
                            description=dim.description,
                            values=[
                                CuratedFilterValue(
                                    key="<default>",
                                    sdmx_code=dim.default,
                                    label=default_label,
                                )
                            ],
                        )
                    )
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
            hidden_defaults=hidden_defaults,
            abs_url=cd.source_url or _abs_url(cd.id),
        )
    client = await _get_client()
    try:
        dsd_msg = await client.get_datastructure(dataset_id)
    except ABSAPIError as e:
        raise ValueError(
            f"Dataset '{dataset_id}' not found ({e}). "
            "Use the search endpoint or search tool to discover valid dataset IDs."
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
    raw_sdmx_key: str | None = None,
    structure_version: str | None = None,
) -> DataResponse:
    """Fetch data from ABS SDMX with optional pass-through mode.

    0.13.7: SDMX pass-through (Christian Klettner / Bizscisolve request
    2026-05-28). When `raw_sdmx_key` is supplied, skip the curated-filter
    translation entirely and forward the key verbatim to the ABS SDMX
    endpoint. This lets analysts who designed a query in ABS Data Explorer
    paste the key directly without learning the renamed dimension values.

    `structure_version` pins the dataflow version (e.g. "1.0.0") so a
    pinned analysis does not break when ABS publishes a new structure
    version. Default behaviour unchanged — ABS serves the latest version
    when no `structure_version` is set.

    Constraint: when `raw_sdmx_key` is set, `filters` must be None.
    Mixing the two would be ambiguous about which translation applies.
    """
    # Reset the graceful-degradation flag at the start of each tool call so
    # we only report staleness introduced by THIS call's fetches.
    reset_stale_signal()
    dataset_id = _normalize_dataset_id(dataset_id)

    # 0.13.7: validate the raw-key path early so we fail fast with a clear
    # message rather than letting the SDMX server reject a malformed key.
    if raw_sdmx_key is not None:
        if filters:
            raise ValueError(
                "Cannot mix `raw_sdmx_key` and `filters`. The pass-through "
                "mode uses the SDMX key verbatim; if you want curated "
                "filters, omit `raw_sdmx_key`. If you want pass-through, "
                "drop the filters and put dimension values in the key."
            )
        if not isinstance(raw_sdmx_key, str) or not raw_sdmx_key.strip():
            raise ValueError(
                f"raw_sdmx_key must be a non-empty string, got {raw_sdmx_key!r}. "
                "Example shape: 'M13.3.1599.10.1+2.M' (dot-separated dimension "
                "codes; `+` joins multiple values per dimension; empty positions "
                "match all values for that dimension)."
            )

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
    # MCP / LLM clients often send a year as a JSON number rather than a
    # string (`start_period=2024` instead of `start_period="2024"`). Coerce
    # int → str so both forms work. Excludes bool (subclass of int in Python)
    # so True/False still raise a clean type error. Parity with rba-mcp 0.1.8.
    if isinstance(start_period, int) and not isinstance(start_period, bool):
        start_period = str(start_period)
    if isinstance(end_period, int) and not isinstance(end_period, bool):
        end_period = str(end_period)
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

    # 0.13.7: pass-through mode skips curated-filter translation. In that
    # mode we still need to fetch the DSD (for response shaping — labels,
    # units) but we don't translate the user's input — the raw key
    # flows straight through to ABS.
    if raw_sdmx_key is not None:
        cd = None
        sdmx_filters = {}
        user_query_echo = {"sdmx_key": raw_sdmx_key}
        if structure_version:
            user_query_echo["structure_version"] = structure_version
    else:
        cd, sdmx_filters, user_query_echo = await _resolve_filters(
            dataset_id, filters
        )

    # Curated dataflows may map their user-facing `id` to a different ABS SDMX
    # dataflow via `sdmx_dataflow_id` — e.g. curated `CPI` resolves to SDMX
    # `CPI_Q`. SDMX queries (DSD + data) MUST use the resolved ID; the response
    # keeps the user-facing `dataset_id` so the customer sees what they asked
    # for.
    sdmx_id = cd.sdmx_id if cd is not None else dataset_id

    try:
        dsd_msg = await client.get_datastructure(
            sdmx_id, version=structure_version
        )
    except ABSAPIError as e:
        raise ValueError(
            f"Dataset '{dataset_id}' not found ({e}). "
            "Use the search endpoint or search tool to discover valid dataset IDs."
        ) from e

    if sdmx_id not in dsd_msg.structure:
        raise ValueError(
            f"DSD for '{dataset_id}' missing in API response. "
            "This usually means the dataflow ID exists but its data-structure "
            "definition is unpublished or moved. Use the search endpoint or "
            "search tool to find the current ID, or the list-curated endpoint "
            "for always-supported dataflows."
        )
    dim_order = [d.id for d in dsd_msg.structure[sdmx_id].dimensions.components]
    # For non-curated dataflows, validate user keys against the DSD here —
    # build_sdmx_key silently drops keys not in dim_order, which previously
    # let a typoed dim name return unfiltered data while the response echoed
    # the typo. Curated path already validates inside translate_filters.
    if cd is None and sdmx_filters and raw_sdmx_key is None:
        valid_dims = [d for d in dim_order if d != "TIME_PERIOD"]
        unknown = [k for k in sdmx_filters if k not in dim_order]
        if unknown:
            raise ValueError(
                f"Unknown filter key(s) {unknown} for dataset '{dataset_id}'. "
                f"Valid SDMX dimensions: {valid_dims}. "
                f"Use the describe endpoint or describe tool to see filter shapes "
                f"for dataset '{dataset_id}'."
            )

    # 0.13.7: in pass-through mode use the raw key verbatim. Otherwise
    # build the key from translated filters as before.
    if raw_sdmx_key is not None:
        sdmx_key = raw_sdmx_key
    else:
        sdmx_key = curated.build_sdmx_key(dim_order, sdmx_filters) or "all"

    try:
        data_msg = await client.get_data(
            sdmx_id,
            key=sdmx_key,
            start_period=start_period,
            end_period=end_period,
            last_n=last_n,
            version=structure_version,
        )
    except ABSAPIError as e:
        # Don't leak the internal SDMX key (e.g. ".10001.10..M") or the upstream
        # URL into the error message — REST callers don't speak SDMX and the
        # internal key shape confuses humans. Echo the user-facing filter
        # dimensions instead, and point to the transport-agnostic describe entry.
        user_dims = sorted((filters or {}).keys()) or None
        dim_hint = (
            f" filters: {user_dims}." if user_dims else " (no filters supplied)."
        )
        # When the ABS API rejects on a 4xx (typically 404 for unknown
        # dimension-code combinations or 422 for malformed query), include
        # a period-format hint if the caller passed a period in a likely
        # mistyped form (e.g. '2024Q1' missing the hyphen, '2024-01' on a
        # quarterly dataflow).
        err_str = str(e)
        period_hint = ""
        if "404" in err_str or "422" in err_str:
            for label, p in (("start_period", start_period), ("end_period", end_period)):
                if not p:
                    continue
                # Catch common mistypes and offer the canonical form.
                if re.fullmatch(r"\d{4}Q[1-4]", p):
                    period_hint += (
                        f" {label}={p!r} likely needs a hyphen: try '{p[:4]}-{p[4:]}' "
                        f"(quarterly format is 'YYYY-Q1' / 'YYYY-Q2' etc.)."
                    )
                elif re.fullmatch(r"\d{4}S[1-2]", p):
                    period_hint += (
                        f" {label}={p!r} likely needs a hyphen: try '{p[:4]}-{p[4:]}' "
                        f"(half-yearly format is 'YYYY-S1' / 'YYYY-S2')."
                    )
                elif re.fullmatch(r"\d{6}", p):
                    period_hint += (
                        f" {label}={p!r} looks like 'YYYYMM' — monthly format requires a "
                        f"hyphen: try '{p[:4]}-{p[4:]}'."
                    )
                elif re.fullmatch(r"\d{4}-\d{2}", p) and cd is not None and cd.id == dataset_id:
                    # Caller passed monthly format. Check if the dataflow is
                    # quarterly (CPI etc) — suggest quarterly format.
                    if "quarterly" in (cd.description or "").lower() or cd.id.endswith("_Q"):
                        q_hint = int(p[5:7])
                        q_num = (q_hint - 1) // 3 + 1 if 1 <= q_hint <= 12 else None
                        if q_num:
                            period_hint += (
                                f" {label}={p!r} is monthly but {dataset_id} is quarterly — "
                                f"try '{p[:4]}-Q{q_num}'."
                            )
        raise ValueError(
            f"Query failed for dataset '{dataset_id}' ({e})."
            f"{dim_hint}{period_hint} "
            "Use the describe endpoint or describe tool to see valid filter values "
            f"for dataset '{dataset_id}'."
        ) from e

    resp = build_response(
        dataset_id=dataset_id,
        msg=data_msg,
        dsd_msg=dsd_msg,
        user_query=user_query_echo,
        fmt=fmt_norm,
        abs_url=cd.source_url if (cd and cd.source_url) else _abs_url(dataset_id),
        curated=cd,
        start_period=start_period,
        end_period=end_period,
        sdmx_id=sdmx_id,
    )

    # 0.13.3: CPI_MONTHLY historical-stitch. ABS rebuilt the monthly CPI in
    # 2025: the new `CPI` dataflow with FREQ=M has data from ~2024-10
    # onwards; the legacy `CPI_M` dataflow (cat 6484.0) has data
    # 2017-09 → 2025-09 and is then frozen. The INDEX codelist is identical
    # between the two (CL_CPI_INDEX_17 ≡ the new CPI INDEX block), so a
    # transparent stitch is a clean record-level merge with NO code
    # translation. Triggers only when:
    #   * customer-facing dataset_id == "CPI_MONTHLY"
    #   * start_period is set AND lexicographically <= "2025-09"
    #   * records output is requested (CSV path skips for now)
    # When triggered we issue a SECOND fetch against `CPI_M` over the same
    # SDMX key with end_period clamped to "2025-09" and prepend its
    # observations to the response so the customer sees one continuous
    # series 2017-09 → today.
    if (
        dataset_id == "CPI_MONTHLY"
        and start_period is not None
        and str(start_period) <= "2025-09"
        and fmt_norm == "records"
    ):
        legacy_end = min(str(end_period) if end_period else "2025-09", "2025-09")
        try:
            legacy_dsd = await client.get_datastructure("CPI_M")
            legacy_msg = await client.get_data(
                "CPI_M",
                key=sdmx_key,
                start_period=start_period,
                end_period=legacy_end,
                last_n=None,
            )
            legacy_resp = build_response(
                dataset_id=dataset_id,
                msg=legacy_msg,
                dsd_msg=legacy_dsd,
                user_query=user_query_echo,
                fmt="records",
                abs_url=cd.source_url if (cd and cd.source_url) else _abs_url(dataset_id),
                curated=cd,
                start_period=start_period,
                end_period=legacy_end,
                sdmx_id="CPI_M",
            )
            # Merge: legacy records (older) + primary records (newer),
            # dedupe by period, sort ascending. Primary wins on period
            # overlap (new dataflow has the canonical revisions).
            primary_periods = {
                r.period for r in resp.records if getattr(r, "period", None)
            }
            merged: list = list(legacy_resp.records or [])
            merged = [
                r for r in merged
                if getattr(r, "period", None) not in primary_periods
            ]
            merged.extend(resp.records)
            merged.sort(key=lambda r: (
                getattr(r, "period", None) is None,
                getattr(r, "period", "") or "",
            ))
            resp.records = merged
            resp.row_count = len(merged)
            # Update the response period range to span the stitched window.
            if merged:
                first_p = next(
                    (r.period for r in merged if getattr(r, "period", None)), None
                )
                last_p = next(
                    (r.period for r in reversed(merged) if getattr(r, "period", None)),
                    None,
                )
                if first_p and last_p and resp.period is not None:
                    resp.period.start = first_p
                    resp.period.end = last_p
        except Exception:
            # Stitch is best-effort. If legacy fetch fails, return primary
            # response unchanged with no error to the customer.
            pass

    # If any fetch in the chain (DSD or data) served a stale-cache fallback
    # because the upstream API was unreachable, propagate it to the response.
    stale, reason = get_stale_signal()
    if stale:
        resp.stale = True
        resp.stale_reason = reason
    return resp


@mcp.tool
async def get_data(
    dataset_id: Annotated[
        str,
        Field(
            description="ABS dataflow ID like 'LF', 'CPI'. Use search_datasets to discover.",
            examples=["LF", "CPI", "LEND_HOUSING", "ANA_AGG"],
        ),
    ],
    filters: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Dimension filters. For curated dataflows: plain-English keys and "
                "values, e.g. {'region': 'nsw', 'measure': 'unemployment_rate'}. "
                "For raw dataflows: SDMX dimension IDs and codes. Pass a list as "
                "the value to query multiple values for a dimension. Whitespace "
                "is stripped; empty list / empty value rejected with a hint."
            ),
            examples=[
                {"region": "nsw", "measure": "unemployment_rate"},
                {"region": ["nsw", "vic", "qld"], "measure": "employed_persons"},
                {"region": "australia", "measure": "change_year"},
            ],
        ),
    ] = None,
    start_period: Annotated[
        str | int | None,
        Field(
            description=(
                "Inclusive start period. Format follows the dataflow's cadence: "
                "annual 'YYYY' (e.g. '2020'), monthly 'YYYY-MM' (e.g. '2024-03'), "
                "quarterly 'YYYY-Q1', half-yearly 'YYYY-S1', daily 'YYYY-MM-DD'. "
                "An int year (e.g. 2024) is also accepted and treated as 'YYYY'. "
                "URL-unsafe characters (?, &, /, etc.) are rejected at the boundary."
            ),
            examples=["2020", "2024-03", "2024-Q1", "2024-S1", 2020],
        ),
    ] = None,
    end_period: Annotated[
        str | int | None,
        Field(
            description="Inclusive end period. Same format as start_period.",
            examples=["2025", "2025-12", "2025-Q4", 2025],
        ),
    ] = None,
    format: Annotated[
        Literal["records", "series", "csv"],
        Field(
            description=(
                "Response shape. 'records' (default): flat list of observations. "
                "'series': observations grouped by dimension key for chart-friendly "
                "shapes. 'csv': returns the table as a CSV string in the `csv` field "
                "with records empty."
            ),
            examples=["records", "series", "csv"],
        ),
    ] = "records",
) -> DataResponse:
    """Query an ABS dataflow and return observations.

    Pass filters and/or a period range — unfiltered queries on large
    dataflows can return tens of thousands of observations.

    Curated dataflows accept plain-English filter keys and values that
    are translated to SDMX codes server-side. For example, on LF:
    `{"region": "nsw", "measure": "unemployment_rate"}` resolves to
    SDMX key `M13.3.1599.20.1.M` with hidden-dim defaults auto-applied.

    Examples:
        # NSW unemployment monthly for 2024
        resp = await get_data(
            "LF",
            filters={"region": "nsw", "measure": "unemployment_rate"},
            start_period="2024",
            end_period="2024-12",
        )
        # → resp.records[0]: period='2024-01', value=4.8, unit='Percent'

        # Multi-state comparison
        resp = await get_data(
            "LF",
            filters={"region": ["nsw","vic","qld"], "measure": "unemployment_rate"},
            start_period="2024",
            format="csv",
        )
        # → resp.csv contains 36 rows (3 states × 12 months)

        # Australia quarterly CPI annual change
        resp = await get_data(
            "CPI",
            filters={"region": "australia", "measure": "change_year"},
            start_period="2020",
        )

    When to use:
        - You want observations over a time range (use latest() for the most-recent only)
        - You want a multi-state or multi-measure comparison via list filters
        - You want a CSV for downstream charting / spreadsheet tools

    Returns:
        DataResponse with records (list of {period, value, dimensions, unit}),
        unit (when homogeneous), period bounds, the resolved query echo,
        the ABS source URL, and the CC-BY 4.0 attribution string.
    """
    return await _get_data_impl(dataset_id, filters, start_period, end_period, format)


@mcp.tool
async def latest(
    dataset_id: Annotated[
        str,
        Field(
            description="ABS dataflow ID. Use search_datasets to discover.",
            examples=["LF", "CPI", "ANA_AGG", "LEND_HOUSING"],
        ),
    ],
    filters: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Dimension filters. For curated dataflows: plain-English keys and "
                "values. Without filters, expect one observation per dimension "
                "combination (often hundreds) — pass at least region + measure for "
                "a clean single number."
            ),
            examples=[
                {"region": "nsw", "measure": "unemployment_rate"},
                {"region": "australia", "measure": "change_year"},
                {"region": "1GSYD", "region_type": "gccsa"},
            ],
        ),
    ] = None,
) -> DataResponse:
    """Return the most recent observation(s) for a dataflow.

    Wraps get_data with lastNObservations=1 and a 15-minute cache TTL
    (vs 1 hour for general data calls). Use this for "what's the current
    X?" questions — it's a cheap, fast call: warm-cache p50 ~22ms,
    cold-cache ~200ms.

    Examples:
        # Latest NSW unemployment rate
        resp = await latest("LF", {"region": "nsw", "measure": "unemployment_rate"})
        # → resp.records[0]: period='2026-03', value=4.61, unit='Percent'

        # Latest Australia headline annual inflation
        resp = await latest("CPI", {"region": "australia", "measure": "change_year"})
        # → resp.records[0]: period='2026-Q1', value=4.6, unit='Percent'

        # Latest Greater Sydney population
        resp = await latest("ABS_ANNUAL_ERP_ASGS2021",
                            {"region": "greater_sydney", "region_type": "gccsa"})
        # → resp.records[0]: period='2025', value=5640000, unit='Persons'

    When to use:
        - You want "the current value" of an indicator (most common workflow)
        - You're answering a "what's the unemployment rate?" style question
        - You want sub-50ms warm-cache latency for chat/agent integration

    Returns:
        DataResponse with one most-recent observation per matched dimension
        combination. Same envelope as get_data.
    """
    # When no filters are supplied, fall back to the YAML-curated
    # `latest_defaults` block (if present). Large register-style dataflows —
    # e.g. Census G02 SA2 (~2,400 regions × 8 measures) — overrun the ABS API
    # when fully fanned out; the default narrows to a sensible 1-row snapshot.
    # Filtered calls bypass this entirely so explicit queries keep working.
    if not filters:
        norm_id = _normalize_dataset_id(dataset_id)
        cd = curated.get(norm_id)
        if cd is not None and cd.latest_defaults:
            filters = dict(cd.latest_defaults)
    return await _get_data_impl(dataset_id, filters, None, None, "records", last_n=1)


@mcp.tool
async def top_n(
    dataset_id: Annotated[
        str,
        Field(
            description=(
                "ABS dataflow ID. Must be a curated dataflow with a 'measure' "
                "dimension. Use the list-curated endpoint or list tool to enumerate."
            ),
            examples=["LF", "CPI", "ERP_Q", "BA_GCCSA", "WPI", "LEND_HOUSING"],
        ),
    ],
    measure: Annotated[
        str,
        Field(
            description=(
                "Plain-English measure key to rank by — one of the curated "
                "measure values for this dataflow. Use the describe endpoint "
                "or describe tool to see available measures."
            ),
            examples=[
                "unemployment_rate",
                "employed_persons",
                "change_year",
                "dwelling_units",
                "wage_price_index",
            ],
        ),
    ],
    n: Annotated[
        int,
        Field(
            description="How many top (or bottom) rows to return.",
            ge=1,
            le=100,
            examples=[5, 10, 20, 50],
        ),
    ] = 10,
    filters: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Optional additional dimension filters, same shape as get_data. "
                "Do NOT include the 'measure' key here — that is supplied via "
                "the `measure` parameter."
            ),
            examples=[
                {"sex": "persons"},
                {"region_type": "gccsa"},
                {"sector": "private"},
            ],
        ),
    ] = None,
    direction: Annotated[
        Literal["top", "bottom"],
        Field(
            description=(
                "'top' returns the N rows with the LARGEST measure values "
                "(highest unemployment_rate, biggest population, etc.). "
                "'bottom' returns the SMALLEST."
            ),
            examples=["top", "bottom"],
        ),
    ] = "top",
) -> DataResponse:
    """Return the N rows with the largest (or smallest) value of a measure.

    Ranks across the most-recent available period only (uses
    lastNObservations=1 under the hood) so the result is a clean
    "top N entities at the latest period" view — not noisy historical highs.

    This is the most common agent workflow: "show me the top 10 X by Y".
    Without this tool, an agent would call get_data, receive the full time
    series, and then sort/slice locally — wasting tokens and turns. top_n
    does the rank server-side and returns only the requested rows.

    Examples:
        # 5 states with the highest current unemployment rate
        top_n("LF", "unemployment_rate", n=5)

        # 10 GCCSAs with the largest populations
        top_n("ABS_ANNUAL_ERP_ASGS2021", "estimated_resident_population",
              n=10, filters={"region_type": "gccsa"})

        # 3 industries with the lowest wage growth
        top_n("WPI", "wage_price_index", n=3, direction="bottom")

    Returns:
        DataResponse with at most `n` records, sorted by `measure` value
        in the requested direction. Other fields (period, unit, attribution)
        match a regular get_data call.
    """
    # Validate inputs that pydantic's runtime can't enforce strictly when the
    # function is called directly (Literal/ge/le are type-checker-only in
    # some FastMCP code paths).
    if not isinstance(measure, str) or not measure.strip():
        raise ValueError(
            "measure is required and must be a non-empty string. "
            "Example: top_n('LF', 'unemployment_rate', n=5). "
            "Use the describe endpoint or describe tool to see available "
            "measure keys for the dataset."
        )
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError(
            f"n must be a positive integer (1-100), got {n!r} ({type(n).__name__}). "
            "Try n=10 (default) or n=5. Valid range: 1-100."
        )
    if n < 1:
        raise ValueError(
            f"n must be >= 1, got {n}. "
            "Try n=10 (default) or n=5. Valid range: 1-100."
        )
    if n > 100:
        raise ValueError(
            f"n must be <= 100, got {n}. "
            "Try n=10 (default) or n=50. Valid range: 1-100."
        )
    if direction not in ("top", "bottom"):
        valid = ["top", "bottom"]
        suggestion = curated._did_you_mean(str(direction).lower(), valid)
        raise ValueError(
            f"direction must be 'top' or 'bottom', got {direction!r}."
            f"{suggestion} "
            "Try direction='top' (default, largest values first) or "
            "direction='bottom' (smallest values first)."
        )

    norm_id = _normalize_dataset_id(dataset_id)
    cd = curated.get(norm_id)
    if cd is None:
        raise ValueError(
            f"top_n requires a curated dataflow with a 'measure' dimension. "
            f"Dataset {dataset_id!r} is not curated. "
            "Enumerate the curated set to see the 10 dataflows that support top_n "
            "(LF, CPI, ANA_AGG, AWE, BA_GCCSA, ERP_Q, JV, LEND_HOUSING, "
            "WPI, ABS_ANNUAL_ERP_ASGS2021)."
        )
    if "measure" not in cd.dimensions or cd.dimensions["measure"].hidden:
        raise ValueError(
            f"Dataset {norm_id!r} does not expose a user-facing 'measure' "
            "dimension. top_n is only meaningful when the dataset has a "
            "measure dimension to filter on. Use the describe endpoint or "
            "describe tool to inspect the dataset's filter schema, or use the "
            "get-data endpoint directly."
        )
    measure_dim = cd.dimensions["measure"]
    measure_keys = sorted(measure_dim.values.keys())
    measure_key = measure.strip()
    if measure_key not in measure_dim.values:
        suggestion = curated._did_you_mean(measure_key, measure_keys)
        shown = ", ".join(measure_keys[:15])
        rest = "..." if len(measure_keys) > 15 else ""
        raise ValueError(
            f"Unknown measure {measure!r} for dataset {norm_id!r}."
            f"{suggestion} "
            f"Try one of: {shown}{rest}. "
            f"Use the describe endpoint or describe tool to see the full "
            f"measure list for dataset {norm_id!r}."
        )

    # Merge `measure` into the user-supplied filters. Reject collision so the
    # caller doesn't silently get a different measure than what they ranked by.
    user_filters = _validate_filters(filters)
    if "measure" in user_filters:
        raise ValueError(
            "Do not pass 'measure' inside filters — supply it via the "
            "`measure` parameter instead. "
            f"Example: top_n({norm_id!r}, {measure!r}, n=10, filters={{...}})."
        )
    merged_filters = dict(user_filters)
    merged_filters["measure"] = measure_key

    # Pull the most-recent observation per dimension combination so the rank
    # is "top N entities at the latest period", not a historical-high view.
    full = await _get_data_impl(norm_id, merged_filters, None, None, "records", last_n=1)
    valid_records = [
        r for r in full.records
        if hasattr(r, "value") and r.value is not None
    ]
    valid_records.sort(
        key=lambda r: r.value,  # type: ignore[union-attr,return-value]
        reverse=(direction == "top"),
    )
    top = valid_records[:n]
    # Preserve the response envelope; replace records and row_count.
    return full.model_copy(update={"records": top, "row_count": len(top)})


@mcp.tool
def list_curated() -> list[str]:
    """List the 10 ABS dataflow IDs with hand-curated plain-English support.

    These are the dataflows where get_data accepts plain-English filter
    keys (`{"region": "nsw"}`) and describe_dataset returns rich
    human-readable metadata. All other ABS dataflows (~1,200) are still
    accessible via get_data with raw SDMX dimension IDs and codes.

    The 10 curated dataflows:
        - LF — Labour Force (unemployment, employment, participation)
        - CPI — Consumer Price Index (inflation)
        - WPI — Wage Price Index (wage growth)
        - AWE — Average Weekly Earnings
        - JV — Job Vacancies
        - BA_GCCSA — Building Approvals (by Greater Capital City)
        - LEND_HOUSING — Lending Indicators / Housing Finance
        - ANA_AGG — National Accounts (GDP)
        - ERP_Q — Estimated Resident Population (quarterly)
        - ABS_ANNUAL_ERP_ASGS2021 — Population (annual; supports SA2/SA3/SA4)

    Example:
        ids = list_curated()
        # → ['ABS_ANNUAL_ERP_ASGS2021', 'ANA_AGG', 'AWE', 'BA_GCCSA', 'CPI',
        #    'ERP_Q', 'JV', 'LEND_HOUSING', 'LF', 'WPI']

    When to use:
        - You want to know which dataflows have plain-English support
        - You're enumerating capabilities programmatically (e.g. building a UI)
        - You're showing users a "supported topics" list

    Returns:
        Sorted list of dataflow IDs. Always 10 entries today.
    """
    return curated.list_ids()


@mcp.tool
async def release_calendar(
    days_ahead: Annotated[
        int,
        Field(
            ge=1,
            le=365,
            description=(
                "Horizon in days. Returns ABS publications scheduled to "
                "release between now and `now + days_ahead`. Default 30 "
                "covers the typical monthly + quarterly cadence."
            ),
            examples=[7, 30, 90],
        ),
    ] = 30,
) -> ReleaseCalendarResponse:
    """Upcoming ABS publication schedule (data releases).

    Scrapes the official ABS release calendar
    (https://www.abs.gov.au/release-calendar/future-releases-calendar) and
    returns each scheduled publication with its release timestamp, title,
    reference period, and — when the title maps to a curated abs-mcp
    dataset — the `dataset_id` an agent can plug into `get_data` or
    `latest`. Curated mappings cover the 10 datasets in `list_curated()`
    plus a handful of commonly-watched non-curated catalogues (Retail
    Trade, International Trade in Goods, etc., where `dataset_id` stays
    null but `publication_id` carries the ABS catalogue number).

    `release_at` is returned with Sydney's local UTC offset (`+10:00` AEST
    or `+11:00` AEDT) — what ABS publishes against. The DST switch is
    naive (month-based), within an hour of correct at the changeover
    boundary; downstream code should treat the offset as authoritative
    rather than re-deriving local time.

    Examples:
        # Next 7 days
        cal = await release_calendar(7)
        for r in cal.releases:
            print(r.release_at, r.title, r.dataset_id)

        # Filter to curated datasets only
        cal = await release_calendar(30)
        curated_releases = [r for r in cal.releases if r.dataset_id]

    When to use:
        - Building a webhook / notification feed (ABS publishes at 11:30 AEST)
        - "What's next from the ABS?" agent answers
        - Pre-warming caches the morning of a known release

    Returns:
        `ReleaseCalendarResponse` — same envelope shape as `rba-mcp`'s
        `release_calendar` for portfolio interop. Sorted ascending by
        `release_at`. `stale=True` + `stale_reason` is set when the live
        HTML scrape failed and a cached payload was served past TTL.
    """
    if not isinstance(days_ahead, int) or isinstance(days_ahead, bool):
        raise ValueError(
            f"days_ahead must be an int, got {type(days_ahead).__name__}. "
            "Try days_ahead=30 for the typical monthly horizon."
        )
    if not (1 <= days_ahead <= 365):
        raise ValueError(
            f"days_ahead must be between 1 and 365, got {days_ahead}. "
            "Use 7 (one week), 30 (default), or 90 (one quarter)."
        )
    client = await _get_client()
    releases, stale, stale_reason = await fetch_release_calendar(
        client._http, client.cache, days_ahead
    )
    return ReleaseCalendarResponse(
        horizon_days=days_ahead,
        row_count=len(releases),
        releases=releases,
        retrieved_at=datetime.now(UTC),
        stale=stale,
        stale_reason=stale_reason,
    )


async def prewarm_curated(
    dataset_ids: list[str] | None = None,
    *,
    max_concurrency: int = 2,
    log: Any = None,
) -> dict[str, str]:
    """Warm the SQLite + Parquet cache for curated datasets with bounded
    concurrency. Designed for gateway / Fly-worker startup so the first
    customer hit doesn't pay cold-fetch latency, WITHOUT the OOM cascade
    that hits when 5+ SDMX parses run simultaneously on a 512MB worker.

    Each cold ABS dataflow parse holds the raw XML response + the sdmx1
    DSD + the pandas frame in memory simultaneously, peaking at
    150-250MB for the larger curated dataflows (ERP, LF_AGE, ITGS,
    BUSINESS_INDICATORS). Five-in-parallel exceeds a 512MB worker; two-
    in-parallel stays under.

    Parameters
    ----------
    dataset_ids:
        Curated dataflow IDs to warm. Defaults to every curated dataset
        (`curated.list_ids()`). Unknown IDs raise ValueError so misspelled
        gateway configs fail loudly at startup.
    max_concurrency:
        Maximum number of dataflows to warm in parallel. Default 2 keeps
        peak resident memory under ~500MB on Fly's 512MB worker. Pass 1
        for strict sequential (safest, slowest), or higher on workers
        with more memory.
    log:
        Optional callable accepting a single string for progress lines
        (e.g. `print`). None silences progress.

    Returns
    -------
    Dict mapping dataset_id → "ok" / error message. Errors are caught
    per-dataset so one failure doesn't abort the rest (a misbehaving
    dataflow shouldn't block startup of the other 20).

    Use from a gateway startup hook (FastAPI lifespan, Fly machine init,
    etc.) instead of fan-out calls to `latest()` per dataset.
    """
    if dataset_ids is None:
        dataset_ids = curated.list_ids()
    # Validate up-front — fail loud if a gateway-side pin is stale or
    # has a typo, rather than silently warming a subset.
    known = set(curated.list_ids())
    unknown = [d for d in dataset_ids if d not in known]
    if unknown:
        raise ValueError(
            f"prewarm_curated received unknown dataset IDs: {unknown}. "
            f"Valid IDs: {sorted(known)}."
        )

    sem = asyncio.Semaphore(max(1, int(max_concurrency)))
    results: dict[str, str] = {}

    async def _warm_one(ds_id: str) -> None:
        async with sem:
            if log:
                log(f"[abs-mcp prewarm] warming {ds_id}")
            try:
                # Use latest() with the curated dataset's own latest_defaults
                # — this is the same path the gateway hits for warm
                # cache. Pop the result; we only care about the cache fill.
                cd = curated.get(ds_id)
                filters: dict[str, Any] | None = None
                if cd is not None and cd.latest_defaults:
                    filters = dict(cd.latest_defaults)
                await latest(dataset_id=ds_id, filters=filters)
                results[ds_id] = "ok"
                if log:
                    log(f"[abs-mcp prewarm] done    {ds_id}")
            except Exception as e:
                # Catch every exception; warming is best-effort. Stash
                # the error string so the caller can audit but don't
                # propagate — a misbehaving dataflow shouldn't block the
                # other 20 from warming.
                msg = f"{type(e).__name__}: {e!s}"[:200]
                results[ds_id] = f"error: {msg}"
                if log:
                    log(f"[abs-mcp prewarm] FAILED  {ds_id}: {msg}")

    await asyncio.gather(*[_warm_one(d) for d in dataset_ids])
    return results


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="abs-mcp",
        description="MCP server for the Australian Bureau of Statistics Data API.",
    )
    parser.add_argument(
        "--warmup",
        action="store_true",
        help=(
            "Warm the curated-dataset cache and exit, instead of starting the "
            "MCP stdio server. Use from gateway startup hooks to avoid the "
            "OOM cascade when multiple SDMX parses run in parallel on memory-"
            "constrained workers. Honours --warmup-concurrency (default 2)."
        ),
    )
    parser.add_argument(
        "--warmup-concurrency",
        type=int,
        default=2,
        help="Max parallel dataflow warms when --warmup is set (default 2).",
    )
    parser.add_argument(
        "--warmup-only",
        type=str,
        default=None,
        help=(
            "Comma-separated curated dataset IDs to warm. Defaults to every "
            "curated dataset. Example: --warmup-only LF,CPI,WPI"
        ),
    )
    args = parser.parse_args()

    if args.warmup:
        ds_ids: list[str] | None = None
        if args.warmup_only:
            ds_ids = [s.strip() for s in args.warmup_only.split(",") if s.strip()]
        results = asyncio.run(prewarm_curated(
            ds_ids,
            max_concurrency=args.warmup_concurrency,
            log=lambda m: print(m, file=sys.stderr, flush=True),
        ))
        fails = {k: v for k, v in results.items() if not v.startswith("ok")}
        if fails:
            print(
                f"[abs-mcp prewarm] {len(fails)} dataflow(s) failed: {sorted(fails)}",
                file=sys.stderr,
            )
        sys.exit(1 if fails else 0)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
