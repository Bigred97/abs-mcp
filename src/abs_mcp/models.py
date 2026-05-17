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
    # 0-100 score from the two-pool ranker (token_set_ratio + capped WRatio +
    # curated bonus - deprecation penalty). None when entry came from
    # list_dataflows() / list_curated() rather than search_datasets().
    relevance: float | None = None


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


class ReleaseEntry(BaseModel):
    """One upcoming publication on the ABS release calendar.

    Shared shape with `rba-mcp` so the ausdata-api webhook poller doesn't
    need to branch per source.
    """
    release_at: datetime = Field(
        description=(
            "Publication time as an ISO-8601 timestamp with tz offset. ABS "
            "publishes at 11:30 AEST (UTC+10) on weekdays — the offset moves "
            "between +10:00 and +11:00 across DST."
        )
    )
    title: str = Field(
        description=(
            "Publication title as the ABS labels it, e.g. 'Consumer Price "
            "Index, Australia'. Combine with `reference_period` for the full "
            "human-readable name."
        )
    )
    event_type: str = Field(
        default="data_release",
        description=(
            "One of 'data_release', 'policy_decision', 'statement', "
            "'speech'. ABS releases are all 'data_release' — the field is "
            "kept for shape parity with rba-mcp's release_calendar."
        ),
    )
    dataset_id: str | None = Field(
        default=None,
        description=(
            "Curated abs-mcp dataset ID if the publication maps to one "
            "(e.g. 'CPI' for catalogue 6401.0), else null. Use this to "
            "route webhook subscribers per dataset."
        ),
    )
    publication_id: str | None = Field(
        default=None,
        description=(
            "ABS catalogue number like '6401.0' — stable across releases, "
            "useful for deduplication / external joins. Null when the "
            "calendar entry doesn't reference a catalogued product."
        ),
    )
    source_url: str = Field(
        description=(
            "Canonical click-through URL on abs.gov.au for the publication."
        )
    )
    reference_period: str | None = Field(
        default=None,
        description=(
            "The reporting period the release covers, as ABS labels it — "
            "e.g. 'March 2026', 'Q1 2026', 'May 2026'. Used by the gateway "
            "for diff/dedup against the previous poll."
        ),
    )


class ReleaseCalendarResponse(BaseModel):
    """Upcoming ABS publications over a rolling horizon.

    Shape mirrors `rba-mcp.ReleaseCalendarResponse` so the gateway can
    diff/route both sources through the same code path.
    """
    horizon_days: int
    row_count: int
    releases: list[ReleaseEntry] = Field(default_factory=list)
    source: str = "Australian Bureau of Statistics"
    source_url: str = "https://www.abs.gov.au/release-calendar"
    attribution: str = _ABS_ATTRIBUTION
    retrieved_at: datetime
    server_version: str = Field(default_factory=_get_server_version)
    stale: bool = False
    stale_reason: str | None = None
