"""Transform SDMX DataMessages into the denormalised JSON shapes we expose.

Three output shapes:
  - records: flat list of {period, value, dimensions, unit}
  - series:  grouped by dimension key, each with an inner observation list
  - csv:     pipe through sdmx.to_pandas + DataFrame.to_csv

We translate raw SDMX dimension codes to human-readable labels via the DSD's
codelists. For curated datasets, the dim *names* are also remapped to the
plain-English keys defined in the YAML.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import sdmx
from sdmx.message import DataMessage, StructureMessage

from .catalog import TIME_PERIOD, name_text
from .curated import CuratedDataflow
from .models import DataResponse, Observation


def _codelists_by_dim(dsd_msg: StructureMessage, dataset_id: str) -> dict[str, dict[str, str]]:
    """Map dim_id → {sdmx_code: human_label}."""
    if dataset_id not in dsd_msg.structure:
        return {}
    dsd = dsd_msg.structure[dataset_id]
    out: dict[str, dict[str, str]] = {}
    for dim in dsd.dimensions.components:
        if dim.id == TIME_PERIOD:
            continue
        try:
            cl_id = dim.local_representation.enumerated.id
            cl = dsd_msg.codelist[cl_id]
        except (AttributeError, KeyError):
            continue
        out[dim.id] = {code.id: name_text(code) for code in cl.items.values()}
    return out


def _dim_display_names(curated: CuratedDataflow | None) -> dict[str, str]:
    """Map sdmx_dim_id → user-facing dim name (for curated dataflows)."""
    if curated is None:
        return {}
    out: dict[str, str] = {}
    for human_name, dim in curated.dimensions.items():
        if not dim.hidden:
            out[dim.sdmx_id] = human_name
    return out


def _safe_value(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


def to_records(
    msg: DataMessage,
    dsd_msg: StructureMessage,
    dataset_id: str,
    curated: CuratedDataflow | None = None,
) -> list[Observation]:
    series = sdmx.to_pandas(msg)
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    if not isinstance(series, pd.Series) or series.empty:
        return []

    code_labels = _codelists_by_dim(dsd_msg, dataset_id)
    dim_display = _dim_display_names(curated)

    df = series.reset_index()
    columns = list(df.columns)
    value_col_idx = len(columns) - 1
    try:
        time_idx = columns.index(TIME_PERIOD)
    except ValueError:
        time_idx = -1
    dim_cols = [
        (i, col, dim_display.get(col, col.lower()), code_labels.get(col, {}))
        for i, col in enumerate(columns[:-1])
        if col != TIME_PERIOD
    ]

    records: list[Observation] = []
    for row in df.itertuples(index=False, name=None):
        period = str(row[time_idx]) if time_idx >= 0 else ""
        value = _safe_value(row[value_col_idx])
        dims = {
            display_name: labels.get(str(row[i]), str(row[i]))
            for i, _col, display_name, labels in dim_cols
        }
        records.append(
            Observation(period=period, value=value, dimensions=dims, unit=None)
        )
    return records


def to_csv(msg: DataMessage) -> str:
    series = sdmx.to_pandas(msg)
    if isinstance(series, pd.Series):
        df = series.reset_index().rename(columns={series.name or 0: "value"})
    else:
        df = series
    return df.to_csv(index=False)


def to_series(
    msg: DataMessage,
    dsd_msg: StructureMessage,
    dataset_id: str,
    curated: CuratedDataflow | None = None,
) -> list[dict[str, Any]]:
    records = to_records(msg, dsd_msg, dataset_id, curated=curated)
    grouped: dict[tuple[tuple[str, str], ...], list[dict[str, Any]]] = {}
    for r in records:
        key = tuple(sorted(r.dimensions.items()))
        grouped.setdefault(key, []).append(
            {"period": r.period, "value": r.value}
        )
    out: list[dict[str, Any]] = []
    for key, obs in grouped.items():
        out.append({"dimensions": dict(key), "observations": obs})
    return out


def _dataset_name(dsd_msg: StructureMessage, dataset_id: str) -> str:
    if dataset_id in dsd_msg.structure:
        return name_text(dsd_msg.structure[dataset_id]) or dataset_id
    return dataset_id


def build_response(
    dataset_id: str,
    msg: DataMessage,
    dsd_msg: StructureMessage,
    user_query: dict[str, Any],
    fmt: str,
    abs_url: str,
    curated: CuratedDataflow | None = None,
    start_period: str | None = None,
    end_period: str | None = None,
) -> DataResponse:
    name = curated.name if curated else _dataset_name(dsd_msg, dataset_id)

    if fmt == "csv":
        records: list[Observation] | list[dict[str, Any]] = []
        csv_text = to_csv(msg)
    elif fmt == "series":
        records = to_series(msg, dsd_msg, dataset_id, curated=curated)
        csv_text = None
    else:  # 'records' or anything else
        records = to_records(msg, dsd_msg, dataset_id, curated=curated)
        csv_text = None

    # Compute period bounds from the data when caller didn't pass them
    if (start_period is None or end_period is None) and isinstance(records, list) and records and isinstance(records[0], Observation):
        periods = sorted({r.period for r in records if r.period})  # type: ignore[union-attr]
        start_period = start_period or (periods[0] if periods else None)
        end_period = end_period or (periods[-1] if periods else None)

    return DataResponse(
        dataset_id=dataset_id,
        dataset_name=name,
        query=user_query,
        period={"start": start_period, "end": end_period},
        unit=None,
        records=records,
        csv=csv_text,
        retrieved_at=datetime.now(timezone.utc),
        abs_url=abs_url,
    )
