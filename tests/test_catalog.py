from io import BytesIO
from pathlib import Path

import httpx
import pytest
import sdmx

from abs_mcp.cache import Cache
from abs_mcp.catalog import (
    describe_from_dsd,
    list_dataflows,
    search,
    search_in_memory,
)
from abs_mcp.client import ABSClient
from abs_mcp.models import DatasetSummary


FIXTURES = Path(__file__).parent / "fixtures"


def _client_with_fixture(db_path: Path, *, dataflows_xml: bytes) -> ABSClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=dataflows_xml)

    return ABSClient(cache=Cache(db_path), transport=httpx.MockTransport(handler))


async def test_list_dataflows_returns_all(tmp_path: Path) -> None:
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        summaries = await list_dataflows(client)
    assert len(summaries) > 100, "ABS catalogue has 1000+ dataflows"
    ids = {s.id for s in summaries}
    assert "LF" in ids and "CPI" in ids
    assert "BA_GCCSA" in ids
    assert "LEND_HOUSING" in ids


async def test_list_dataflows_marks_curated(tmp_path: Path) -> None:
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        summaries = await list_dataflows(client, curated_ids={"LF", "CPI"})
    by_id = {s.id: s for s in summaries}
    assert by_id["LF"].is_curated is True
    assert by_id["CPI"].is_curated is True
    assert by_id["BA_GCCSA"].is_curated is False


def test_search_in_memory_finds_unemployment_lf() -> None:
    sums = [
        DatasetSummary(id="LF", name="Labour Force Survey", description="employment, unemployment, participation"),
        DatasetSummary(id="CPI", name="Consumer Price Index", description="inflation"),
        DatasetSummary(id="OTHER", name="Other", description="unrelated stuff"),
    ]
    matches = search_in_memory(sums, "unemployment", limit=3)
    assert matches[0].id == "LF"


def test_search_in_memory_empty_query_raises() -> None:
    with pytest.raises(ValueError):
        search_in_memory([], "   ", limit=5)


async def test_search_unemployment_finds_lf_in_real_catalogue(tmp_path: Path) -> None:
    """Brief-required test: fuzzy 'unemployment' -> LF in top results."""
    xml = (FIXTURES / "dataflows_min.xml").read_bytes()
    async with _client_with_fixture(tmp_path / "c.db", dataflows_xml=xml) as client:
        results = await search(client, "unemployment", limit=10)
    top_ids = [r.id for r in results]
    assert "LF" in top_ids, f"LF should appear in top 10 for 'unemployment', got: {top_ids}"


def test_describe_from_dsd_lf_returns_dimensions() -> None:
    xml = (FIXTURES / "lf_dsd.xml").read_bytes()
    msg = sdmx.read_sdmx(BytesIO(xml))
    detail = describe_from_dsd("LF", msg)
    assert detail.id == "LF"
    assert detail.is_curated is False
    dim_ids = {d.sdmx_id for d in detail.dimensions}
    assert {"MEASURE", "REGION", "SEX", "TSEST"} <= dim_ids
    measure = next(d for d in detail.dimensions if d.sdmx_id == "MEASURE")
    assert len(measure.values) > 0
