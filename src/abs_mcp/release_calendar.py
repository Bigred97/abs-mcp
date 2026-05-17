"""Scrape + parse the ABS future-releases-calendar HTML.

Source: https://www.abs.gov.au/release-calendar/future-releases-calendar
Cached via the standard `Cache` with kind="calendar" (24h TTL — see cache.py).

Public surface is `fetch_release_calendar(client, days_ahead)` which the
server's `release_calendar` tool wraps. The parser is pure-text-from-HTML
(re.findall — no BeautifulSoup dep) because each row's markup is regular:

    <strong class="event-name">{title}</strong>
    <span class="reference-period-value">{period}</span>
    <time datetime="{iso-utc}" class="datetime">{local-time}</time>
    <a href="{publication-path}">...

We extract `release_at` from the `<time datetime="...Z">` attribute (always
UTC in the source) and convert to Sydney local time per the portfolio
convention. `dataset_id` + `publication_id` come from a curated
title→catalogue→id map; entries that don't match stay null.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .cache import TTL, Cache
from .client import ABSAPIError
from .models import ReleaseEntry

# Source URL — `latest-releases` is past publications; `future-releases-calendar`
# is the upcoming list we actually want.
CALENDAR_URL = "https://www.abs.gov.au/release-calendar/future-releases-calendar"

# Sydney = AEST (UTC+10) Apr-Oct, AEDT (UTC+11) Oct-Apr. The ABS schedule is
# Sydney-local. We do a naive offset of +10/+11 by month rather than pulling
# zoneinfo because (a) it's stdlib-friendly, (b) the offset is part of the
# rendered string the gateway diffs against. Wrong by one hour at the DST
# changeover boundary itself; that's a known limitation worth taking over
# adding zoneinfo as a runtime dep.
def _sydney_offset_for(dt_utc: datetime) -> timedelta:
    """Naive AEST/AEDT switch. AEDT (UTC+11) runs Oct first Sunday → Apr
    first Sunday; AEST (UTC+10) covers the rest. The first-Sunday
    boundary is approximated as day ≤ 7 in Apr/Oct.

    Wrong by one hour during the changeover Saturday in years where DST
    actually flips on day 5/6/7 — that's a known limitation worth taking
    over adding zoneinfo as a runtime dep.
    """
    m, d = dt_utc.month, dt_utc.day
    # Solid AEST May → Sep
    if 5 <= m <= 9:
        return timedelta(hours=10)
    # Solid AEDT Nov → Mar (incl. Jan/Feb)
    if m in (11, 12, 1, 2, 3):
        return timedelta(hours=11)
    # April: AEDT until first Sunday (~Apr 1-7), AEST afterwards.
    if m == 4:
        return timedelta(hours=11) if d <= 7 else timedelta(hours=10)
    # October: AEST until first Sunday (~Oct 1-7), AEDT afterwards.
    if m == 10:
        return timedelta(hours=10) if d <= 7 else timedelta(hours=11)
    # Defensive — every month is handled above.
    return timedelta(hours=10)


# Title prefix → (catalogue number, curated dataset_id-or-None).
# Maintained by hand — curated YAMLs already encode the mapping for the
# subset that ships as datasets, but the calendar surfaces many more ABS
# publications than abs-mcp curates. `dataset_id` stays None for non-curated
# titles; `publication_id` (the catalogue) still gets populated so the
# gateway can use it for stable identity.
_TITLE_TO_PUBLICATION: list[tuple[re.Pattern[str], str, str | None]] = [
    # Curated abs-mcp datasets — dataset_id is populated.
    (re.compile(r"^Consumer Price Index, Australia\b"), "6401.0", "CPI"),
    (re.compile(r"^Monthly Consumer Price Index Indicator\b"), "6484.0", "CPI_MONTHLY"),
    (re.compile(r"^Labour Force, Australia\b"), "6202.0", "LF"),
    (re.compile(r"^Wage Price Index, Australia\b"), "6345.0", "WPI"),
    (re.compile(r"^Average Weekly Earnings, Australia\b"), "6302.0", "AWE"),
    (re.compile(r"^Producer Price Indexes, Australia\b"), "6427.0", "PPI_FD"),
    (re.compile(r"^Job Vacancies, Australia\b"), "6354.0", "JV"),
    (re.compile(r"^Lending indicators\b", re.IGNORECASE), "5601.0", "LEND_HOUSING"),
    (re.compile(r"^Building Approvals, Australia\b"), "8731.0", "BA_GCCSA"),
    (re.compile(r"^Australian National Accounts: National Income"), "5206.0", "ANA_AGG"),
    (re.compile(r"^Monthly Household Spending Indicator\b"), "5681.0", "HSI_M"),
    (re.compile(r"^National, state and territory population\b"), "3101.0", "ERP_Q"),
    (re.compile(r"^Regional population\b"), "3218.0", "ABS_ANNUAL_ERP_ASGS2021"),
    # Non-curated but commonly-watched publications — catalogue populated,
    # dataset_id null so the gateway can broadcast to "anyone interested in
    # ABS data" subscribers without falsely claiming a curated mapping.
    (re.compile(r"^Retail Trade, Australia\b"), "8501.0", None),  # ceased Jul 2025
    (re.compile(r"^International Trade in Goods\b"), "5368.0", None),
    (re.compile(r"^Balance of Payments\b"), "5302.0", None),
    (re.compile(r"^Gross Domestic Product\b"), "5206.0", None),
    (re.compile(r"^Australian System of National Accounts\b"), "5204.0", None),
    (re.compile(r"^Estimated Resident Population\b"), "3101.0", None),
    (re.compile(r"^Population Projections\b"), "3222.0", None),
]


def _classify(title: str) -> tuple[str | None, str | None]:
    """Return (publication_id, dataset_id) for a release title."""
    norm = title.strip()
    for pattern, cat, dataset in _TITLE_TO_PUBLICATION:
        if pattern.search(norm):
            return cat, dataset
    return None, None


# Each release row in the ABS HTML looks like (whitespace collapsed):
#   <strong class="event-name"> {title} </strong>
#   <span class="reference-period-value">{period}</span>
#   <span class="event-date"><time datetime="{iso-z}" class="datetime">{...}</time></span>
#   ... <a href="{path}">...</a>
# We anchor on `event-name`, then capture forward up to the next `event-name`
# or end-of-doc.
_ROW_RE = re.compile(
    r'<strong class="event-name">\s*([^<]+?)\s*</strong>'
    r'(?:\s*<span class="reference-period-value">\s*([^<]*?)\s*</span>)?'
    r'.*?<time datetime="([^"]+)"'
    r'.*?<a href="(/statistics/[^"#?]*)"',
    re.DOTALL,
)


def _convert_to_sydney_isostring(utc_iso: str) -> str:
    """Convert '2026-05-13T11:30:00Z' → '2026-05-13T21:30:00+10:00' (Sydney).

    Sydney offset varies seasonally; see `_sydney_offset_for`. Output uses
    the canonical ISO-8601 with explicit `+HH:MM` so downstream consumers
    can reparse without zoneinfo.
    """
    # Trim 'Z' → '+00:00' so fromisoformat doesn't choke on pre-3.11 builds.
    dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    offset = _sydney_offset_for(dt)
    local = dt.astimezone(timezone(offset))
    return local.isoformat()


def parse_entries(html: str) -> list[ReleaseEntry]:
    """Parse the rendered HTML body. Best-effort — rows that don't match the
    expected regex are silently skipped so a small template tweak from ABS
    doesn't take the whole tool down.
    """
    out: list[ReleaseEntry] = []
    for title, ref_period, utc_iso, path in _ROW_RE.findall(html):
        title_norm = title.strip()
        if not title_norm:
            continue
        ref_norm = (ref_period or "").strip() or None
        try:
            release_at_local = _convert_to_sydney_isostring(utc_iso)
            release_dt = datetime.fromisoformat(release_at_local)
        except (ValueError, KeyError):
            continue
        publication_id, dataset_id = _classify(title_norm)
        source_url = "https://www.abs.gov.au" + path
        out.append(
            ReleaseEntry(
                release_at=release_dt,
                title=title_norm,
                event_type="data_release",
                dataset_id=dataset_id,
                publication_id=publication_id,
                source_url=source_url,
                reference_period=ref_norm,
            )
        )
    return out


async def _fetch_html(http_client: httpx.AsyncClient, cache: Cache) -> tuple[bytes, bool, str | None]:
    """Return (html_bytes, stale, reason). Graceful-degradation per
    portfolio CLAUDE.md: 5xx falls back to last-good cached payload."""
    cached = await cache.get(CALENDAR_URL, ttl=TTL["calendar"])
    if cached is not None:
        return cached, False, None
    try:
        resp = await http_client.get(CALENDAR_URL, headers={"Accept": "text/html"})
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        fallback = await cache.get_stale(CALENDAR_URL)
        if fallback is not None:
            payload, _ = fallback
            if isinstance(e, httpx.HTTPStatusError):
                reason = (
                    f"ABS release-calendar returned {e.response.status_code}; "
                    "serving last-good cached payload."
                )
            else:
                reason = (
                    f"ABS release-calendar unreachable ({type(e).__name__}); "
                    "serving last-good cached payload."
                )
            return payload, True, reason
        if isinstance(e, httpx.HTTPStatusError):
            raise ABSAPIError(
                f"ABS release-calendar returned {e.response.status_code}"
            ) from e
        raise ABSAPIError(
            f"ABS release-calendar request failed ({type(e).__name__})"
        ) from e
    await cache.set(CALENDAR_URL, resp.content, kind="calendar")
    return resp.content, False, None


async def fetch_release_calendar(
    http_client: httpx.AsyncClient,
    cache: Cache,
    days_ahead: int,
) -> tuple[list[ReleaseEntry], bool, str | None]:
    """Return (entries_within_horizon, stale, stale_reason)."""
    html_bytes, stale, stale_reason = await _fetch_html(http_client, cache)
    try:
        html = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        html = ""
    all_entries = parse_entries(html)
    now_utc = datetime.now(timezone.utc)
    horizon = now_utc + timedelta(days=days_ahead)
    in_window = [
        e for e in all_entries
        if e.release_at >= now_utc and e.release_at <= horizon
    ]
    in_window.sort(key=lambda e: e.release_at)
    return in_window, stale, stale_reason
