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
    msg = await client.get_dataflows()
    curated_ids = curated_ids or set()
    summaries: list[DatasetSummary] = []
    for df in msg.dataflow.values():
        summaries.append(
            DatasetSummary(
                id=df.id,
                name=name_text(df),
                description=description_text(df),
                is_curated=df.id in curated_ids,
            )
        )
    return summaries


def search_in_memory(
    summaries: list[DatasetSummary], query: str, limit: int = 10
) -> list[DatasetSummary]:
    if not query.strip():
        raise ValueError("search query is empty")
    haystack = {
        i: f"{s.id} {s.name} {s.description or ''}" for i, s in enumerate(summaries)
    }
    matches = process.extract(
        query, haystack, scorer=fuzz.WRatio, limit=limit
    )
    return [summaries[idx] for _, _score, idx in matches]


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
