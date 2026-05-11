# Changelog

## 0.2.4 (2026-05-11)

Robustness pass — input validation, filter ergonomics, and a curated-data correction.

- **Input-type validation across all tools.** Non-string `dataset_id`,
  non-string `query`, non-dict `filters`, and non-int `limit` used to crash
  with raw `AttributeError`/`TypeError` from deep inside the call stack.
  Now: a `ValueError` with an actionable hint at the boundary. Negative
  `limit`, empty `dataset_id`, and `dataset_id` containing URL-unsafe
  characters (spaces, slashes, query marks) are also rejected explicitly
  rather than leaking unencoded user input into the request URL.
- **Whitespace-tolerant filter values.** `{"region": " nsw "}` (a common
  LLM/agent payload shape) used to fail "Unknown value ' nsw '". Values
  are now stripped before lookup.
- **Empty-list filters rejected with a hint.** `{"region": []}` used to
  silently expand to "all regions" via the empty SDMX dot-segment. Now:
  `ValueError` telling the user to pass at least one value or omit the
  filter.
- **Hidden curated dims now error cleanly.** Passing an auto-managed
  hidden dimension (e.g. `{"age": "15-19"}` on LF) used to produce
  `Try one of: ` with an empty list, because hidden dims have no
  user-facing value map. Now: an explicit "auto-managed" message
  pointing the user at the visible filters.
- **LEND_HOUSING declared `update_frequency: monthly` but publishes
  quarterly.** ABS switched Lending Indicators from monthly to quarterly
  cadence in 2025. The YAML's `FREQ` default was already `Q`, and the
  monthly endpoint 404s, but the top-level metadata still claimed
  monthly. Fixed to `quarterly` and updated the description.
- **shaping.py: latent period-extraction bug.** The unit-index builder
  iterated `obs.dim.values` and let the loop variable overwrite itself,
  which would silently corrupt the period key if SDMX ever returned >1
  observation dim. Replaced with explicit single-value extraction.
- **shaping.py: removed double `to_records()` call.** csv and series
  formats each parsed the SDMX message twice; now once.
- 34 new regression tests (118 total, was 84 in 0.2.3) — 27 covering
  server-tool input validation, 7 covering filter translation hardening.

## 0.2.3 (2026-05-11)

- LEND_HOUSING measure now defaults to `value` (Australian Dollars) — was returning loan counts (Number) when measure was unspecified, which surprised LLM users asking "what are NSW housing loans?". Headline housing-finance figure is the dollar value.
- 1 new regression test (84 total).

## 0.2.2 (2026-05-11)

**Search relevance overhaul.** A polish audit found that common queries like
`"gdp"`, `"inflation"`, `"labour force"`, and `"mortgage"` were returning
ABS census tables (`ABS_C16_*`, `C21_*`, `ABORIGINAL_*`) instead of the
curated dataflows that actually answer them. ABS publishes 1,200+ dataflows
and ~800 are census tables that mention these keywords incidentally.

- Each curated YAML now declares a `search_keywords` list folded into the
  search haystack. `"mortgage"` finds `LEND_HOUSING`, `"gdp"` finds
  `ANA_AGG`, etc.
- Curated dataflows get a +25 score bonus in `search_in_memory` so they
  outrank the noise even when the match is moderate.
- Wider candidate pool (8× limit) before reranking — gives the boost room
  to work.
- Better error hints: `query is required` and `end_period < start_period`
  now suggest specific next steps.
- 12 new parametrised regression tests lock in search relevance for
  common AU economic queries. 83 tests now (was 71 in 0.2.1).

Before/after: 5/13 common queries hit the right curated → 13/13.

## 0.2.1 (2026-05-11)

Customer stress-test pass — fixes for three real bugs surfaced by edge-case probing.

- `format` parameter is now validated and case-normalised. `format='JSON'` raises `ValueError("Unknown format 'JSON'. Valid options: ['csv','records','series'])`. `format='CSV'` is normalised to `'csv'` (was previously falling through to records).
- Reversed periods (`start_period > end_period`) are now rejected client-side with a clear message before hitting the ABS API.
- WPI state-level queries (e.g. `region='nsw'`) now work — the default `TSEST` was `20` (Seasonally Adjusted), but WPI's SA series is only published nationally. Default changed to `10` (Original); `adjustment` is now a visible filter so users can pick `'seasonally_adjusted'` for the headline national figure.
- 4 new regression tests cover the above. 71 tests now (was 67 in 0.2.0).

## 0.2.0 (2026-05-11)

- 3 new curated dataflows: **ANA_AGG** (GDP / National Accounts), **AWE** (Average Weekly Earnings), **ERP_Q** (quarterly Estimated Resident Population)
- Fix: hidden curated dimensions no longer leak into record dimensions
- Fix: `DataResponse.unit` now populated when all observations share a unit
- Fix: query echo no longer includes `_default_*` noise
- Fix: csv format now populates `period.start` / `period.end`
- Fix: unit attribution — values are scaled by `UNIT_MULT` and labelled by `UNIT_MEASURE`, so JV vacancies show 101,200 Number instead of 101.2
- Curated default fixes: CPI / BA_GCCSA / LEND_HOUSING defaults now point to series with actual data
- 67 tests (was 50 in 0.1.0)

## 0.1.0 (2026-05-11)

Initial release.

- 5 MCP tools: `search_datasets`, `describe_dataset`, `get_data`, `latest`, `list_curated`
- Hand-curated plain-English mappings for 5 dataflows: `LF` (Labour Force), `CPI` (Consumer Price Index), `ABS_ANNUAL_ERP_ASGS2021` (Estimated Resident Population), `BA_GCCSA` (Building Approvals), `LEND_HOUSING` (Lending Indicators - Housing)
- Non-curated dataflows still queryable via raw SDMX dimension IDs and codes
- SQLite-backed cache with per-kind TTL: catalogue 24 h, codelists 7 d, data 1 h, latest 15 min
- Response shapes: `records` (default), `series`, `csv`
- 50 tests (35 unit + 7 live integration + 8 MCP-protocol)
