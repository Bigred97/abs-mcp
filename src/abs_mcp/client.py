"""Async ABS Data API client.

Owns the httpx call (so cache keys are just URLs and tests can swap a
MockTransport) but parses the response body with sdmx1 — we get the full
sdmx1 object model without depending on its synchronous Client.
"""
from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from io import BytesIO
from typing import Any, TypeVar

import httpx
import sdmx
from sdmx.message import DataMessage, Message, StructureMessage

from .cache import TTL, Cache, CacheKind

M = TypeVar("M", bound=Message)

DEFAULT_BASE_URL = "https://data.api.abs.gov.au/rest"
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# ABS's SDMX-JSON output isn't standard-compliant (sdmx1 can't parse it).
# We use the XML formats throughout — sdmx1's XML reader handles them cleanly.
ACCEPT_STRUCTURE = "application/vnd.sdmx.structure+xml;version=2.1"
ACCEPT_DATA = "application/vnd.sdmx.genericdata+xml;version=2.1"


# ─── stale signal (graceful-degradation reporting per CLAUDE.md dim #4) ─
# When the upstream ABS API is unreachable, _fetch_bytes falls back to
# the cached payload regardless of TTL and records the staleness in this
# ContextVar. Server-side tool wrappers read it after the request chain
# and set DataResponse.stale / .stale_reason. ContextVar (not instance
# attr) so concurrent MCP tool calls each see their own state.
_stale_signal: ContextVar[tuple[bool, str | None]] = ContextVar(
    "abs_mcp_stale_signal", default=(False, None)
)


def reset_stale_signal() -> None:
    """Clear the stale state. Call once at the start of each tool call."""
    _stale_signal.set((False, None))


def get_stale_signal() -> tuple[bool, str | None]:
    """Return (stale, reason) for the most recent fetch chain in this context."""
    return _stale_signal.get()


def _mark_stale(reason: str) -> None:
    """Record that a stale-cache fallback was served this context.

    If multiple fetches in one chain are stale, we keep the FIRST reason
    (it's usually the most informative — the originating upstream failure).
    """
    cur_stale, _ = _stale_signal.get()
    if not cur_stale:
        _stale_signal.set((True, reason))


class ABSAPIError(Exception):
    """Raised when the ABS API returns a non-2xx response."""


class ABSClient:
    def __init__(
        self,
        cache: Cache | None = None,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache = cache or Cache()
        self._http = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            headers={"User-Agent": "abs-mcp/0.1"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "ABSClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _fetch_bytes(self, url: str, kind: CacheKind, accept: str) -> bytes:
        cached = await self.cache.get(url, ttl=TTL[kind])
        if cached is not None:
            return cached
        try:
            resp = await self._http.get(url, headers={"Accept": accept})
            resp.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            # Graceful degradation: when upstream is unreachable, fall back
            # to the most-recent cached payload (regardless of TTL) rather
            # than raising and breaking the agent's chain of reasoning.
            # The staleness is surfaced via the _stale_signal ContextVar
            # and ends up in DataResponse.stale / stale_reason.
            fallback = await self.cache.get_stale(url)
            if fallback is not None:
                payload, cached_at = fallback
                age_min = max(0, int((time.time() - cached_at) / 60))
                if isinstance(e, httpx.HTTPStatusError):
                    upstream = f"ABS API returned {e.response.status_code}"
                else:
                    upstream = f"ABS API unreachable ({type(e).__name__})"
                _mark_stale(
                    f"{upstream} for {url}; serving cached payload from "
                    f"~{age_min} minute(s) ago"
                )
                return payload
            # Genuinely no cache to fall back to — preserve original behaviour.
            # NOTE: do NOT embed the full upstream URL in the error text. The
            # message bubbles up through ValueError chains to REST callers (and
            # any non-SDMX-fluent client); the raw URL leaks the internal SDMX
            # key shape (e.g. ".10001.10..M") and the data.api.abs.gov.au host.
            # Status code is kept — it's transport-agnostic and informative.
            if isinstance(e, httpx.HTTPStatusError):
                raise ABSAPIError(
                    f"ABS API returned {e.response.status_code}"
                ) from e
            raise ABSAPIError(f"ABS API request failed ({type(e).__name__})") from e
        await self.cache.set(url, resp.content, kind=kind)
        return resp.content

    async def _fetch_parsed(
        self, url: str, kind: CacheKind, accept: str, expected: type[M]
    ) -> M:
        body = await self._fetch_bytes(url, kind, accept)
        try:
            # Run sync SDMX-XML parse off the event loop. Large dataflows
            # (CPI, Census, ANA_AGG) ship 5-20MB XML payloads; sdmx.read_sdmx
            # is CPU-bound XML traversal that takes 1-3 seconds and would
            # otherwise block the async tool for every concurrent request.
            msg = await asyncio.to_thread(sdmx.read_sdmx, BytesIO(body))
        except Exception as e:
            # Callers catch ABSAPIError; anything else escapes the contract.
            # Happens on schema drift or an HTML error page slipping past status checks.
            raise ABSAPIError(f"Failed to parse SDMX response ({type(e).__name__})") from e
        if not isinstance(msg, expected):
            raise ABSAPIError(
                f"ABS API returned a {type(msg).__name__} where {expected.__name__} was expected"
            )
        return msg

    async def get_dataflows(self) -> StructureMessage:
        url = f"{self.base_url}/dataflow/ABS/all/latest"
        return await self._fetch_parsed(url, "catalogue", ACCEPT_STRUCTURE, StructureMessage)

    async def get_datastructure(self, dataset_id: str) -> StructureMessage:
        url = f"{self.base_url}/datastructure/ABS/{dataset_id}?references=all"
        return await self._fetch_parsed(url, "datastructure", ACCEPT_STRUCTURE, StructureMessage)

    async def get_data(
        self,
        dataset_id: str,
        key: str = "all",
        start_period: str | None = None,
        end_period: str | None = None,
        last_n: int | None = None,
    ) -> DataMessage:
        # Defensive: `last_n` is internal (latest() hardcodes 1; the public
        # get_data tool doesn't expose it). The previous `if last_n:` falsy
        # check silently dropped `last_n=0` and negative values. Future
        # callers should get a clear ValueError instead of a silent
        # fetch-everything if they pass a bad value.
        if last_n is not None and last_n <= 0:
            raise ValueError(
                f"last_n must be a positive integer, got {last_n}. "
                "Use last_n=None to fetch all observations."
            )
        params: list[str] = []
        if start_period:
            params.append(f"startPeriod={start_period}")
        if end_period:
            params.append(f"endPeriod={end_period}")
        if last_n is not None:
            params.append(f"lastNObservations={last_n}")
        query = ("?" + "&".join(params)) if params else ""
        url = f"{self.base_url}/data/ABS,{dataset_id}/{key}{query}"
        kind: CacheKind = "latest" if last_n == 1 else "data"
        return await self._fetch_parsed(url, kind, ACCEPT_DATA, DataMessage)
