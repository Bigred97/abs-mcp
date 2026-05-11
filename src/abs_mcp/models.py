from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
    records: list[Observation] | list[dict[str, Any]] = Field(default_factory=list)
    csv: str | None = None
    source: str = "Australian Bureau of Statistics"
    retrieved_at: datetime
    abs_url: str
