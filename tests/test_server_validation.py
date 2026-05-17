"""Server-tool input validation.

Bad inputs (wrong types, malformed IDs, ill-formed filters) must raise
ValueError with an actionable hint *before* any HTTP call. These tests are
unit tests, not live — validation fires before `_get_client()` is reached.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from abs_mcp import server


# ---------- _normalize_dataset_id ----------

def test_normalize_dataset_id_strips_and_uppercases():
    assert server._normalize_dataset_id("  lf  ") == "LF"
    assert server._normalize_dataset_id("CPI") == "CPI"
    assert server._normalize_dataset_id("ana_agg") == "ANA_AGG"


def test_normalize_dataset_id_rejects_non_string():
    for bad in (123, None, ["LF"], True, 4.5, {"id": "LF"}):
        with pytest.raises(ValueError, match="dataset_id must be a string"):
            server._normalize_dataset_id(bad)


def test_normalize_dataset_id_rejects_empty():
    with pytest.raises(ValueError, match="dataset_id is empty"):
        server._normalize_dataset_id("")
    with pytest.raises(ValueError, match="dataset_id is empty"):
        server._normalize_dataset_id("   ")


def test_normalize_dataset_id_rejects_url_unsafe_characters():
    """Stops unencoded user input reaching the URL builder."""
    for bad in ("ABS/EVIL", "ABS DATAFLOW", "ABS?injection", "ABS#frag", "LF;"):
        with pytest.raises(ValueError, match="invalid characters"):
            server._normalize_dataset_id(bad)


# ---------- _validate_filters ----------

def test_validate_filters_accepts_none():
    assert server._validate_filters(None) == {}


def test_validate_filters_accepts_dict():
    assert server._validate_filters({"region": "nsw"}) == {"region": "nsw"}


def test_validate_filters_rejects_string():
    """A common LLM mistake: pass a query string instead of a dict."""
    with pytest.raises(ValueError, match="filters must be"):
        server._validate_filters("region=nsw")


def test_validate_filters_rejects_list():
    with pytest.raises(ValueError, match="filters must be"):
        server._validate_filters(["nsw", "vic"])


def test_validate_filters_rejects_int():
    with pytest.raises(ValueError, match="filters must be"):
        server._validate_filters(42)


# ---------- search_datasets ----------

async def test_search_datasets_rejects_non_string_query():
    with pytest.raises(ValueError, match="query must be a string"):
        await server.search_datasets(123)  # type: ignore[arg-type]


async def test_search_datasets_rejects_list_query():
    with pytest.raises(ValueError, match="query must be a string"):
        await server.search_datasets(["unemployment"])  # type: ignore[arg-type]


async def test_search_datasets_rejects_empty_query():
    with pytest.raises(ValueError, match="query is required"):
        await server.search_datasets("")


async def test_search_datasets_rejects_whitespace_query():
    with pytest.raises(ValueError, match="query is required"):
        await server.search_datasets("   ")


async def test_search_datasets_rejects_negative_limit():
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await server.search_datasets("cpi", limit=-1)


async def test_search_datasets_rejects_zero_limit():
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await server.search_datasets("cpi", limit=0)


async def test_search_datasets_rejects_string_limit():
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        await server.search_datasets("cpi", limit="ten")  # type: ignore[arg-type]


async def test_search_datasets_rejects_bool_limit():
    """bool is an int subclass; reject so True/False don't silently coerce to 1/0."""
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        await server.search_datasets("cpi", limit=True)  # type: ignore[arg-type]


# ---------- describe_dataset ----------

async def test_describe_dataset_rejects_non_string_id():
    with pytest.raises(ValueError, match="dataset_id must be a string"):
        await server.describe_dataset(123)  # type: ignore[arg-type]


async def test_describe_dataset_rejects_none_id():
    with pytest.raises(ValueError, match="dataset_id must be a string"):
        await server.describe_dataset(None)  # type: ignore[arg-type]


async def test_describe_dataset_rejects_empty_id():
    with pytest.raises(ValueError, match="dataset_id is empty"):
        await server.describe_dataset("   ")


async def test_describe_dataset_rejects_id_with_spaces():
    """Used to leak an unencoded space to the URL and surface a malformed
    request URL in the error message."""
    with pytest.raises(ValueError, match="invalid characters"):
        await server.describe_dataset(" CPI WITH SPACE")


# ---------- get_data ----------

async def test_get_data_rejects_non_string_id():
    with pytest.raises(ValueError, match="dataset_id must be a string"):
        await server.get_data(123)  # type: ignore[arg-type]


async def test_get_data_rejects_non_dict_filters():
    with pytest.raises(ValueError, match="filters must be"):
        await server.get_data("LF", filters="region=nsw")  # type: ignore[arg-type]


