"""Dataflow listing, fuzzy search, and SDMX-to-plain-English description.

`describe_from_dsd` is the fallback path for non-curated dataflows: it walks
the DSD's dimensions and codelists and produces the same `DatasetDetail`
shape that curated YAMLs do.
"""
from __future__ import annotations

from rapidfuzz import fuzz, process
from sdmx.message import StructureMessage

from .client import ABSClient
from .models import (
    CuratedFilter,
    CuratedFilterValue,
    DatasetDetail,
    DatasetSummary,
)

ABS_DETAIL_URL = "https://explore.data.abs.gov.au/?fs[0]=Topic&pg=0&fc=Topic&snb=1&df[ds]=ECONOMY_TOPICS&df[id]={id}&df[ag]=ABS"
ABS_HOMEPAGE = "https://www.abs.gov.au/"
TIME_PERIOD = "TIME_PERIOD"


def description_text(item) -> str | None:
    desc = getattr(item, "description", None)
    if not desc:
        return None
    if hasattr(desc, "localizations"):
        return desc.localizations.get("en") or next(iter(desc.localizations.values()), None)
    return str(desc)


def name_text(item) -> str:
    name = getattr(item, "name", None)
    if name is None:
        return getattr(item, "id", "")
    if hasattr(name, "localizations"):
        return name.localizations.get("en") or next(iter(name.localizations.values()), str(name))
    return str(name)


async def list_dataflows(
    client: ABSClient, curated_ids: set[str] | None = None
) -> list[DatasetSummary]:
    """Build a flat list of summaries. For curated dataflows, concatenate the
    curated YAML's description + search_keywords onto the API description so
    the fuzzy search has rich keywords to match against ('mortgage' → LEND_HOUSING,
    'inflation' → CPI, etc.)."""
    from . import curated as curated_mod  # avoid circular import at module load
    msg = await client.get_dataflows()
    curated_ids = curated_ids or set()
    summaries: list[DatasetSummary] = []
    for df in msg.dataflow.values():
        api_desc = description_text(df) or ""
        if df.id in curated_ids:
            cd = curated_mod.get(df.id)
            if cd is not None:
                extras = " ".join(filter(None, [cd.description, " ".join(cd.search_keywords)]))
                api_desc = f"{api_desc} {extras}".strip()
        summaries.append(
            DatasetSummary(
                id=df.id,
                name=name_text(df),
                description=api_desc or None,
                is_curated=df.id in curated_ids,
            )
        )
    return summaries


def search_in_memory(
    summaries: list[DatasetSummary], query: str, limit: int = 10
) -> list[DatasetSummary]:
    """Fuzzy-search summaries; curated dataflows are boosted so they outrank
    the ~800 ABS census tables that otherwise dominate every common query."""
    if not query.strip():
        raise ValueError(
            "query is required. Try 'unemployment', 'inflation', 'gdp', "
            "'wages', 'population', 'housing', or any other ABS topic."
        )
    haystack = {
        i: f"{s.id} {s.name} {s.description or ''}" for i, s in enumerate(summaries)
    }
    # Pull a wide candidate pool; rerank with curated bonus before truncating.
    pool_size = max(limit * 8, 80)
    matches = process.extract(
        query, haystack, scorer=fuzz.WRatio, limit=pool_size
    )
    CURATED_BONUS = 25  # Score 0-100; +25 reliably moves a moderate match above noise.
    rescored = [
        (score + (CURATED_BONUS if summaries[idx].is_curated else 0), score, idx)
        for _hay, score, idx in matches
    ]
    rescored.sort(key=lambda t: (-t[0], -t[1]))
    return [summaries[idx] for _adj, _score, idx in rescored[:limit]]


async def search(
    client: ABSClient,
    query: str,
    limit: int = 10,
    curated_ids: set[str] | None = None,
) -> list[DatasetSummary]:
    summaries = await list_dataflows(client, curated_ids=curated_ids)
    return search_in_memory(summaries, query, limit)


def describe_from_dsd(dataset_id: str, msg: StructureMessage) -> DatasetDetail:
    """Translate a non-curated dataflow's DSD into a DatasetDetail."""
    if dataset_id not in msg.structure:
        raise KeyError(f"DSD for '{dataset_id}' not found in structure message")
    dsd = msg.structure[dataset_id]
    dims_out: list[CuratedFilter] = []
    for dim in dsd.dimensions.components:
        if dim.id == TIME_PERIOD:
            continue
        cl = None
        try:
            enum = dim.local_representation.enumerated
            if enum is not None and enum.id in msg.codelist:
                cl = msg.codelist[enum.id]
        except AttributeError:
            cl = None
        values: list[CuratedFilterValue] = []
        if cl is not None:
            for code in cl.items.values():
                values.append(
                    CuratedFilterValue(
                        key=code.id,
                        sdmx_code=code.id,
                        label=name_text(code),
                    )
                )
        dims_out.append(
            CuratedFilter(
                name=dim.id.lower(),
                sdmx_id=dim.id,
                description=None,
                values=values[:200],
            )
        )

    name = name_text(dsd)
    description = description_text(dsd) or f"ABS dataflow {dataset_id} ({name}). No curated description available; values are raw SDMX codes."
    return DatasetDetail(
        id=dataset_id,
        name=name,
        description=description,
        is_curated=False,
        dimensions=dims_out,
        abs_url=ABS_HOMEPAGE,
    )
