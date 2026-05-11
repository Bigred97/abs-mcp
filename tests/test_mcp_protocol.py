"""End-to-end MCP-protocol smoke test.

Spins up the FastMCP server in-process and exercises each tool through the
real MCP protocol surface. This catches bugs in tool registration, JSON
schema generation, and serialization that unit tests of the Python
functions miss.

Marked `live` because the tools call the real ABS API.
"""
from __future__ import annotations

import json

import pytest
from fastmcp import Client

from abs_mcp import server

pytestmark = pytest.mark.live


@pytest.fixture
async def mcp_client():
    async with Client(server.mcp) as c:
        yield c


async def test_tools_list_exposes_five_tools(mcp_client: Client) -> None:
    tools = await mcp_client.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "search_datasets",
        "describe_dataset",
        "get_data",
        "latest",
        "list_curated",
    }


async def test_each_tool_has_input_schema(mcp_client: Client) -> None:
    tools = await mcp_client.list_tools()
    for t in tools:
        assert t.inputSchema is not None
        assert t.description, f"{t.name} has no description"


async def test_call_list_curated_returns_five() -> None:
    async with Client(server.mcp) as c:
        result = await c.call_tool("list_curated", {})
    payload = result.data
    assert isinstance(payload, list)
    assert set(payload) == {
        "LF", "CPI", "ABS_ANNUAL_ERP_ASGS2021", "BA_GCCSA", "LEND_HOUSING",
        "WPI", "JV", "ANA_AGG", "AWE", "ERP_Q",
    }


async def test_call_search_datasets_returns_summaries() -> None:
    async with Client(server.mcp) as c:
        result = await c.call_tool("search_datasets", {"query": "unemployment", "limit": 5})
    payload = result.data
    assert isinstance(payload, list)
    assert any(item.id == "LF" for item in payload), f"LF should be in results: {payload}"


async def test_call_latest_lf_unemployment_nsw() -> None:
    async with Client(server.mcp) as c:
        result = await c.call_tool(
            "latest",
            {"dataset_id": "LF", "filters": {"region": "nsw", "measure": "unemployment_rate"}},
        )
    payload = result.data
    assert payload.dataset_id == "LF"
    assert len(payload.records) >= 1
    obs = payload.records[0]
    assert obs.value is not None
    assert 0 < obs.value < 30
    assert obs.dimensions["region"] == "New South Wales"


async def test_call_describe_dataset_curated() -> None:
    async with Client(server.mcp) as c:
        result = await c.call_tool("describe_dataset", {"dataset_id": "LF"})
    payload = result.data
    assert payload.is_curated is True
    assert payload.name == "Labour Force"


async def test_unknown_filter_surfaces_helpful_error() -> None:
    async with Client(server.mcp) as c:
        with pytest.raises(Exception) as exc_info:
            await c.call_tool(
                "get_data",
                {"dataset_id": "LF", "filters": {"state": "nsw"}},  # 'state' is wrong, should be 'region'
            )
    msg = str(exc_info.value).lower()
    assert "unknown filter" in msg or "state" in msg, f"Error should hint at the issue: {exc_info.value}"


async def test_unknown_dataset_id_surfaces_helpful_error() -> None:
    async with Client(server.mcp) as c:
        with pytest.raises(Exception) as exc_info:
            await c.call_tool("describe_dataset", {"dataset_id": "TOTALLY_FAKE_ID_12345"})
    msg = str(exc_info.value).lower()
    assert "not found" in msg or "search_datasets" in msg, f"Error should suggest a fix: {exc_info.value}"