async def test_get_data_rejects_list_filters():
    with pytest.raises(ValueError, match="filters must be"):
        await server.get_data("LF", filters=["nsw"])  # type: ignore[arg-type]


async def test_get_data_rejects_int_filters():
    with pytest.raises(ValueError, match="filters must be"):
        await server.get_data("LF", filters=42)  # type: ignore[arg-type]


# ---------- latest ----------

async def test_latest_rejects_non_string_id():
    with pytest.raises(ValueError, match="dataset_id must be a string"):
        await server.latest(123)  # type: ignore[arg-type]


async def test_latest_rejects_non_dict_filters():
    with pytest.raises(ValueError, match="filters must be"):
        await server.latest("LF", filters="region=nsw")  # type: ignore[arg-type]


# ---------- get_data: format / period type guards ----------

async def test_get_data_rejects_non_string_format():
    """format used to crash with raw AttributeError on int/bool/list."""
    for bad in (1, True, ["records"], {"fmt": "records"}):
        with pytest.raises(ValueError, match="format must be a string"):
            await server.get_data("LF", format=bad)  # type: ignore[arg-type]


async def test_get_data_rejects_list_start_period():
    """A list is still a non-period — must error cleanly."""
    with pytest.raises(ValueError, match="start_period must be a string"):
        await server.get_data("LF", start_period=["2024"])  # type: ignore[arg-type]


async def test_get_data_rejects_non_string_end_period():
    with pytest.raises(ValueError, match="end_period must be a string"):
        await server.get_data("LF", end_period=["2024"])  # type: ignore[arg-type]


async def test_get_data_rejects_bool_for_periods():
    """isinstance(True, int) is True in Python — the 0.2.12 int-coerce
    explicitly excludes bools so True/False still raise a type error
    rather than becoming '1'/'0' periods."""
    for bad in (True, False):
        with pytest.raises(ValueError, match="must be a string"):
            await server.get_data("LF", start_period=bad)  # type: ignore[arg-type]


# ---------- _resolve_filters: non-curated list coercion ----------

async def test_resolve_filters_non_curated_coerces_list_to_str():
    """Non-curated dataflows accept raw filters. List elements that are not
    strings used to survive into build_sdmx_key and raise a bare TypeError
    from '+'.join(non_strings)."""
    # 'ALC' is a real ABS dataflow (Alcohol consumption); not in our curated set.
    _, sdmx_filters, _ = await server._resolve_filters("ALC", {"REGION": [1, 2]})
    assert sdmx_filters == {"REGION": ["1", "2"]}


async def test_resolve_filters_non_curated_coerces_scalar_to_str():
    _, sdmx_filters, _ = await server._resolve_filters("ALC", {"REGION": 1})
    assert sdmx_filters == {"REGION": ["1"]}


async def test_resolve_filters_non_curated_strips_whitespace():
    """Path-asymmetry fix: non-curated path now strips like the curated path."""
    _, sdmx_filters, _ = await server._resolve_filters("ALC", {"REGION": "  AUS  "})
    assert sdmx_filters == {"REGION": ["AUS"]}


async def test_resolve_filters_non_curated_strips_inside_list():
    _, sdmx_filters, _ = await server._resolve_filters("ALC", {"REGION": [" AUS ", "VIC"]})
    assert sdmx_filters == {"REGION": ["AUS", "VIC"]}


async def test_resolve_filters_non_curated_rejects_empty_list():
    with pytest.raises(ValueError, match="empty list"):
        await server._resolve_filters("ALC", {"REGION": []})


async def test_resolve_filters_non_curated_rejects_empty_value():
    with pytest.raises(ValueError, match="empty value"):
        await server._resolve_filters("ALC", {"REGION": "   "})


async def test_resolve_filters_non_curated_rejects_empty_in_list():
    with pytest.raises(ValueError, match="empty value"):
        await server._resolve_filters("ALC", {"REGION": ["AUS", "  "]})


# ---------- URL-injection guard: non-curated filter values & periods ----------

@pytest.mark.parametrize("bad_value", [
    "x?dimensionAtObservation=AllDimensions",  # query-param injection
    "a/b",                                     # extra path segment
    "a&b",                                     # query-string injection
    "a#frag",                                  # fragment truncation
    "a=b",                                     # query-param assignment
    "a.b",                                     # SDMX dim-separator collision
    "a+b",                                     # SDMX multi-value collision
    "a%20b",                                   # percent-encoded space
    "a;b",                                     # semicolon
    "a b",                                     # raw space
])
async def test_resolve_filters_non_curated_rejects_url_injection_values(bad_value):
    """Non-curated filter values flow into the SDMX URL path. Anything outside
    [A-Za-z0-9_-] would either change the request shape or hand the user a
    surprising response."""
    with pytest.raises(ValueError, match="invalid characters"):
        await server._resolve_filters("ALC", {"REGION": bad_value})


