from pathlib import Path

import httpx
import pytest

from abs_mcp.cache import Cache
from abs_mcp.client import ABSAPIError, ABSClient


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.db"


def _mock_transport(responses: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in responses:
            return responses[url]
        return httpx.Response(404, text=f"No mock for {url}")

    return httpx.MockTransport(handler)


async def test_get_dataflows_caches(db_path: Path) -> None:
    """First call hits HTTP; second call hits cache."""
    fixture = (Path(__file__).parent / "fixtures" / "dataflows_min.xml").read_bytes()
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, content=fixture)

    transport = httpx.MockTransport(handler)
    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=transport) as client:
        msg1 = await client.get_dataflows()
        msg2 = await client.get_dataflows()
    assert call_count["n"] == 1, "second call should hit the cache"
    # Both messages should parse to a StructureMessage with dataflow content
    assert hasattr(msg1, "dataflow")
    assert hasattr(msg2, "dataflow")


async def test_4xx_raises_abs_api_error(db_path: Path) -> None:
    transport = _mock_transport({})  # all 404
    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=transport) as client:
        with pytest.raises(ABSAPIError):
            await client.get_datastructure("DOES_NOT_EXIST")


async def test_parse_error_wraps_as_abs_api_error(db_path: Path) -> None:
    """If ABS returns a 200 with a malformed body (HTML error page slipping
    through, schema drift, truncated XML), sdmx.read_sdmx raises a library
    exception. That used to escape the ABSAPIError contract and crash the
    server tool with an unstructured error."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"<html>this is not sdmx</html>")
    )
    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=transport) as client:
        with pytest.raises(ABSAPIError, match="parse SDMX response"):
            await client.get_dataflows()


async def test_get_data_url_includes_filters(db_path: Path) -> None:
    fixture = (Path(__file__).parent / "fixtures" / "lf_one_obs.xml").read_bytes()
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["accept"] = request.headers.get("accept", "")
        return httpx.Response(200, content=fixture)

    transport = httpx.MockTransport(handler)
    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=transport) as client:
        await client.get_data(
            "LF",
            key="M13.3.1599.20.1.M",
            start_period="2024",
            last_n=1,
        )
    assert "data/ABS,LF/M13.3.1599.20.1.M" in captured["url"]
    assert "startPeriod=2024" in captured["url"]
    assert "lastNObservations=1" in captured["url"]
    assert "genericdata+xml" in captured["accept"]


async def test_get_data_rejects_non_positive_last_n(db_path: Path) -> None:
    """0.2.10 audit finding: `if last_n:` used to silently drop `last_n=0`
    and negative values via the falsy check, returning a full fetch instead
    of an error. The defensive guard now raises ValueError. Internal-only
    today (latest hardcodes last_n=1), but the latent footgun is sealed."""
    cache = Cache(db_path)
    async with ABSClient(cache=cache, transport=_mock_transport({})) as client:
        with pytest.raises(ValueError, match="last_n must be a positive integer"):
            await client.get_data("LF", last_n=0)
        with pytest.raises(ValueError, match="last_n must be a positive integer"):
            await client.get_data("LF", last_n=-5)
