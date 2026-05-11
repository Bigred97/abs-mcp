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


def _hidden_dim_ids(curated: CuratedDataflow | None) -> set[str]:
    if curated is None:
        return set()
    return {dim.sdmx_id for dim in curated.dimensions.values() if dim.hidden}


def _safe_value(v: Any) -> float | None:
    """Coerce to float; return None for NaN, None, or unparseable values.

    ABS publishes missing-data sentinels in some series; we surface them as
    None so consumers can distinguish 'not reported' from 'zero'.
    """
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
    hidden_ids = _hidden_dim_ids(curated)
    unit_labels = _attribute_codelist(dsd_msg, "UNIT_MEASURE")
    obs_units = _obs_unit_index(msg)  # (series_key_tuple, time_period) -> (unit_label, mult)

    df = series.reset_index()
    columns = list(df.columns)
    value_col_idx = len(columns) - 1
    try:
        time_idx = columns.index(TIME_PERIOD)
    except ValueError:
        time_idx = -1
    # User-facing dimensions exclude hidden curated dims (they're noise — defaults the user didn't ask about).
    dim_cols = [
        (i, col, dim_display.get(col, col.lower()), code_labels.get(col, {}))
        for i, col in enumerate(columns[:-1])
        if col != TIME_PERIOD and col not in hidden_ids
    ]
    # Series key (for unit lookup) still uses the FULL set of dim columns since UNIT_MULT is per-series.
    series_key_idxs = [i for i, col in enumerate(columns[:-1]) if col != TIME_PERIOD]

    records: list[Observation] = []
    for row in df.itertuples(index=False, name=None):
        period = str(row[time_idx]) if time_idx >= 0 else ""
        raw_value = _safe_value(row[value_col_idx])
        series_key = tuple(str(row[i]) for i in series_key_idxs)
        unit_code, mult = obs_units.get((series_key, period), (None, 0))
        scaled_value = raw_value * (10 ** mult) if (raw_value is not None and mult) else raw_value
        unit_label = unit_labels.get(unit_code, unit_code) if unit_code else None
        dims = {
            display_name: labels.get(str(row[i]), str(row[i]))
            for i, _col, display_name, labels in dim_cols
        }
        records.append(
            Observation(period=period, value=scaled_value, dimensions=dims, unit=unit_label)
        )
    return records


def _attribute_codelist(dsd_msg: StructureMessage, attr_id: str) -> dict[str, str]:
    """Map attribute code → human label for a DSD attribute (e.g. UNIT_MEASURE)."""
    for dsd in dsd_msg.structure.values():
        for attr in getattr(dsd.attributes, "components", []):
            if attr.id != attr_id:
                continue
            try:
                cl_id = attr.local_representation.enumerated.id
                cl = dsd_msg.codelist[cl_id]
            except (AttributeError, KeyError):
                return {}
            return {code.id: name_text(code) for code in cl.items.values()}
    return {}


def _obs_unit_index(msg: DataMessage) -> dict[tuple[tuple[str, ...], str], tuple[str | None, int]]:
    """Build (series_key_tuple, time_period) → (unit_measure_code, unit_mult).

    sdmx1 stores UNIT_MEASURE and UNIT_MULT on the Observation.attrib dict but
    `to_pandas` drops them. We rebuild the index by walking the message.
    """
    out: dict[tuple[tuple[str, ...], str], tuple[str | None, int]] = {}
    for ds in msg.data:
        for sk, observations in ds.series.items():
            sk_dims = tuple(str(kv.value) for _dim_id, kv in sk.values.items())
            for obs in observations:
                period = ""
                if hasattr(obs, "dim") and hasattr(obs.dim, "values"):
                    # SDMX observation dim is always the single time period.
                    dim_values = obs.dim.values
                    if dim_values:
                        period = str(next(iter(dim_values.values())).value)
                attrib = getattr(obs, "attrib", {}) or {}
                unit_code = None
                mult = 0
                if "UNIT_MEASURE" in attrib:
                    unit_code = str(attrib["UNIT_MEASURE"].value)
                if "UNIT_MULT" in attrib:
                    try:
                        mult = int(str(attrib["UNIT_MULT"].value))
                    except (ValueError, TypeError):
                        mult = 0
                out[(sk_dims, period)] = (unit_code, mult)
    return out


def to_csv(msg: DataMessage) -> str:
    series = sdmx.to_pandas(msg)
    if isinstance(series, pd.Series):
        df = series.reset_index().rename(columns={series.name or 0: "value"})
    else:
        df = series
    return df.to_csv(index=False)


def _group_records_as_series(records: list[Observation]) -> list[dict[str, Any]]:
    grouped: dict[tuple[tuple[str, str], ...], list[dict[str, Any]]] = {}
    for r in records:
        key = tuple(sorted(r.dimensions.items()))
        grouped.setdefault(key, []).append({"period": r.period, "value": r.value})
    return [{"dimensions": dict(key), "observations": obs} for key, obs in grouped.items()]


def to_series(
    msg: DataMessage,
    dsd_msg: StructureMessage,
    dataset_id: str,
    curated: CuratedDataflow | None = None,
) -> list[dict[str, Any]]:
    return _group_records_as_series(
        to_records(msg, dsd_msg, dataset_id, curated=curated)
    )


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

    # underlying is always the records list; csv/series derive their shape from it
    # without re-parsing the SDMX message a second time.
    underlying = to_records(msg, dsd_msg, dataset_id, curated=curated)
    records: list[Observation] | list[dict[str, Any]]
    if fmt == "csv":
        records = []
        csv_text = to_csv(msg)
    elif fmt == "series":
        records = _group_records_as_series(underlying)
        csv_text = None
    else:  # 'records'
        records = underlying
        csv_text = None

    response_unit: str | None = None
    if underlying:
        units = {o.unit for o in underlying if o.unit}  # type: ignore[union-attr]
        if len(units) == 1:
            response_unit = next(iter(units))

    if (start_period is None or end_period is None) and underlying:
        periods = sorted({o.period for o in underlying if o.period})  # type: ignore[union-attr]
        start_period = start_period or (periods[0] if periods else None)
        end_period = end_period or (periods[-1] if periods else None)

    return DataResponse(
        dataset_id=dataset_id,
        dataset_name=name,
        query=user_query,
        period={"start": start_period, "end": end_period},
        unit=response_unit,
        records=records,
        csv=csv_text,
        retrieved_at=datetime.now(timezone.utc),
        abs_url=abs_url,
    )
