"""Hand-curated metadata for the top-N dataflows.

Each YAML in `data/curated/` defines a dataflow's plain-English dimension
names + value mappings to SDMX codes. Hidden dimensions get auto-applied
defaults so users don't have to reason about SDMX boilerplate (FREQ, TSEST).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml
from aus_identity import (
    is_valid_postcode,
    normalize_state,
    postcode_to_state,
)


# Dim names whose values are state/region references. When `translate_filters`
# encounters a value on one of these dims it tries `aus_identity` first so
# "NSW", "nsw", "New South Wales", "AU-VIC", "Tassie", and 4-digit postcodes
# all resolve to the lowercase curated key (`nsw`, `vic`, …).
_STATE_LIKE_DIM_NAMES = frozenset({"region", "state", "state_territory"})


def _did_you_mean(needle: str, haystack: list[str]) -> str:
    """Return a ' Did you mean 'X'?' suffix for a single close match, else ''.

    Uses difflib (stdlib — no extra deps) with a moderate cutoff so a typo
    like 'queensland' → 'qld' is not surfaced (too distant) but 'unemploymnt'
    → 'unemployment_rate' is.
    """
    close = difflib.get_close_matches(needle, haystack, n=1, cutoff=0.6)
    return f" Did you mean '{close[0]}'?" if close else ""

# Real ABS sub-state SDMX codes contain at least one digit (SA2/SA3/SA4 are
# pure digits, GCCSA codes are like '1GSYD'). Requiring a digit rules out
# pure-letter strings — uppercase or lowercase — which on a permissive dim
# are almost always typos of the plain-English curated keys (e.g. 'NSW',
# 'QUEENSLAND', 'queensland'). Those fall through to the existing curated
# "Try one of:" hint instead of being sent to ABS as a raw code and 404ing.
_SDMX_CODE_PATTERN = re.compile(r"^(?=.*\d)[A-Z0-9_\-]+$")


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
    # When True, user-supplied values not in `values` and not in the known-codes
    # set are accepted as raw SDMX codes (validated only against URL safety).
    # Use this for dims whose codelist is too large to curate in full — e.g.
    # ASGS sub-state region codes (~3,000 SA2/SA3/SA4 entries).
    permissive: bool = False


@dataclass(frozen=True)
class CuratedDataflow:
    id: str
    name: str
    description: str
    source_url: str | None
    update_frequency: str | None
    dimensions: dict[str, CuratedDimension]
    search_keywords: tuple[str, ...] = ()  # extra terms folded into the search haystack
    # Plain-English filters merged into a bare `latest()` call (no user filters)
    # to prevent unfiltered SDMX fan-out on large register-style datasets
    # (e.g. Census G02 SA2/POA, ~2,400 regions × 8 measures). Same keys/values
    # users would pass to `latest(filters=...)` — translated through
    # `translate_filters` like a normal user query.
    latest_defaults: dict[str, str] = field(default_factory=dict)


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
            raise ValueError(
                f"Bad value for {name}.{key}: {v!r}. "
                "Each entry under `values:` must be either a string (the SDMX code) "
                "or a dict with `sdmx_code:` (and optional `description:`). "
                f"Example: `{key}: \"TOT\"` or `{key}: {{sdmx_code: TOT, description: Total}}`."
            )
    return CuratedDimension(
        sdmx_id=raw["sdmx_id"],
        description=raw.get("description"),
        values=values,
        hidden=bool(raw.get("hidden", False)),
        default=str(raw["default"]) if "default" in raw else None,
        permissive=bool(raw.get("permissive", False)),
    )


def _load_one(path: Path) -> CuratedDataflow:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    dims = {
        dim_name: _parse_dimension(dim_name, dim_raw)
        for dim_name, dim_raw in (raw.get("dimensions") or {}).items()
    }
    latest_defaults_raw = raw.get("latest_defaults") or {}
    if not isinstance(latest_defaults_raw, dict):
        raise ValueError(
            f"latest_defaults for {raw.get('id')!r} must be a mapping of "
            f"plain-English filter keys to values, got {type(latest_defaults_raw).__name__}."
        )
    latest_defaults = {str(k): str(v) for k, v in latest_defaults_raw.items()}
    return CuratedDataflow(
        id=str(raw["id"]),
        name=str(raw["name"]),
        description=str(raw.get("description", "")),
        source_url=raw.get("source_url"),
        update_frequency=raw.get("update_frequency"),
        dimensions=dims,
        search_keywords=tuple(raw.get("search_keywords") or ()),
        latest_defaults=latest_defaults,
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


def _normalise_state_like(user_dim: str, value: str, valid_keys: list[str]) -> str | None:
    """Try `aus_identity` normalisation when the user value is state-shaped.

    Returns the normalised key (matching one of `valid_keys`) if successful,
    else `None` (caller falls back to existing lookup + suggestion logic).

    Routes:
    - "NSW" / "nsw" / "New South Wales" / "AU-NSW" / "Tassie" → canonical code
    - 4-digit postcode → state code via `postcode_to_state`

    Both routes finally map the canonical 3-letter code to the curated key
    by case-insensitive match against `valid_keys`.
    """
    if user_dim not in _STATE_LIKE_DIM_NAMES:
        return None
    # Postcode route first (digits only). is_valid_postcode is non-raising.
    if value.isdigit() and is_valid_postcode(value):
        try:
            code = postcode_to_state(value)
        except ValueError:
            return None
    else:
        try:
            code = normalize_state(value)
        except ValueError:
            return None
    # Map canonical code (NSW/VIC/...) back to a curated key. Try uppercase
    # first (asic/ato/aihw), then lowercase (abs).
    for key in valid_keys:
        if key.upper() == code:
            return key
    return None


def translate_filters(
    curated: CuratedDataflow, filters: dict[str, str | list[str]]
) -> dict[str, list[str]]:
    """Translate plain-English filters → {sdmx_dim_id: [sdmx_codes]}.

    Multi-value filters (lists) are preserved — the caller joins them with '+'
    when building the SDMX dot-string.
    """
    out: dict[str, list[str]] = {}
    visible_dims = {name: dim for name, dim in curated.dimensions.items() if not dim.hidden}
    for user_dim, user_val in filters.items():
        if user_dim not in visible_dims:
            valid = sorted(visible_dims.keys())
            # Hidden dims are auto-managed via YAML defaults; surfacing them as
            # "Unknown filter" with empty Try-one-of: would be a worse hint.
            if user_dim in curated.dimensions:
                raise ValueError(
                    f"Filter '{user_dim}' is auto-managed for dataset '{curated.id}' "
                    "and cannot be set by users. "
                    f"User-facing filters: {', '.join(valid)}. "
                    f"Try describe_dataset('{curated.id}') to see the full schema."
                )
            suggestion = _did_you_mean(user_dim, valid)
            raise ValueError(
                f"Unknown filter '{user_dim}' for dataset '{curated.id}'."
                f"{suggestion} "
                f"Valid filters: {', '.join(valid)}. "
                f"Try describe_dataset('{curated.id}') to see the full schema."
            )
        dim = visible_dims[user_dim]
        if isinstance(user_val, list):
            if not user_val:
                raise ValueError(
                    f"Filter '{user_dim}' has an empty list. "
                    "Pass at least one value, or omit the filter to query all values."
                )
            values = user_val
        else:
            values = [user_val]
        codes: list[str] = []
        for v in values:
            # Whitespace is never meaningful in a curated value key.
            v_str = str(v).strip()
            if not v_str:
                valid_keys = sorted(dim.values.keys())
                raise ValueError(
                    f"Filter '{user_dim}' has an empty value. "
                    f"Try one of: {', '.join(valid_keys[:15])}"
                    + ("..." if len(valid_keys) > 15 else "")
                    + f". See describe_dataset('{curated.id}') for the full schema."
                )
            if v_str in dim.values:
                codes.append(dim.values[v_str].sdmx_code)
            else:
                # Cross-source normalisation: try aus_identity for state-shaped
                # dims so "NSW", "New South Wales", "AU-NSW", and 4-digit
                # postcodes all resolve to the curated key.
                normalised = _normalise_state_like(
                    user_dim, v_str, sorted(dim.values.keys())
                )
                if normalised is not None:
                    codes.append(dim.values[normalised].sdmx_code)
                    continue
                # Allow raw SDMX codes to pass through (escape hatch).
                known_codes = {cv.sdmx_code for cv in dim.values.values()}
                if v_str in known_codes:
                    codes.append(v_str)
                elif dim.permissive and _SDMX_CODE_PATTERN.match(v_str):
                    # Dim opts in to accepting raw SDMX codes that look like
                    # codes (uppercase + digits + at least one digit). Lets
                    # the ~2,985 ASGS sub-state codes through without us
                    # having to enumerate them.
                    codes.append(v_str)
                else:
                    valid_keys = sorted(dim.values.keys())
                    suggestion = _did_you_mean(v_str, valid_keys)
                    raise ValueError(
                        f"Unknown value '{v}' for filter '{user_dim}' on '{curated.id}'."
                        f"{suggestion} "
                        f"Try one of: {', '.join(valid_keys[:15])}"
                        + ("..." if len(valid_keys) > 15 else "")
                        + f". See describe_dataset('{curated.id}') for the full schema."
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