async def test_resolve_filters_non_curated_rejects_injection_inside_list():
    with pytest.raises(ValueError, match="invalid characters"):
        await server._resolve_filters("ALC", {"REGION": ["AUS", "a?b"]})


async def test_resolve_filters_non_curated_accepts_valid_sdmx_codes():
    """Sanity: legit SDMX codes with underscores/digits/letters still work."""
    _, sdmx_filters, _ = await server._resolve_filters(
        "ALC", {"MEASURE": "DV5167_FHB", "REGION": ["1", "2", "TOT_FHB"]}
    )
    assert sdmx_filters == {"MEASURE": ["DV5167_FHB"], "REGION": ["1", "2", "TOT_FHB"]}


@pytest.mark.parametrize("bad_period", [
    "2024&format=jsonstat",   # query-param injection
    "2024?foo=bar",            # extra query
    "2024#frag",               # fragment
    "2024/extra",              # path segment
    "2024=evil",               # assignment
    "2024+x",                  # plus
    "May 2024",                # space
    "2024,2025",               # comma
])
async def test_get_data_rejects_url_injection_in_start_period(bad_period):
    """start_period lands in the URL query string. URL-unsafe chars must be
    rejected at the boundary, not handed to ABS."""
    with pytest.raises(ValueError, match="invalid characters"):
        await server.get_data("LF", start_period=bad_period)


async def test_get_data_rejects_url_injection_in_end_period():
    with pytest.raises(ValueError, match="invalid characters"):
        await server.get_data("LF", end_period="2024&injected=1")


async def test_get_data_accepts_valid_period_formats():
    """Sanity: the period guard must NOT reject the four ABS-valid shapes.
    We can't run a real query in a unit test, so just confirm the validation
    layer passes them through (we expect a later error from cd lookup or
    network, but NOT the period-shape ValueError)."""
    valid = ["2024", "2024-03", "2024-Q1", "2024-S1", "2024-12-31"]
    for p in valid:
        # Should NOT raise "invalid characters". May raise something else later.
        try:
            await server.get_data("LF_NONEXISTENT_DATASET_FOR_TEST", start_period=p)
        except ValueError as e:
            assert "invalid characters" not in str(e), (
                f"period {p!r} should have passed the URL-safety guard: {e}"
            )


# ---------- 0.2.14: actionable hints in server-level ValueErrors ----------

async def test_search_datasets_limit_type_error_suggests_valid_range():
    """Regression (0.2.14): the 'limit must be a positive integer' message
    must include a concrete worked example so the LLM can self-correct."""
    with pytest.raises(ValueError, match=r"limit=10"):
        await server.search_datasets("cpi", limit="ten")  # type: ignore[arg-type]


# ---------- 0.9.3: error-message scrubbing for transport-agnostic hints ----------

# Forbidden substrings that previously leaked into REST-customer-facing errors.
_LEAK_FORBIDDEN = (
    "api.abs.gov.au",       # internal upstream host
    "data.api.abs.gov.au",  # internal upstream host (full)
    "describe_dataset(",    # MCP-only tool name with call syntax
)

# At least one helpful-hint marker every error must carry.
_HINT_MARKERS = ("Try", "try ", "Did you mean", "Valid", "valid ", "Use the",
                 "Example", "example", "Pass ", "pass at ", "or ")


def _assert_scrubbed_and_helpful(msg: str) -> None:
    for bad in _LEAK_FORBIDDEN:
        assert bad not in msg, (
            f"Leak: error contains forbidden substring {bad!r}: {msg}"
        )
    assert any(m in msg for m in _HINT_MARKERS), (
        f"No helpful hint found in error: {msg}"
    )


async def test_value_errors_dont_leak_upstream_urls_query_failure():
    """Reproduces the original audit case: bad period → ABS API 422.
    Previously leaked '.../data/ABS,CPI/.10001.10..M?startPeriod=not-a-date'."""
    with pytest.raises(ValueError) as exc:
        await server._get_data_impl("CPI", None, "not-a-date", None, "records")
    _assert_scrubbed_and_helpful(str(exc.value))


async def test_value_errors_dont_leak_unknown_filter_curated():
    """Curated-dataflow unknown-filter path used to say 'Try describe_dataset(X)'."""
    with pytest.raises(ValueError) as exc:
        await server._get_data_impl(
            "LF", {"not_a_real_dim": "nsw"}, None, None, "records"
        )
    _assert_scrubbed_and_helpful(str(exc.value))


