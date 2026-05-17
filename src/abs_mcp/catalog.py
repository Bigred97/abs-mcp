"""Dataflow listing, fuzzy search, and SDMX-to-plain-English description.

`describe_from_dsd` is the fallback path for non-curated dataflows: it walks
the DSD's dimensions and codelists and produces the same `DatasetDetail`
shape that curated YAMLs do.
"""
from __future__ import annotations

from rapidfuzz import fuzz
from sdmx.message import StructureMessage

from . import curated as curated_mod
from .client import ABSClient
from .curated import CuratedDataflow
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
    """Build a flat list of summaries with the API description as-is.

    Curated `search_keywords` are NOT folded into the description — the
    ranker reads them out-of-band via `_ranker_context` and scores them in
    a separate high-signal pool so long prose can't outweigh them. Keeping
    description clean also tightens the user-facing `search_datasets` payload.

    Curated entries may indirect to a different SDMX dataflow via
    `sdmx_dataflow_id` (e.g. curated `CPI` → SDMX `CPI_Q`). In that case we
    surface the curated display ID (`CPI`) and hide both the indirection
    target (`CPI_Q`, exposed via curated already) and any SDMX dataflow that
    collides with a curated display ID (`CPI`, the legacy combined product
    that customers shouldn't be hitting directly).
    """
    msg = await client.get_dataflows()
    curated_ids = curated_ids or set()
    curated_by_sdmx: dict[str, CuratedDataflow] = {}
    for cid in curated_ids:
        cd = curated_mod.get(cid)
        if cd is not None:
            curated_by_sdmx[cd.sdmx_id] = cd
    summaries: list[DatasetSummary] = []
    emitted: set[str] = set()
    for df in msg.dataflow.values():
        # Drop SDMX entries whose id collides with a curated display id but
        # whose curated entry points elsewhere — otherwise we'd emit two
        # summaries with the same id (one curated, one raw SDMX).
        if df.id in curated_ids and df.id not in curated_by_sdmx:
            continue
        api_desc = description_text(df) or ""
        if df.id in curated_by_sdmx:
            cd = curated_by_sdmx[df.id]
            summaries.append(
                DatasetSummary(
                    id=cd.id,
                    name=cd.name or name_text(df),
                    description=api_desc or None,
                    is_curated=True,
                )
            )
            emitted.add(cd.id)
            continue
        summaries.append(
            DatasetSummary(
                id=df.id,
                name=name_text(df),
                description=api_desc or None,
                is_curated=False,
            )
        )
        emitted.add(df.id)
    # Surface any curated dataset whose SDMX target wasn't in the catalogue
    # (e.g. ABS catalogue temporarily missing it) so search_datasets can
    # always find every curated entry by display name.
    for cid in curated_ids:
        if cid in emitted:
            continue
        cd = curated_mod.get(cid)
        if cd is None:
            continue
        summaries.append(
            DatasetSummary(
                id=cd.id,
                name=cd.name,
                description=cd.description or None,
                is_curated=True,
            )
        )
    return summaries


def _ranker_context(
    curated_ids: set[str] | None,
) -> tuple[dict[str, str], set[str]]:
    """Build the high-signal keyword overlay + deprecated-id set from curated YAMLs.

    Overlay maps display ID → space-joined `search_keywords`. These curated
    terms are the dataset's intentional retrieval anchors and live in the
    high-signal scoring pool.

    Deprecated set contains IDs whose `update_frequency` starts with `ceased`
    (e.g. `RT`, ceased Jul 2025). The ranker applies a penalty so the active
    replacement (`HSI_M`) wins natural-language queries like 'retail' even
    though both share retail-themed keywords.
    """
    overlay: dict[str, str] = {}
    deprecated: set[str] = set()
    for cid in curated_ids or set():
        cd = curated_mod.get(cid)
        if cd is None:
            continue
        if cd.search_keywords:
            overlay[cid] = " ".join(cd.search_keywords)
        if (cd.update_frequency or "").startswith("ceased"):
            deprecated.add(cid)
    return overlay, deprecated


