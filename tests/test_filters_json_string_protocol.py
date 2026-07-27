"""MCP-protocol-level regression test for the `filters` JSON-string bug.

Every real MCP client sends tool arguments as JSON over the wire. FastMCP
deserializes the JSON-RPC payload and validates each argument against the
tool's Pydantic-derived input schema *before* the tool function body ever
runs. `get_data`, `latest`, and `top_n` all previously declared
`filters: Annotated[dict[str, Any] | None, ...]` — a dict-only schema — so a
client that (correctly, per JSON-RPC/MCP convention for structured params)
sent `filters` as a JSON-encoded string got rejected at the schema-validation
boundary with a raw Pydantic `dict_type` error. The lenient
`_validate_filters()` helper (which already `json.loads()`s a string, with
its own actionable hint on malformed JSON) never even ran.

These tests exercise the tools through `fastmcp.Client`, which round-trips
arguments through the same in-process MCP JSON-RPC/Pydantic validation path a
real client hits — unlike calling `server.get_data(...)` directly with a
Python dict, which bypasses the schema layer entirely and is why this bug
shipped undetected (see tests/test_server_validation.py, which only proves
the *helper* is correct, not that it's reachable).

No network calls: every case here is designed to fail during local
validation (`_validate_filters` JSON parsing, or curated `translate_filters`
filter-key lookup) — both happen before any ABS API client is touched — so
these run in the default (non-live) suite.
"""
from __future__ import annotations

import pytest
from fastmcp import Client

from abs_mcp import server


async def _call(tool: str, arguments: dict) -> str:
    """Call `tool` via the real MCP protocol path and return the error text.

    Raises AssertionError if the call unexpectedly succeeds.
    """
    async with Client(server.mcp) as c:
        result = await c.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error, (
        f"{tool}({arguments!r}) unexpectedly succeeded: {result.data!r}"
    )
    # fastmcp surfaces tool-raised exceptions as text content on the result.
    return " ".join(
        getattr(block, "text", "") for block in (result.content or [])
    ).lower()


# ---------- malformed JSON string: must reach _validate_filters, not a bare schema rejection ----------


async def test_get_data_malformed_json_string_filters_reaches_helper():
    msg = await _call(
        "get_data", {"dataset_id": "LF", "filters": "{not valid json"}
    )
    # This is _validate_filters' own hint (server.py `_validate_filters`).
    # If the schema still rejected the string before the function ran, this
    # text would never appear — we'd see a generic Pydantic dict_type error
    # instead.
    assert "invalid json string" in msg, msg
    assert "dict_type" not in msg, msg


async def test_latest_malformed_json_string_filters_reaches_helper():
    msg = await _call(
        "latest", {"dataset_id": "LF", "filters": "{not valid json"}
    )
    assert "invalid json string" in msg, msg
    assert "dict_type" not in msg, msg


async def test_top_n_malformed_json_string_filters_reaches_helper():
    msg = await _call(
        "top_n",
        {
            "dataset_id": "LF",
            "measure": "unemployment_rate",
            "filters": "{not valid json",
        },
    )
    assert "invalid json string" in msg, msg
    assert "dict_type" not in msg, msg


# ---------- well-formed JSON string: must actually parse and flow into business logic ----------


async def test_get_data_valid_json_string_filters_parses_and_reaches_curated_logic():
    """A syntactically valid JSON string must be json.loads()'d into a real
    dict and handed to the curated filter translator — proven here because
    an unknown filter key inside the JSON string surfaces
    translate_filters' own "Unknown filter" hint, not a schema error and not
    a "must be a JSON object" parse error.
    """
    msg = await _call(
        "get_data",
        {"dataset_id": "LF", "filters": '{"not_a_real_dimension": "x"}'},
    )
    assert "unknown filter" in msg, msg
    assert "dict_type" not in msg, msg


async def test_latest_valid_json_string_filters_parses_and_reaches_curated_logic():
    msg = await _call(
        "latest",
        {"dataset_id": "LF", "filters": '{"not_a_real_dimension": "x"}'},
    )
    assert "unknown filter" in msg, msg
    assert "dict_type" not in msg, msg


async def test_top_n_valid_json_string_filters_parses_and_reaches_curated_logic():
    msg = await _call(
        "top_n",
        {
            "dataset_id": "LF",
            "measure": "unemployment_rate",
            "filters": '{"not_a_real_dimension": "x"}',
        },
    )
    assert "unknown filter" in msg, msg
    assert "dict_type" not in msg, msg
