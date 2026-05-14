from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


_ABS_ATTRIBUTION = (
    "Data sourced from the Australian Bureau of Statistics and licensed under "
    "Creative Commons Attribution 4.0 International (CC BY 4.0). "
    "https://www.abs.gov.au/about/copyright-and-creative-commons"
)


def _get_server_version() -> str:
    try:
        from importlib.metadata import version
        return version("abs-mcp")
    except Exception:
        return "0.0.0+unknown"


class DatasetSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    is_curated: bool = False


class CuratedFilterValue(BaseModel):
    key: str
    sdmx_code: str
    label: str | None = None


class CuratedFilter(BaseModel):
    name: str
    sdmx_id: str
    description: str | None = None
    values: list[CuratedFilterValue]


class DatasetDetail(BaseModel):
    id: str
    name: str
    description: str
    is_curated: bool
    dimensions: list[CuratedFilter]
    # Hidden dimensions whose SDMX codes are auto-applied when the user does
    # not pass them — surfaced here so the LLM knows what's being assumed
    # (e.g. for LF: AGE=15+, TSEST=seasonally adjusted, FREQ=monthly).
    # Each entry has the curated dim shape with a single value: the default.
    hidden_defaults: list[CuratedFilter] = Field(default_factory=list)
    abs_url: str


class Observation(BaseModel):
    period: str
    value: float | None
    dimensions: dict[str, str]
    unit: str | None = None


class DataResponse(BaseModel):
    dataset_id: str
    dataset_name: str
    query: dict[str, Any] = Field(default_factory=dict)
    period: dict[str, str | None] = Field(default_factory=lambda: {"start": None, "end": None})
    unit: str | None = None
    row_count: int = Field(default=0, description="Number of observation rows in records.")
    records: list[Observation] | list[dict[str, Any]] = Field(default_factory=list)
    csv: str | None = None
    source: str = "Australian Bureau of Statistics"
    # CC-BY 4.0 requires the attribution to travel WITH the data, not just be
    # reachable via a link. Mirrors rba-mcp's DataResponse.attribution shape.
    attribution: str = _ABS_ATTRIBUTION
    retrieved_at: datetime
    source_url: str = Field(
        description=(
            "Canonical click-through URL. Same value as abs_url; both populated "
            "for backward compat."
        )
    )
    abs_url: str = Field(
        description=(
            "Click-through URL for this dataset's source page. abs-mcp legacy "
            "name — prefer source_url (canonical) for new code. Both fields are "
            "populated identically."
        )
    )
    # Echoed in every response so testers can verify which wheel served the
    # call — uvx caches per-version and stale caches have caused real "is
    # this fixed?" confusion. `pip install -U` / `uvx --refresh` to update.
    server_version: str = Field(default_factory=_get_server_version)
    # Set when the ABS API was unreachable and we served a cached payload
    # past its normal TTL. Agents should surface `stale=True` to end users
    # (e.g. "ABS reported 503; showing data from 12 minutes ago").
    stale: bool = False
    stale_reason: str | None = None
    # Set when `latest()` truncated a large response to a limit. Original
    # row count goes here so agents can detect + surface the cap.
    truncated_at: int | None = None
