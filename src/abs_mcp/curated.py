"""Hand-curated metadata for the top-N dataflows.

Each YAML in `data/curated/` defines a dataflow's plain-English dimension
names + value mappings to SDMX codes. Hidden dimensions get auto-applied
defaults so users don't have to reason about SDMX boilerplate (FREQ, TSEST).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CuratedValue:
    sdmx_code: str
    description: str | None = None


@dataclass(frozen=True)
class CuratedDimension:
    sdmx_id: str
    description: str | None = None
    values: dict[str, CuratedValue] = field(default_factory=dict)
    hidden: bool = False
    default: str | None = None  # SDMX code applied when user doesn't pass this dim


@dataclass(frozen=True)
class CuratedDataflow:
    id: str
    name: str
    description: str
    source_url: str | None
    update_frequency: str | None
    dimensions: dict[str, CuratedDimension]  # plain-English dim name → CuratedDimension


_REGISTRY: dict[str, CuratedDataflow] | None = None


def _yaml_dir() -> Path:
    """Locate data/curated/ both during dev (repo root) and after install (in wheel)."""
    try:
        ref = resources.files("abs_mcp").joinpath("data/curated")
        if ref.is_dir():
            return Path(str(ref))
    except (ModuleNotFoundError, AttributeError):
        pass
    here = Path(__file__).resolve()
    candidates = [
        here.parent / "data" / "curated",
        here.parent.parent.parent / "data" / "curated",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    raise FileNotFoundError("Could not locate data/curated/ directory")


def _parse_dimension(name: str, raw: dict) -> CuratedDimension:
    values: dict[str, CuratedValue] = {}
    for key, v in (raw.get("values") or {}).items():
        if isinstance(v, str):
            values[key] = CuratedValue(sdmx_code=v)
        elif isinstance(v, dict):
            values[key] = CuratedValue(
                sdmx_code=str(v["sdmx_code"]),
                description=v.get("description"),
            )
        else:
            raise ValueError(f"Bad value for {name}.{key}: {v!r}")
    return CuratedDimension(
        sdmx_id=raw["sdmx_id"],
        description=raw.get("description"),
        values=values,
        hidden=bool(raw.get("hidden", False)),
        default=str(raw["default"]) if "default" in raw else None,
    )


def _load_one(path: Path) -> CuratedDataflow:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    dims = {
        dim_name: _parse_dimension(dim_name, dim_raw)
        for dim_name, dim_raw in (raw.get("dimensions") or {}).items()
    }
    return CuratedDataflow(
        id=str(raw["id"]),
        name=str(raw["name"]),
        description=str(raw.get("description", "")),
        source_url=raw.get("source_url"),
        update_frequency=raw.get("update_frequency"),
        dimensions=dims,
    )


def _load_all() -> dict[str, CuratedDataflow]:
    out: dict[str, CuratedDataflow] = {}
    for path in sorted(_yaml_dir().glob("*.yaml")):
        df = _load_one(path)
        out[df.id] = df
    return out


def get(dataset_id: str) -> CuratedDataflow | None:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return _REGISTRY.get(dataset_id.upper())


def list_ids() -> list[str]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return sorted(_REGISTRY.keys())


def reset_registry() -> None:
    """For tests."""
    global _REGISTRY
    _REGISTRY = None


def translate_filters(
    curated: CuratedDataflow, filters: dict[str, str | list[str]]
) -> dict[str, list[str]]:
    """Translate plain-English filters → {sdmx_dim_id: [sdmx_codes]}.

    Multi-value filters (lists) are preserved — the caller joins them with '+'
    when building the SDMX dot-string.
    """
    out: dict[str, list[str]] = {}
    for user_dim, user_val in filters.items():
        if user_dim not in curated.dimensions:
            valid = sorted(curated.dimensions.keys())
            raise ValueError(
                f"Unknown filter '{user_dim}' for dataset '{curated.id}'. "
                f"Try one of: {', '.join(valid)}"
            )
        dim = curated.dimensions[user_dim]
        values = user_val if isinstance(user_val, list) else [user_val]
        codes: list[str] = []
        for v in values:
            v_str = str(v)
            if v_str in dim.values:
                codes.append(dim.values[v_str].sdmx_code)
            else:
                # Allow raw SDMX codes to pass through (escape hatch).
                known_codes = {cv.sdmx_code for cv in dim.values.values()}
                if v_str in known_codes:
                    codes.append(v_str)
                else:
                    valid_keys = sorted(dim.values.keys())
                    raise ValueError(
                        f"Unknown value '{v}' for filter '{user_dim}' on '{curated.id}'. "
                        f"Try one of: {', '.join(valid_keys[:15])}"
                        + ("..." if len(valid_keys) > 15 else "")
                    )
        out[dim.sdmx_id] = codes
    return out


def apply_defaults(
    curated: CuratedDataflow, sdmx_filters: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Inject hidden-dimension defaults when not set by the user."""
    out = dict(sdmx_filters)
    for dim in curated.dimensions.values():
        if dim.sdmx_id not in out and dim.default is not None:
            out[dim.sdmx_id] = [dim.default]
    return out


def build_sdmx_key(dim_order: list[str], sdmx_filters: dict[str, list[str]]) -> str:
    """Build the SDMX dot-separated key from a DSD's dimension order + filters.

    Empty between dots means 'all values' for that dim.
    """
    parts: list[str] = []
    for dim_id in dim_order:
        if dim_id == "TIME_PERIOD":
            continue
        codes = sdmx_filters.get(dim_id, [])
        parts.append("+".join(codes))
    return ".".join(parts)