async def test_value_errors_dont_leak_unknown_value_curated():
    """Curated-dataflow unknown-value path used to suffix '.See describe_dataset(X) ...'."""
    with pytest.raises(ValueError) as exc:
        await server._get_data_impl(
            "LF", {"region": "atlantis"}, None, None, "records"
        )
    _assert_scrubbed_and_helpful(str(exc.value))


async def test_value_errors_dont_leak_unknown_measure_top_n():
    """top_n unknown-measure path used to end '.See describe_dataset(X) ...'."""
    with pytest.raises(ValueError) as exc:
        await server.top_n("LF", measure="not_a_real_measure", n=5)
    _assert_scrubbed_and_helpful(str(exc.value))


async def test_value_errors_dont_leak_unknown_dataset_describe():
    """describe_dataset failure used to embed the upstream URL via ABSAPIError."""
    # A syntactically valid but non-existent dataset id triggers the ABSAPIError
    # path inside describe_dataset → wrapped into ValueError.
    with pytest.raises(ValueError) as exc:
        await server.describe_dataset("ZZ_NOT_A_REAL_DATAFLOW_XYZ")
    _assert_scrubbed_and_helpful(str(exc.value))


async def test_absapierror_message_does_not_embed_url():
    """Unit-test the underlying ABSAPIError text — it must not embed the URL,
    since downstream ValueErrors interpolate it via f'... ({e})'."""
    import httpx

    from abs_mcp.client import ABSClient

    # Mock transport returns 422 for any request — same as ABS's response on
    # the bad-period reproducer. The handler signature accepts the request.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="bad request")

    transport = httpx.MockTransport(handler)
    client = ABSClient(transport=transport)
    try:
        from abs_mcp.client import ABSAPIError

        with pytest.raises(ABSAPIError) as exc:
            await client.get_data("CPI", key=".10001.10..M")
        msg = str(exc.value)
        # The status code stays (informative + transport-agnostic).
        assert "422" in msg
        # The URL and the internal SDMX key must not appear.
        assert "api.abs.gov.au" not in msg, f"URL leak: {msg}"
        assert ".10001.10..M" not in msg, f"SDMX key leak: {msg}"
        assert "/rest/data/" not in msg, f"URL path leak: {msg}"
    finally:
        await client.aclose()


# ----- transport-agnostic error hints (mirrors rba-mcp's guard) -----
#
# Error messages must not reference MCP-tool names (e.g. `describe_dataset()`,
# `search_datasets()`, `list_curated()`). An error from the abs_mcp package
# should read the same whether the caller is an MCP client, a REST gateway,
# or a Python script calling the functions directly.

_SRC_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "abs_mcp"


def _extract_user_facing_strings() -> list[tuple[pathlib.Path, int, str]]:
    """Walk every .py under src/abs_mcp/, parse the AST, and yield only the
    string arguments to `raise <SomeExc>(...)` calls — these are the strings
    users actually see in error reports.
    """
    out: list[tuple[pathlib.Path, int, str]] = []
    for py in _SRC_ROOT.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            call = node.exc if isinstance(node.exc, ast.Call) else None
            if call is None:
                continue
            for arg in call.args:
                pieces: list[str] = []
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    pieces.append(arg.value)
                elif isinstance(arg, ast.JoinedStr):
                    for v in arg.values:
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            pieces.append(v.value)
                elif isinstance(arg, ast.BinOp):
                    stack: list[ast.AST] = [arg]
                    while stack:
                        cur = stack.pop()
                        if isinstance(cur, ast.Constant) and isinstance(cur.value, str):
                            pieces.append(cur.value)
                        elif isinstance(cur, ast.BinOp):
                            stack.append(cur.left)
                            stack.append(cur.right)
                        elif isinstance(cur, ast.JoinedStr):
                            for v in cur.values:
                                stack.append(v)
                if pieces:
                    out.append((py, node.lineno, "".join(pieces)))
    return out


def test_no_mcp_tool_refs_in_error_strings():
    """No error message references an MCP tool by name
    (`describe_dataset(...)`, `search_datasets(...)`, `list_curated(...)`).
    The hint must suggest what to do (look up valid keys, retry, etc.)
    without naming a specific transport's API surface.
    """
    pat = re.compile(r"\b(describe_dataset|search_datasets|list_curated)\s*\(")
    offenders: list[str] = []
    for path, lineno, text in _extract_user_facing_strings():
        if pat.search(text):
            offenders.append(f"{path.relative_to(_SRC_ROOT.parent.parent)}:{lineno}: {text!r}")
    assert not offenders, (
        "User-facing error messages reference MCP tool names — "
        "these are transport-specific and shouldn't leak through ValueError. "
        "Replace with transport-agnostic hints (e.g. 'See the valid-options list "
        f"for X').\n  {chr(10).join(offenders)}"
    )