def search_in_memory(
    summaries: list[DatasetSummary],
    query: str,
    limit: int = 10,
    *,
    keyword_overlay: dict[str, str] | None = None,
    deprecated_ids: set[str] | None = None,
) -> list[DatasetSummary]:
    """Fuzzy-search summaries with a high/low-signal pool split.

    The previous single-haystack ranker concatenated `id + name + description`
    and scored it with WRatio. That let long Census API descriptions
    (~1.9k chars for `C21_G02_SA2`) outrank focused curated datasets on
    customer queries like 'retail' or 'population' — long prose racked up
    incidental token matches.

    Two further WRatio quirks made the single-pool design unreliable:
      * `partial_ratio` rewarded substring matches even when the matched
        substring sat inside an unrelated word: WRatio('lending', 'Family
        Blending') == 90 because "lending" is a substring of "Blending".
      * WRatio length-penalises long haystacks, so a focused curated entry
        with a rich keyword bag (LEND_HOUSING) actually scored *lower* than
        a short census-table name that happened to contain the query as a
        substring.

    Fix: split the haystack and use different scorers per pool.
      * high-signal pool = `id + name + curated.search_keywords`
        scored with `token_set_ratio` — token-strict, so "lending" only
        matches a real "lending" token, not "Blending".
      * low-signal pool = API description, scored with `WRatio` (keeps
        typo/fuzzy tolerance for free-text queries) and capped at
        `DESCRIPTION_CAP` so long prose can't dominate.
    plus a `+CURATED_BONUS` and a `-DEPRECATION_PENALTY` for `ceased_*`
    datasets so historical-ceased entries (`RT`) sit below their active
    replacements (`HSI_M`) while still ranking above non-curated SDMX entries.
    """
    if not query.strip():
        raise ValueError(
            "query is required. Try 'unemployment', 'inflation', 'gdp', "
            "'wages', 'population', 'housing', or any other ABS topic."
        )

    overlay = keyword_overlay or {}
    deprecated = deprecated_ids or set()

    # Description contribution caps at 50: well below a clean high-signal
    # token match (100) so a curated dataset's id/name/keywords reliably
    # beats any description-only incidental match. Tuned against the
    # canonical query suite in test_catalog.py.
    DESCRIPTION_CAP = 50
    CURATED_BONUS = 25
    # 30 is sized so a tied-on-high-signal pair of curated dataflows where
    # one is ceased always favours the active replacement: with both at
    # high=100, low=50, +CURATED_BONUS=25, the deprecated one loses by
    # exactly 30. That's the RT-vs-HSI_M scenario for query='retail'.
    DEPRECATION_PENALTY = 30

    # Score every summary against both pools — no top-N truncation. With
    # ~1,200 ABS dataflows × 2 microsecond-scale fuzz calls this stays
    # under a few milliseconds per search, and avoids the regression where
    # a focused curated entry (HSI_M for 'retail') is dropped from a
    # truncated low pool because dozens of census descriptions score
    # higher on the raw query, leaving its low contribution stuck at 0
    # and letting a deprecated sibling (RT) win on description alone.
    candidates: list[tuple[float, float, float, int]] = []
    for idx, s in enumerate(summaries):
        extras = overlay.get(s.id, "")
        high_str = f"{s.id} {s.name} {extras}".strip()
        low_str = s.description or ""
        h = fuzz.token_set_ratio(query, high_str)
        low_raw = fuzz.WRatio(query, low_str) if low_str else 0
        low = min(low_raw, DESCRIPTION_CAP)
        combined = h + low
        adjusted = combined
        if s.is_curated:
            adjusted += CURATED_BONUS
        if s.id in deprecated:
            adjusted -= DEPRECATION_PENALTY
        candidates.append((adjusted, h, combined, idx))

    # Sort: adjusted desc, then high-signal desc (favours datasets that
    # match the high-signal pool over those riding the description-only
    # path), then combined desc.
    candidates.sort(key=lambda t: (-t[0], -t[1], -t[2]))
    # Attach the adjusted score (token_set_ratio + capped WRatio +
    # curated/deprecation bonuses) to each summary so direct-MCP callers
    # can order their UI. The score can technically range above 100 from
    # h(100) + low(50) + CURATED_BONUS(25); clamp to 100 since the
    # gateway and most consumers expect a 0-100 scale.
    return [
        summaries[idx].model_copy(
            update={"relevance": round(max(0.0, min(float(adj), 100.0)), 1)}
        )
        for adj, _h, _comb, idx in candidates[:limit]
    ]


async def search(
    client: ABSClient,
    query: str,
    limit: int = 10,
    curated_ids: set[str] | None = None,
) -> list[DatasetSummary]:
    summaries = await list_dataflows(client, curated_ids=curated_ids)
    overlay, deprecated = _ranker_context(curated_ids)
    return search_in_memory(
        summaries,
        query,
        limit,
        keyword_overlay=overlay,
        deprecated_ids=deprecated,
    )


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
                values=values,
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
