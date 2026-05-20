# Changelog

## 0.13.1 (2026-05-20)

### Added — BA_LGA2024 (council / LGA-level building approvals)

- **New curated dataset `BA_LGA2024`** — building approvals at the **council
  (Local Government Area) level**, monthly, covering ~570 Australian councils.
  Far more granular than `BA_GCCSA` (which stops at the 8 capital cities +
  states). Pass `region=` an ASGS LGA code (e.g. `"10050"` Albury, `"22750"`
  Greater Geelong) for council-by-council approvals, or use a named shortcut.
  Region is `permissive: true` so any of the ~570 LGA codes passes through
  without enumeration (same pattern as `ABS_ANNUAL_ERP_ASGS2021`). Defaults to
  number of dwelling units approved, total residential, private sector, total
  work, Australia.
- **Currency note:** the customer-facing ID stays `BA_LGA2024` (the catalogue
  name property economists ask for) but resolves via `sdmx_dataflow_id:
  BA_LGA2025` indirection to ABS's current LGA dataflow. The standalone
  `BA_LGA2024` SDMX dataflow froze at 2025-06; `BA_LGA2025` carries the live
  monthly series. Verified current against the live ABS API: Albury (LGA 10050)
  total-residential approvals **2026-03 = 39** dwelling units. Same
  alias-indirection pattern as `CPI → CPI_Q` and `BUILDING_APPROVALS → BA_GCCSA`.

## 0.13.0 (2026-05-20)

### Added — BUILDING_ACTIVITY (dwelling completions)

- **New curated dataset `BUILDING_ACTIVITY`** (ABS cat 8752.0, SDMX dataflow
  `BUILDING_ACTIVITY`). Where `BUILDING_APPROVALS` is the leading indicator
  (dwellings green-lit), `BUILDING_ACTIVITY` is the trailing reality:
  dwelling units **completed**, commenced, and under construction, plus the
  dollar value of work done / commenced / completed. Completes the
  housing-supply story — approvals (pipeline) → completions (delivered stock).
  Defaults to number of dwelling units completed, total residential, all
  sectors, Australia, current price, original series. Verified current against
  the live ABS API: **2025-Q4 = 47,802** total-residential dwelling completions.
  Quarterly cadence. Pairs with `BUILDING_APPROVALS` for the
  approvals-vs-completions narrative and `NOM` for supply-vs-migration.

### Confirmed — CPI_MONTHLY currency fix (ships the 0.12.1 re-point)

- The 0.12.1 re-point of `CPI_MONTHLY` from the frozen `CPI_M` dataflow
  (stuck at 2025-09) to the live `CPI` dataflow (FREQ=M) was never published
  to PyPI. This release ships it. Re-verified against the live ABS API: the
  default key (`MEASURE=3`, `INDEX=10001`, `TSEST=10`, `REGION=50`, `FREQ=M`)
  now returns **2026-03 = 4.6%** annual (2026-02 = 3.7%, 2026-01 = 3.8%).
  The old `CPI_M` dataflow remains frozen at 2025-09 (re-confirmed). Added a
  BUILDING_ACTIVITY currency-guard live test alongside the existing
  CPI_MONTHLY guard.

## 0.12.1 (2026-05-20)

### Fixed — data-currency + integrity audit (two stale/broken datasets)

A systematic cross-check of every curated dataflow against the live ABS
Data API surfaced two datasets serving stale or no data:

- **CPI_MONTHLY was frozen at 2025-09.** ABS retired the standalone
  `CPI_M` "Monthly CPI Indicator" dataflow (cat 6484.0) when it moved to
  a complete monthly CPI inside the main `CPI` dataflow (keyed by
  FREQ=M). `CPI_M` stopped updating at September 2025, so `CPI_MONTHLY`
  was returning a ~6-month-stale 3.5% instead of the current figure.
  Re-pointed `CPI_MONTHLY` at the `CPI` dataflow → now returns
  **2026-03 = 4.6%**, matching the ABS published headline exactly
  (transport +8.9%, also verified). Region/TSEST/FREQ defaults were
  already correct (50 / 10 / M), so this was a one-line dataflow re-point.

- **BUSINESS_INDICATORS returned 404 on every call.** Two key-construction
  bugs: (1) `price_adjustment` defaulted to `"CP"`, but QBIS only accepts
  `CUR` / `CVM` / `IPD`; (2) the default query asked for Sales (M1) at the
  all-industries `TOT` level, which ABS does not publish (summing sales
  across industries double-counts). Fixed the price-adjustment code and
  switched the default measure to gross_operating_profits (M7), which
  does carry a national total. Now returns **2025-Q4** national GOP
  ($148.99B); sales-by-industry (e.g. mining $109.98B) verified against
  source. Added `chain_volume` / `deflator` as queryable real-terms options.

### Changed — suppress superseded SDMX dataflows from the catalogue

Added `_SUPERSEDED_SDMX_DATAFLOWS` (currently `{CPI_M}`) so the frozen
monthly-indicator dataflow no longer surfaces in `search_datasets` —
customers can't accidentally query its stale 2025-09 data.

### Added — currency guard test

`test_cpi_monthly_is_current_not_frozen` (live) asserts CPI_MONTHLY's
latest period is within ~6 months of today, so a future re-freeze or
bad re-point fails CI instead of silently shipping stale inflation data.

## 0.12.0 (2026-05-20)

### Added
- **BUILDING_APPROVALS** — ABS Building Approvals (cat 8731.0, SDMX `BA_GCCSA`).
  Monthly dwelling approvals by state/Greater Capital City × building type ×
  measure. The spine of the property-economist "Building Index" narrative.
  Latest period verified current (2026-03).
- **NOM** — ABS Net Overseas Migration (cat 3412.0, SDMX `NOM_FY`). Arrivals,
  departures, and the published NET flow by state × age × sex, financial-year
  grid. Publishes the headline NET figure directly and runs to FY2025 — the
  canonical migration-vs-housing series. (The older `ABS_NOM_VISA_CY` visa
  series remains for the visa-subclass breakdown.)

Both map to the highest-evidenced unmet AU-data demand (housing supply vs
migration). Household Spending is already served by the existing `HSI_M`
(Monthly Household Spending Indicator, verified current 2026-03).


## [0.11.15] — 2026-05-19

### Fixed

- **Per-thread `_client` cache** (P0 prod bug, observed on ausdata-api):
  module-global `_client` bound to the FIRST event loop and tripped
  `RuntimeError: Event loop is closed` when called from a multi-loop
  host that wraps the MCP and runs `asyncio.run(_get_data_impl(...))`
  in a worker thread per request. Cache moved to `threading.local()`
  so each worker thread gets its own client bound to its own loop.
  `reset_client_for_tests()` now only clears the calling thread.

## [0.11.14] - 2026-05-18

### Added — `prewarm_curated()` + `abs-mcp --warmup` CLI for gateway startup

Gateway integration reported an OOM cascade when pre-warming five new
curated datasets (LF_AGE + ITGS + BUSINESS_INDICATORS + CPI_MONTHLY +
RES_DWELL_ST) in parallel on a 512MB Fly worker — each cold SDMX parse
peaks at 150-250MB transient, so 5 in parallel exceeds the worker's
resident-memory ceiling.

Added `abs_mcp.server.prewarm_curated()`:
  - `dataset_ids=None` defaults to every curated dataset
  - `max_concurrency=2` semaphore bounds parallel warms (default sized
    for a 512MB worker; bump to 4-5 on larger workers)
  - Per-dataset error catching — one failing dataflow doesn't abort the
    rest
  - Returns `dict[id, "ok" | "error: ..."]` for caller-side audit

CLI equivalent for gateway init hooks (FastAPI lifespan, Fly machine
init, etc.):
```
abs-mcp --warmup                              # warm all curated datasets, conc=2
abs-mcp --warmup --warmup-concurrency 1       # strict sequential
abs-mcp --warmup --warmup-only LF,CPI,WPI     # warm a subset
```

Exits 0 on success, 1 if any dataflow failed. Progress streamed to
stderr so logs survive.

### Improved — explicit error when CPI quarterly requested with `adjustment='original'`

Customer-feedback flagged that `latest('CPI', filters={'adjustment':
'original'})` hit a confusing ABS 404. Root cause: ABS's quarterly CPI
SDMX product (CPI_Q) publishes Seasonally Adjusted only — Original
(NSA) and Trend simply don't exist in the Data API for the quarterly
release. The `adjustment` filter was un-hidden in 0.11.11 to advertise
the dim, but ABS doesn't actually publish Original quarterly data.

Pre-flight check now raises before hitting the ABS API with a clear
methodology explanation:

  > abs.CPI (quarterly, cat 6401.0) publishes the Seasonally Adjusted
  > series only — `adjustment='original'` is not available in ABS's
  > Data API for the quarterly product. For Original (NSA) or Trend
  > values, query `CPI_MONTHLY` (cat 6484.0) which publishes all three
  > time-series treatments at monthly cadence. Alternatively, omit the
  > `adjustment` filter on CPI to get the SA headline (the figure RBA
  > + Treasury cite as 'CPI').

195 unit tests pass.

## [0.11.13] - 2026-05-18

### Added — `BUSINESS_INDICATORS` curated dataset (QBIS cat 5676.0)

Quarterly Business Indicators Survey — sales, inventories, wages,
gross operating profits, and profit margins across ANZSIC industries
× corporate/unincorporated/total scope × state. Defaults to All-industries
quarterly sales, national, current-price SA.

Pairs with WPI (wage prices), CPI (inflation), ANA_AGG (GDP) for
top-down macro analysis. Customer query: "Corporate profits trend last
2 years", "Inventories-to-sales ratio retail trade", etc.

195 unit tests pass.

## [0.11.12] - 2026-05-18

### Added — `LF_AGE` and `ITGS` curated datasets

Two new datasets unblock customer-feedback queries that were previously
unreachable:

`LF_AGE` — Labour Force by Age Group (cat 6202.0, LF_AGES dataflow).
Adds 13 age bands (`youth` / `15_24`, `25_34`, …, `55_64`, `65_plus`,
plus 5-year detail) to participation rate, unemployment rate,
employment-to-pop ratio, hours worked, underemployment rate. Plain-
English aliases include `youth` and `seniors`. Verification: national
youth (15-24) unemployment rate = 8.89% @ 2026-03.

`ITGS` — International Trade in Goods (cat 5368.0). Monthly merchandise
trade by commodity category. Plain-English measure aliases for
`balance_on_goods` (the headline trade-balance figure),
`total_goods_exports`, `total_goods_imports`, plus rural / metal-ore /
meat / cereal / wool commodity export categories. Defaults to national
balance, current prices, seasonally adjusted. Verification: March 2026
trade balance = -$1.84B; metal ores exports = $12.85B.

195 unit tests pass.

## [0.11.11] - 2026-05-18

### Fixed — CPI_MONTHLY expenditure-group codes (5 wrong, 5 missing)

Customer-sim flagged the CPI-by-expenditure-group workflow as not
working: queries against `category` values 'transport', 'health',
'communication', 'recreation', 'education', 'insurance_financial' all
returned 0 rows or ABS 404s because the curated YAML mapped them to
non-existent SDMX codes (20007-20011 don't exist in CPI_M; those
divisions use 115xxx and 126xxx codes).

Verified against the actual SDMX codelist (`CL_CPI_INDEX_17`) and
re-mapped:

  Division                                | OLD code | NEW code
  ----------------------------------------|----------|---------
  Food and non-alcoholic beverages        | 20001    | 20001 ✓
  Clothing and footwear                   | 20003    | 20002 (was wrong)
  Housing                                 | 20004    | 20003 (was wrong)
  Furnishings, household equipment        | 20005    | 20004 (was wrong)
  Transport                               | 20007    | 20005 (was wrong)
  Alcohol and tobacco                     | 20002    | 20006 (was wrong)
  Health                                  | 20006    | 115486 (was wrong + missing)
  Communication                           | 20008    | 115488 (was missing)
  Recreation and culture                  | 20009    | 115489 (was missing)
  Education                               | 20010    | 115493 (was missing)
  Insurance and financial services        | 20011    | 126670 (was missing)

Verification: all 11 divisions now return data. Sep 2025 Original
annual changes: Food 3.1%, Housing 5.6%, Health 4.1%, Education 5.3%,
Insurance 2.6%, etc.

### Improved — CPI / CPI_MONTHLY `adjustment` filter un-hidden

Previously `adjustment` (Original / Seasonally Adjusted / Trend) was a
hidden dim that customers couldn't request explicitly. Now visible with
plain-English values. CPI (quarterly) only publishes SA — the YAML
documents this and points to CPI_MONTHLY for Original (NSA) values.

195 unit tests pass.

## [0.11.10] - 2026-05-18

### Improved — period-format hints on ABS 4xx errors

When ABS rejects a query with 404/422 because the period is mistyped,
the error now suggests the canonical ABS format instead of just echoing
the upstream code. Common mistypes caught:

- `2024Q1` (no hyphen) → "try '2024-Q1' (quarterly format)"
- `2024S1` (no hyphen) → "try '2024-S1' (half-yearly format)"
- `202403` (no hyphen) → "try '2024-03' (monthly format)"
- `2024-01` against a quarterly dataflow (CPI etc) → "is monthly but
  CPI is quarterly — try '2024-Q1'" (computes the matching quarter)

Period-format guidance was already in the docstring but customers
typing in chat hit the API rejection first; this surfaces the format
at the point of error so the next call works.

195 unit tests pass.

## [0.11.9] - 2026-05-18

### Added — `RES_DWELL_ST` curated (Cat 6416.0 Residential Dwellings — Values, Mean Price, Count)

Customer-sim flagged the housing-affordability use case as unreachable —
ABS catalogue 6416.0 (Total Value of Dwellings, Mean Price, Count by
state) wasn't exposed through any curated dataset. Resolved by curating
the `RES_DWELL_ST` SDMX dataflow.

Defaults to total dwelling stock value, national, latest quarter
(currently $12.3 trillion @ 2025-Q4). Five measures:
- `value_all_sectors` — total dwelling stock value (AUD millions)
- `value_households` — household-owned dwelling stock
- `value_non_households` — non-household-owned dwelling stock
- `dwelling_count` — number of residential dwellings
- `mean_price` — mean price of residential dwellings (AUD per dwelling)

Per-state filter via `region` (accepts state codes via aus-identity).
Quarterly cadence, ~6-week lag.

Verification:
- `latest('RES_DWELL_ST')` → $12.3T total Australian housing stock
- `latest('RES_DWELL_ST', {region: 'nsw', measure: 'mean_price'})` →
  NSW mean dwelling price $1,301,100

195 unit tests pass.

## [0.11.8] - 2026-05-18

### Added — `ABS_NOM_VISA_CY` curated (Net Overseas Migration by visa subclass)

Customer-sim flagged `abs.ABS_NOM_VISA_CY` as opaque — `latest()` returned
one of 5,814 rows (visa × direction × region × frequency combinations)
with no obvious headline number, so customers asking for "2022 NOM"
got `80710` (a specific visa subcategory) instead of the ~518k national
NOM they expected.

Added curated YAML:
- `latest_defaults` narrows to TOTAL visa × Australia × annual
- `measure` filter exposes 16 visa categories with plain-English keys
  (`total`, `permanent_skill`, `temporary_student`, etc.)
- `migrationtype` has `arrivals` + `departures` (the only values
  populated — Net is NOT a published series; customers compute net =
  arrivals - departures client-side)
- `region` accepts state codes via aus-identity
- `freq` is auto-managed (Annual only)

`latest()` now returns 2 rows by default — total arrivals (2022:
646,110) and total departures (2022: 223,880) for Australia.
Computed net = 422,230 for 2022 calendar year. Description documents
that the published ~518k figure is the 2022-23 FISCAL year NOM (a
different cadence not in this dataflow; see ABS Cat 3101.0 ERP).

195 unit tests pass.

## [0.11.7] - 2026-05-18

### Fixed — CI lint failures (E402 in test_catalog.py + unused import)

0.11.6 release CI failed lint:
- `from typing import Any` in `release_calendar.py` — pre-existing
  unused import surfaced by the lint sweep, cleaned via `ruff --fix`.
- E402 module-level imports below a comment block in
  `tests/test_catalog.py` — these are deliberate scope-isolation
  imports for the canonical-query ranking suite. Added `E402` to the
  `tests/*` per-file-ignores list in pyproject.toml so the intentional
  pattern stays valid without scattering `# noqa` comments.

No runtime change vs 0.11.6.

## [0.11.6] - 2026-05-18

### Fixed — HSI_M and ABS_ANNUAL_ERP_ASGS2021 latest() now headline-narrow

Continuation of the 0.11.5 size audit:
- `latest('HSI_M')` returned 378 rows / 82 KB (one row per measure ×
  category × region × price_adjustment combination). Now returns 1 row:
  Australia total household spending, current prices, headline measure.
- `latest('ABS_ANNUAL_ERP_ASGS2021')` returned 2,909 rows / 475 KB (all
  ASGS regions). Now returns 1 row: Australia total population, all
  ages, both sexes.

Both `latest_defaults` blocks are documented in their YAMLs and are
overridable by passing explicit filters. C21_G02_SA2 and C21_G02_POA
already had similar defaults from earlier work.

195 unit tests pass.

## [0.11.5] - 2026-05-18

### Fixed — C21_G01_POA latest() no longer dumps 47 MB

`latest('C21_G01_POA')` returned all 285,438 rows (~47 MB JSON, ~12M
tokens) — every postcode × every person characteristic × sex. Customer
querying "what's the latest Census postcode data?" got an unusable
context-blowing response.

Added `latest_defaults` to C21_G01_POA.yaml pointing at postcode 2000
(Sydney CBD) + total_persons. Equivalent to C21_G02_SA2's existing
pattern. Customers narrow to their postcode of interest for typical
location-intelligence queries.

Verification:
- `latest('C21_G01_POA')` → 3 rows / 1 KB (was 285k rows / 47 MB)
- Default returns: postcode 2000 total population by sex (males 14,223,
  females 13,713, persons 27,936). Matches Census 2021 published total.

195 unit tests pass.

## [0.11.4] - 2026-05-18

### Improved — proportional relevance scaling (no more ties at 100)

Customer-sim reported non-curated dataflows like `C21_G57_CED`
(Census family-income table) scoring rel=100 for unrelated queries
('labour force unemployment'). Root cause: the ranker's raw score
can exceed 100 (high=100 + low=50 + CURATED_BONUS=25 = 175), then
clamped to 100 — wiping the differences between the actual winner
and noise hits that also clamped.

Now the leader's raw score sets the scale: leader caps at 100, other
results scale proportionally to (raw / leader_raw) * 100. So a leader
at raw=175 stays at 100; a follower at raw=122 shows as 70 instead of
also clamping to 100.

Live verified:
- 'labour force unemployment' → LF rel=100, C21_G57_CED rel=70.1
  (was both tied at 100)
- 'cpi inflation' → CPI_MONTHLY rel=100, CPI rel=89.9 (graduated)
- 'housing prices' → LEND_HOUSING rel=100, BA_GCCSA rel=85.9

Sort order unchanged — only the displayed relevance value differs.

195 tests pass.

## [0.11.3] - 2026-05-18

### Added — SA4 granularity to `C21_G02_SA2` (Census G02 income/age/rent)

Customer-sim flagged "SA4-level household income" as a coverage gap.
The C21_G02 SDMX flow already supported SA4 — just wasn't exposed
in the curated YAML's `region_type` values. Added it.

Customers can now query `{region_type: sa4, measure: median_personal_income_weekly,
state: nsw}` and get ~30 NSW SA4 regions with their median weekly
personal income. The same pattern works for any G02 measure (rent,
mortgage, family/household income, household size).

Live verification (NSW SA4 income, 2021 Census):
- Sydney - Eastern Suburbs: $1,296/week
- Sydney - City and Inner South: $1,174/week
- Sydney - Baulkham Hills: $988/week
- Sydney - Blacktown: $833/week
- Central Coast: $727/week

No backward-compat impact — defaults still resolve to SA2.
195 tests pass.

## [0.11.2] - 2026-05-18

### Added — `ABS_ANNUAL_ERP_ASGS2021` now exposes `age` × `sex` dimensions

Customer-sim flagged "population by age × sex × state" as a coverage
gap (catalogue 3235.0). The data was always available — ABS publishes
it via the `ERP_ASGS2021` SDMX dataflow — but the curated wrapper
mapped to the simpler `ABS_ANNUAL_ERP_ASGS2021` flow which only
exposed region/region_type.

Repointed the curated dataset's `sdmx_dataflow_id` to `ERP_ASGS2021`
and added two new dimensions:

- **`sex`**: `persons` (default, all combined), `males`/`men`,
  `females`/`women`. SDMX codelist CL_SEX.
- **`age`**: `all_ages` (default, TOT), five-year bands as
  `0_4`, `5_9`, …, `85_plus`, plus plain-English cohort shortcuts
  (`children`, `school_age`, `teenagers`, `young_adults`, `retirees`,
  `elderly`). SDMX codelist CL_ERP_AGE.

Live verification:
- `region=nsw, sex=females, age=25_29, period=2023` → 294,930 ✓
- `region=nsw, period=2023` → 8,341,199 (NSW total) ✓
- `region=australia, period=2023` → 26,652,777 (AU total) ✓

Backward-compat: existing queries without `sex`/`age` filters return
the same totals they did before (defaults apply Persons / All-ages).
The `region` / `region_type` / `measure` / `frequency` dims are
unchanged.

195 tests pass.

## [0.11.1] - 2026-05-18

### Added — `DatasetSummary.relevance` populated by `search_datasets()`

`search_datasets()` results now carry their RapidFuzz score on the
`relevance` field (0-100, rounded to 1dp). Previously the score was
computed internally for sort order but discarded before returning,
so direct-MCP callers (Claude Code etc.) had no way to order results
without re-running the fuzzy match themselves. The ausdata-api
gateway already re-ranks across sources, so its consumers see no
change.

The score is the two-pool ranker's `adjusted` value (token_set_ratio
on id/name/keywords + capped WRatio on description + curated bonus
- deprecation penalty), clamped to [0, 100].

`relevance: None` when the entry came from `list_curated()` rather
than a fuzzy search.

## [0.11.0] - 2026-05-17

### Added — `release_calendar(days_ahead)` tool

New tool exposes the official ABS release schedule
(https://www.abs.gov.au/release-calendar/future-releases-calendar) as a
structured feed. Built for the ausdata-api gateway's upcoming webhook
product: it polls this every 5 min and POSTs to subscribers when a
release crosses the publish boundary.

Per-entry shape (shared with `rba-mcp.release_calendar`):

```
release_at:       ISO-8601 with Sydney offset, "2026-04-30T11:30:00+10:00"
title:            "Consumer Price Index, Australia"
event_type:       "data_release" (ABS releases are all data, never policy)
dataset_id:       curated abs-mcp ID if mapped ("CPI"), else null
publication_id:   ABS catalogue number ("6401.0"), null when uncatalogued
source_url:       click-through URL on abs.gov.au
reference_period: "March 2026" / "Q1 2026" — useful for gateway dedup
```

### Performance — `latest()` cache TTL 15 min → 2 h

ABS releases happen at 11:30 AEST on weekdays; between publish
boundaries the latest observation doesn't change, so the 15-min TTL
was burning network for no freshness gain. The new `release_calendar`
tool gives consumers a precise way to invalidate after a published
embargo instead of polling. Bumped `latest()` TTL accordingly.

### Internal

- New `release_calendar.py` module — HTML scraper + title→catalogue
  mapping table covering the 10 curated datasets plus 7 commonly-watched
  non-curated catalogues (Retail Trade, International Trade in Goods,
  Balance of Payments, GDP, ASNA, Estimated Resident Population,
  Population Projections).
- `ReleaseCalendarResponse` + `ReleaseEntry` Pydantic models in
  `models.py` — uniform shape with `rba-mcp` for cross-source webhook
  routing.
- New cache kind `"calendar"` with 24h TTL — fresh enough to catch
  rescheduled embargoes, cheap enough that gateway polling never hits
  the live HTML.
- Naive AEST/AEDT switch via month-based offset (first-Sunday boundary
  in April / October — within an hour of correct at the changeover
  Saturday; worth taking over a zoneinfo runtime dep).
- 16 regression tests in `tests/test_release_calendar.py` covering
  parser, DST switch, classifier, cache-fallback on 5xx, and tool
  surface validation. 195 tests pass.

## [0.10.3] - 2026-05-17

### Performance — in-process parsed-message LRU

The byte-cache layer (`Cache.get` keyed by URL) already skipped network
on warm calls, but every warm call still re-parsed the SDMX-XML
(1-3s on large dataflows like CPI / Census / ANA_AGG). The customer-sim
flagged `ABS_ANNUAL_ERP_ASGS2` at 7s cold parse, with ~3s of that being
the parse cost on every subsequent call after the byte cache warmed.

Added a small bounded LRU (max 16 entries) inside `ABSClient` keyed by
(url, expected_message_type). Warm calls skip both the cache decompress
AND the SDMX-XML re-parse — `~3s` → `~5ms`. The 16-entry cap keeps the
parsed object graph (multi-MB per DataMessage / StructureMessage)
bounded.

Cold-cold (no byte cache yet) is unchanged — still pays ABS API network
+ initial parse. The fix is for the "byte cache warm, parsed cache
cold" path which is the typical case after a worker restart that has
been serving traffic for >15 minutes (the latest-kind TTL).

### Internal

- Added `_parsed_cache: OrderedDict` + `_parsed_cache_lock` on
  `ABSClient`. Bounded LRU with `_PARSED_CACHE_MAX_ENTRIES = 16`.
- New `reset_parsed_cache_for_tests()` method for test hygiene.
- 3 regression tests in `test_client.py`:
  `test_parsed_cache_skips_re_parse_on_warm_hit`,
  `test_parsed_cache_invalidates_per_url`,
  `test_reset_parsed_cache_for_tests_clears_lru`.

## [0.10.2] - 2026-05-17

### Improved — transport-agnostic Field descriptions

Two `Field(description=...)` strings in `server.py`'s `top_n` tool
referenced MCP-tool-name (`list_curated()`, `describe_dataset()`). The
descriptions become part of the parameter schema, so customers hitting
the REST gateway at `/v1/top-n` would see "Use list_curated() to
enumerate" — confusing because they're not calling a Python function.
Rewrote both to "Use the {endpoint or tool} to ..." — same intent, no
transport-specific noise. Matches the ato 0.8.7 and rba 0.7.5 portfolio
guard. No runtime behaviour change.

## [0.10.1] - 2026-05-17

### Fixed — event-loop blocking on sync SDMX-XML parse

`_fetch_parsed` called `sdmx.read_sdmx(BytesIO(body))` synchronously
inside an async function body. Large ABS dataflows (CPI, Census,
ANA_AGG) ship 5-20MB SDMX-XML payloads, and `sdmx1`'s XML reader is
CPU-bound traversal that takes 1-3 seconds. The sync call blocked the
event loop for every concurrent request, serialised tool calls behind a
single parse, and stalled downstream consumers like the `ausdata-api`
gateway against its 20s budget. Wrapped in `asyncio.to_thread` so the
parse runs on the default executor without blocking other in-flight
calls. Matches the 0.4.7 / 0.5.4 / 0.6.4 / 0.8.6 / 0.8.6 portfolio-wide
fixes in aihw / wgea / asic / apra / ato — abs-mcp's variant is the
biggest of the lot, both in payload size and customer-hit frequency.

## [0.10.0] - 2026-05-17

### BREAKING — `CPI` now returns quarterly periods (re-pointed to cat 6401.0)

- **`CPI` is now the canonical quarterly Consumer Price Index** — what the
  RBA, Treasury, AFR, and economists cite when they say "CPI" or
  "inflation rate". Periods come back as `2025-Q1`, `2025-Q2`, …, fixing
  every cross-source workflow that joins CPI to a quarterly series
  (most notably the WPI × CPI real-wages calculation in ausdata-api's
  `/v1/real-wages` endpoint, which was silently joining on empty period
  overlap pre-0.10.0).

  - Under the hood, curated `CPI` now indirects to ABS SDMX dataflow
    `CPI_Q` (cat 6401.0). The previous `CPI` mapping returned monthly
    indicator data (cat 6484.0) by default — which most customers
    didn't realise.
  - Default `measure=change_year` → annual CPI inflation, the headline
    figure.
  - Default `category=all_groups` → seasonally adjusted All Groups CPI
    (INDEX=999901, TSEST=20) — the headline series ABS publishes
    quarterly y/y % change for.
  - **Customer-visible reductions** (intentional, see CPI_MONTHLY for
    the full surface):
    - Sub-categories (food, alcohol, clothing, housing, transport,
      etc.) are NO LONGER available on `CPI` — ABS doesn't publish
      quarterly y/y % change for the cat 6401.0 detailed-breakdown
      series. They remain accessible via the new `CPI_MONTHLY`
      dataset.
    - Individual capital cities (sydney, melbourne, brisbane, …) are
      NO LONGER available on `CPI`. CPI_Q publishes only the national
      weighted average of eight capital cities at the headline level.
      Per-city CPI moves to `CPI_MONTHLY`.
    - `MEASURE=contribution_to_index` is no longer offered on the
      quarterly headline (use the monthly indicator if you need it).
    - New measures available on quarterly headline:
      `category=trimmed_mean` (`999902`) and
      `category=weighted_median` (`999903`) — the RBA's preferred core
      inflation measures.

### Added — `CPI_MONTHLY` curated dataset (preserves pre-0.10.0 customer surface)

- **`CPI_MONTHLY`** — Monthly Consumer Price Index Indicator (cat 6484.0).
  Maps to SDMX dataflow `CPI_M`. Full sub-category breakdown (food,
  housing, transport, etc.) and per-city series. Returns monthly periods
  (`2025-04`, `2025-05`, …) — the supplementary fast-cadence read on
  inflation between official quarterly releases.
- Existing customer code that called
  `get_data("CPI", filters={"category": "housing"})` against pre-0.10.0
  abs-mcp must migrate to `get_data("CPI_MONTHLY", filters={"category":
  "housing"})` to preserve behaviour. Code that used the headline
  All Groups annual change at quarterly cadence works without changes
  beyond the period format flip.

### Changed — `CuratedDataflow.sdmx_dataflow_id` indirection (internal)

- New optional YAML field `sdmx_dataflow_id` decouples the user-facing
  curated `id` from the underlying ABS SDMX dataflow. Defaults to `id`
  for every existing curated dataset (no behavioural change), but lets
  `CPI` → `CPI_Q` and `CPI_MONTHLY` → `CPI_M` cleanly. The catalogue
  listing hides the indirection target and any legacy SDMX dataflow
  that collides with a curated display id, so search results stay
  unambiguous.

### Fixed — Search ranking quality (canonical queries route correctly)

Customer queries against the live 0.9.2 `search_datasets` were returning
the wrong top-N for the most-asked topics: `retail` led with three Census
postcode tables (`C21_G01_POA`, `C21_G02_POA`, `C21_G02_SA2`) instead of
the active retail-spending dataset `HSI_M`, and `population` missed
`ERP_Q` from the top results. Root cause: the single-haystack WRatio
ranker let long Census API descriptions (~1,900 chars) racked up
incidental token matches and outranked focused curated datasets whose
keywords were dense but whose haystacks were short.

- **High/low-signal pool split.** `search_in_memory` now scores two
  pools per summary:
    * high-signal = `id + name + curated.search_keywords` (concise,
      intentional retrieval anchors), scored with `token_set_ratio`
      so substring matches inside unrelated words don't count
      (previously `WRatio('lending', 'Family Blending')` == 90 because
      "lending" is a substring of "Blending");
    * low-signal = API description, scored with `WRatio` for typo
      tolerance, contribution capped at 50.
  The cap is well below a clean token match (100), so a curated entry
  with the right keyword reliably outranks any description-only hit.
- **Deprecation penalty.** Datasets whose `update_frequency` starts
  with `ceased` (currently only `RT`) take a `-30` adjustment, sized so
  the active replacement (`HSI_M`) wins ties on shared retail keywords
  but the deprecated entry still ranks above non-curated SDMX entries.
- **Score every summary**, no top-N truncation per pool. The previous
  `process.extract(limit=80)` could silently drop a focused curated
  entry (HSI_M for 'retail') from the low pool when dozens of census
  descriptions scored higher on the raw query, leaving its description
  contribution stuck at zero. Full-catalogue scoring (~1,200 dataflows
  × 2 fuzz calls per query) stays under a few milliseconds.
- **Keyword nudges** (YAML-only):
    * `HSI_M`: added `retail, retail trade, retail spending, household
      retail, monthly retail` — customers ask for "retail" by name and
      HSI_M is the explicit ABS-blessed RT replacement.
    * `ERP_Q`: added `population quarterly, population latest` so it
      surfaces alongside the annual ASGS dataset for `population`
      queries.
- **Canonical-query test suite** in `tests/test_catalog.py` pins
  top-N rankings for `inflation`, `unemployment`, `wages`, `retail`,
  `population`, `GDP`, `lending`. Regression guard against future
  ranker changes silently breaking customer-facing routing.

Known carry-over: `house prices` still has no canonical curated answer
(top hits are `PPI_FD` / `CPI`). Curating a Residential Property Price
Index dataset is intentionally deferred — not in scope for 0.10.0.

### Coordination

- ausdata-api `/v1/real-wages` composer: bump abs-mcp floor to
  `>=0.10.0` AFTER this lands and re-verify the WPI × CPI period join
  on quarterly periods.

## [0.9.2] - 2026-05-16

### Added — PPI_FD + C21_G01_POA (extends macro + property/retail/health workflows)

- **`PPI_FD` — Producer Price Index Final Demand (Quarterly).** Completes
  the inflation triad alongside `CPI` (consumer) and `WPI` (wages).
  Defaults to YoY % change Total All Industries — the headline AFR/RBA
  number. Latest Q1 2026: **PPI Final Demand YoY 3.0%**.
- **`C21_G01_POA` — Census 2021 Selected Person Characteristics by
  Postcode.** Pairs with already-curated `C21_G02_POA` (medians) on the
  same POA join key. 23 person-characteristic counts: total population,
  11 age groups (0-4 through 85+), Indigenous status, birthplace,
  language at home, citizenship. Sydney CBD 2000: 14,223 persons.
- Both YAML-only adds — existing SDMX infrastructure handles them.

Note: supersedes the failed v0.9.0/v0.9.1 tags on GitHub (CI rejected
tag-vs-pyproject mismatch on those; content was sound, version
bookkeeping was the issue).

## [0.8.1] - 2026-05-16

### Fixed

- `C21_G02_SA2.latest()` (and `C21_G02_POA.latest()`): bare call without
  filters previously overran the SDMX fan-out (2,400 SA2 × 8 measures ≈
  19,200 rows for SA2; ~21k rows for POA) and raised `ValueError`. The
  curated YAML now carries a `latest_defaults:` block which is merged
  into bare `latest()` calls only — filtered calls continue to bypass
  the defaults so explicit user filters still take full precedence.
  - `C21_G02_SA2`: defaults to `region=australia, measure=median_age`
    (1-row national median age snapshot).
  - `C21_G02_POA`: defaults to `region=2000, measure=median_age`
    (POA codelist has no national 'AUS' aggregate; postcode 2000
    / Sydney CBD anchors a sensible default).
- Adds 4 regression tests covering YAML loading and the bare/filtered
  latest() merge behaviour. 10× zero-flake unit-test pass.

## [0.8.0] - 2026-05-16

### Added — HSI_M (Monthly Household Spending Indicator, current cadence)

- **`HSI_M` curated dataset.** The ABS-blessed live replacement for
  Retail Trade (RT, discontinued 31 July 2025). Built from comprehensive
  banking + POS + payment-processor transactions data — more
  representative than the legacy retail survey.
- Closes the RT-historical gap from 0.7.0: RT now provides 43 years of
  historical retail trend (Apr 1982 → Jun 2025); HSI_M provides current
  monthly household spending Jul 2025 onward.
- 12 measures (dollar value, index, MoM %, YoY %, with calendar-adjusted
  variants), 12 spending categories (food, hotels/cafes, transport,
  housing, recreation, etc.), 8 states + AUS, current/chain-volume/IPD
  price treatments. Defaults to seasonally adjusted total spending.
- Uses existing SDMX infrastructure — no new parser code.

### Customer-value validation (live ABS SDMX fetch, 2026-05-16)

- Retail analyst: `latest('HSI_M', filters={'measure':'household_spending',
  'category':'total','region':'australia'})` → $80.4B (Mar 2026).
- State splits: NSW $25.3B, VIC $19.4B, QLD $16.8B, WA $9.4B.
- Top categories: Transport $14.0B, Recreation $13.4B, Food $12.6B,
  Hotels/cafes $10.8B, Health $9.4B, Clothing $4.5B.
- YoY growth: 6.3% (March 2026).
- Search routing: "household spending", "consumer spending", "mhsi",
  "retail spending" all hit HSI_M at #1 (HSI_M outranks RT for retail
  spending queries — the active dataset correctly takes precedence over
  the historical one).

### Tests

- 153 unit tests passing (was 153). 10× zero-flake. Ruff clean.
- `test_list_ids_returns_curated_dataflows` updated from 13 to 14
  expected curated IDs.

## [0.7.0] - 2026-05-16

### Added — Retail Trade (43-year historical series)

- **`RT` — Retail Trade.** Monthly Australian retail sales by industry
  and state from April 1982 to June 2025. 43+ years of continuous
  history covering total retail, food (supermarket/liquor/specialised),
  household goods (furniture, electrical, hardware), clothing/footwear,
  department stores, cafes/restaurants/takeaway, plus per-state splits.
- ⚠️ **Historical series only.** ABS ceased the Retail Trade publication
  on 31 July 2025; data ends June 2025. The YAML description prominently
  flags the cessation and points clients to the Monthly Household
  Spending Indicator (HSI_M) as the current replacement (curating
  HSI_M is the natural next iteration).
- Uses existing SDMX infrastructure — no new parser code. 4 dimensions:
  MEASURE (current prices / chain volume / % change), INDUSTRY (15
  splits incl. 'cafes_restaurants_takeaway'), REGION (8 states + AUS),
  hidden TSEST default to seasonally adjusted, hidden FREQ to monthly.

### Customer-value validation (live ABS SDMX fetch, 2026-05-16)

- Retail analyst: `latest('RT', filters={'measure':'current_prices','industry':'total','region':'australia'})` → $37.9B (Jun 2025).
- State breakdown: NSW $11.67B, VIC $9.83B, QLD $7.85B, WA $4.35B.
- Industry: supermarket/grocery $12.37B (largest), cafes/restaurants
  $5.58B, department stores $1.99B, liquor $1.48B.
- Search routing: "retail sales", "supermarket sales", "department
  stores", "consumer spending" all surface `RT` at #1.

### Tests

- 153 unit tests passing (was 153 + 1 new curated id assertion). 10×
  zero-flake gauntlet. Ruff clean.
- New `test_list_ids_returns_curated_dataflows` asserts RT is in the
  curated set (total now 13).

## [0.6.0] - 2026-05-16

### Added — Census 2021 G02 medians (Wave 1 portfolio expansion)

- **`C21_G02_SA2` — Census 2021 G02 Selected Medians and Averages by SA2.**
  Eight commercially-critical measures at Statistical Area Level 2 (~2,400
  regions): median age, median personal/family/household income (weekly),
  median mortgage repayment (monthly), median rent (weekly), average persons
  per bedroom, average household size. Permissive REGION dimension accepts
  any ASGS 2021 SA2 code.
- **`C21_G02_POA` — Census 2021 G02 Selected Medians and Averages by
  Postcode.** Same eight measures keyed by 4-digit postal area (~2,600
  postcodes). Direct join partner for `ato-mcp.IND_POSTCODE` — together
  they replace commercial location-intelligence subscriptions (Experian
  Mosaic, Equifax) for site selection, marketing segmentation, lending
  underwriting, and insurance pricing.
- Wired to ABS's SDMX endpoint at `ABS,C21_G02_SA2` / `ABS,C21_G02_POA`
  (no new parser code; existing SDMX client handles it).

### Customer-value validation (live ABS fetch, 2026-05-16)

- Sydney CBD (postcode 2000): median household income $2,225/wk, median
  mortgage $2,800/mo, median rent $625/wk, median age 32.
- Brisbane CBD (4000): median household income $1,860/wk.
- Sydney Haymarket SA2 (117031645): median household income $2,108/wk,
  median mortgage $2,600/mo.
- `top_n` highest-income postcodes correctly surface Seaforth (2092 —
  $4,184/wk), Northbridge (2063 — $3,874/wk), City Beach Perth (6015 —
  $3,700/wk), and Vaucluse (2030 — $3,481/wk).
- Search routing: "median income", "postcode demographics", "census",
  "median rent", "household size" all hit `C21_G02_POA` / `C21_G02_SA2`
  in the top 2.

### Tests

- 153 unit tests now (was 151). 10× zero-flake gauntlet.
- New `test_list_ids_returns_curated_dataflows` asserts the 12 curated IDs.

## [0.5.0] - 2026-05-15

### Added

- **`top_n` tool** — rank rows by a numeric measure and return the top
  (or bottom) N. Wave 3 of the portfolio interoperability pass; signature
  matches aihw-mcp / apra-mcp / ato-mcp exactly so an agent that learned
  `top_n` on one sister uses it identically here:

  ```python
  top_n(dataset_id, measure, n=10, filters=None, direction="top")
  ```

  abs-mcp's `top_n` requires a curated dataflow with a `measure` dimension
  (all 10 of LF / CPI / WPI / JV / AWE / ANA_AGG / BA_GCCSA / LEND_HOUSING /
  ERP_Q / ABS_ANNUAL_ERP_ASGS2021 qualify). It runs over the most-recent
  available period (lastNObservations=1) so the rank is a clean
  "top N entities at the latest period" view rather than a noisy historical
  mix. Common workflow: `top_n("LF", "unemployment_rate", n=5)` →
  five states with the highest current unemployment.

## [0.4.0] - 2026-05-15

### Added

- **DataResponse.source_url**: canonical click-through URL field, populated
  alongside the legacy `abs_url` alias. Cross-sister consumers can now read
  `.source_url` uniformly across the portfolio. `abs_url` remains populated
  with the same value for backward compatibility.
- **DataResponse.row_count**: number of observation rows in `records`
  (`int`, defaults to `0`). Brings abs-mcp in line with the canonical
  `DataResponse` envelope used by the rest of the portfolio.

## 0.3.0 (2026-05-15): aus-identity integration — uniform state / postcode normalisation across the portfolio

The cross-source compatibility moat for the AU public-data MCP stack. Every
location-aware filter (currently `region`) now accepts ANY of:

- Canonical short codes (`NSW`, `VIC`, `QLD`, `SA`, `WA`, `TAS`, `NT`, `ACT`)
- Case-insensitive (`nsw`, `Nsw`)
- Full names (`New South Wales`, `Queensland`, `Tasmania`)
- ISO 3166-2 (`AU-NSW`, `AU-VIC`)
- Common aliases (`Tassie`)
- 4-digit postcodes (`2000` → NSW, `2600` → ACT, `3000` → VIC, `0800` → NT)

Powered by the [`aus-identity`](https://pypi.org/project/aus-identity/) library.
This means an LLM agent that's already fetched a postcode from another sister
MCP (ato-mcp, asic-mcp) can pass it directly to abs-mcp without manual
conversion.

### Added

- **`aus-identity>=0.1.0`** dependency. New top-level dep — adds zero
  transitive deps (pure-Python).
- **Cross-source state/region normalisation** in `curated.translate_filters`:
  before falling through to the existing `Try one of:` hint, a state-shaped
  filter value is run through `aus_identity.normalize_state` (state codes,
  full names, aliases) and `aus_identity.postcode_to_state` (numeric
  postcodes). Mappings preserve existing curated keys — `'nsw'` still
  resolves to SDMX `'1'`, and the canonical SDMX-code escape hatch
  (`region='1'`) still works.
- **7 new unit tests** under `tests/test_curated.py` covering full-name,
  uppercase short-code, ISO 3166-2, postcode, ACT-postcode (which is
  geographically inside NSW), and unknown-state edge cases.

### Changed

- The existing curated-suggestion tests that asserted `'queensland'` /
  `'NSW'` raise `ValueError` now expect those values to RESOLVE (they're
  valid AU state references). The "Did you mean?" rejection path is still
  exercised via `'narnia'` and other genuinely-invalid inputs.

### Notes

- This is the first cross-source moat shipped — abs-mcp + ato-mcp + apra-mcp
  + aihw-mcp + asic-mcp now all accept the same location-input shapes,
  enabling the planned `ausdata-mcp` bundle to route a single user input
  through any sister.
- No breaking changes: any input that worked in 0.2.14 still works.

## 0.2.14 (2026-05-15): error-message sweep — rejection messages now suggest the correction

Quality-dimension #5 (Deterministic Error Handling) pass. Every `ValueError`
raised by the server, client, and curated layers now carries a "Try X" /
"Did you mean X?" / "Valid options: ..." hint that gives the LLM a
self-correction path, not just a rejection notice. Audited all 30 raise
sites; rewrote the 6 that didn't already meet the bar.

- **`curated.translate_filters` — unknown filter key**: now appends `Did
  you mean 'X'?` (via `difflib.get_close_matches`, no new deps) for obvious
  typos like `measur` → `measure`, plus a `Try describe_dataset('LF')`
  pointer to the full schema. Same change for unknown filter values
  (`unemploymnt_rate` → `unemployment_rate`).
- **`curated.translate_filters` — empty value**: appends the
  `describe_dataset('<id>')` pointer alongside the existing `Try one of:`
  sample of valid keys.
- **`curated.translate_filters` — auto-managed (hidden) dim passed as a
  user filter**: appends `Try describe_dataset('<id>') to see the full
  schema.` so the LLM can discover the visible filter surface.
- **`curated._parse_dimension` — bad YAML value**: now shows the expected
  shape (`string SDMX code OR dict with sdmx_code:`) plus a worked example.
  Internal but matters when authoring a new curated YAML.
- **`server.search_datasets` — limit type error**: now includes a worked
  example (`e.g. limit=10`).
- **`server.search_datasets` — catalogue fetch failed**: now suggests
  retrying and lists the 10 curated dataflow IDs the user can query
  without the catalogue endpoint.
- **`server._get_data_impl` — DSD missing in API response**: now points
  at `search_datasets` and `list_curated()` for recovery.
- **+6 regression tests** in `test_curated.py` and `test_server_validation.py`
  that lock in the new suggestion-style messages so a future regression
  can't quietly revert them: `describe_dataset` pointer present on every
  unknown-filter / unknown-value raise, `Did you mean X?` triggered for
  typos in both filter keys and filter values, hidden-dim path also
  carries the pointer, and the worked example survives in the limit-type
  error.
- 128 unit tests now (was 122 in 0.2.13). 10 consecutive zero-flake runs.

No public API changes; this is a message-quality pass. Existing tests that
match on substrings of the old messages continue to pass because the
actionable hint was appended, not substituted.

## 0.2.13 (2026-05-15)

Graceful degradation — quality dimension #4 in CLAUDE.md.

When the upstream ABS Data API is unreachable (5xx, timeout, DNS failure,
connection refused), the client now falls back to the most-recent cached
payload regardless of TTL and surfaces the staleness in the response.
Agents see `DataResponse.stale=True` with a `stale_reason` like *"ABS API
returned 503; serving cached payload from ~17 minute(s) ago"* and can
continue reasoning, rather than the tool raising and breaking the chat.

Genuine no-cache-to-fall-back-to case still raises `ABSAPIError` — only
degrade gracefully when there's something to degrade to.

- **New: `Cache.get_stale(key) -> (payload, cached_at)`** — TTL-bypassing
  read, the building block for the fallback path.
- **New: `_stale_signal` ContextVar in `client.py`** — `reset_stale_signal()`
  + `get_stale_signal()` are the public API. The server resets at the
  start of each tool call and reads at the end to propagate `stale=True`
  into the response.
- **New: `DataResponse.stale: bool` and `DataResponse.stale_reason: str | None`** —
  echoed in every response when serving a stale cache.
- **New: `DataResponse.truncated_at: int | None`** — placeholder field
  matching the sister-MCP envelope (used by register-style MCPs like
  asic-mcp; remains `None` for time-series-shaped abs-mcp data).
- **+4 regression tests** in `test_client.py`:
  1. 503 + stale cache → fallback + stale flag set
  2. ConnectError + stale cache → same
  3. 503 + empty cache → raises `ABSAPIError` (unchanged behaviour)
  4. `Cache.get_stale()` round-trip + TTL bypass verification
- 122 unit tests now (was 118 in 0.2.12).

This is the reference implementation; the same pattern is being
propagated to the 6 sister MCPs.

## 0.2.12 (2026-05-13)

Loop-audit value pass — two cross-portfolio polish items found by a
focused review of the customer surface.

- **New: `DatasetDetail.hidden_defaults`** — `describe_dataset` now
  surfaces hidden curated dimensions with their auto-applied default
  SDMX code in a dedicated `hidden_defaults` field. The LLM can see
  what's being assumed (e.g. for LF: `AGE=15+`, `TSEST=seasonally
  adjusted`, `FREQ=monthly`) and explain those assumptions to end users
  rather than silently injecting them. Pre-0.2.12 hidden dims were
  filtered out of the response entirely.
- **`start_period` / `end_period` accept int years (parity with
  rba-mcp 0.1.8).** MCP / LLM clients often send a year as a JSON
  number rather than a string (`start_period=2024` instead of
  `"2024"`). Pre-0.2.12 this errored at the Pydantic boundary. Now:
  the Annotated type is `str | int | None` and `_get_data_impl`
  coerces `int → str` transparently. Bool is explicitly excluded
  from coercion (`isinstance(True, int)` is `True` in Python) so
  True/False still raise a clean type error rather than becoming
  "1"/"0" periods.
- **Tests**: +3 regressions (hidden_defaults populated, int year
  accepted end-to-end, bool still rejected), plus renamed the prior
  "rejects non-string start_period" test to reflect the new behaviour.

## 0.2.11 (2026-05-12)

Glama Tool Definition Quality pass — every parameter on every tool now
carries an explicit `description` and `examples` in the MCP JSON schema,
and every docstring carries an `Examples:` + `When to use:` + `Returns:`
block. Targets the Glama sub-scores that were sitting at 2-3/5 on
`describe_dataset` and `latest` (Parameters, Usage Guidelines).

- **Annotated parameter schemas.** All 5 tools (`search_datasets`,
  `describe_dataset`, `get_data`, `latest`, `list_curated`) now use
  `Annotated[Type, Field(description=…, examples=[…])]` for every
  parameter. The FastMCP-generated JSON schema now exposes:
    - a human-readable description on each parameter
    - 2–5 worked examples per parameter
    - numeric bounds where applicable (`ge`, `le` on `limit`)
- **Richer docstrings.** Each tool gains worked code examples (real
  filter dicts, expected response shape), an explicit "When to use"
  section, and a Returns block. The 10 curated dataflows are listed by
  topic in `list_curated`'s docstring so an LLM can plan a multi-tool
  call without needing to invoke it first.
- No behavioural changes. All 117 unit tests + 48 live tests still
  green; schema verified end-to-end through the FastMCP Client.

## 0.2.10 (2026-05-12)

Cross-portfolio consistency pass — three fixes that bring abs-mcp to parity
with rba-mcp 0.1.6's gate-4 and licence-compliance guards. Surfaced by an
adversarial / domain-correctness / production-stress audit against the
shipped 0.2.9 wheel.

- **Fix: `DataResponse.attribution` now carries the CC-BY 4.0 string in
  every response body.** Previously only `source` ("Australian Bureau of
  Statistics") and `abs_url` were populated. CC-BY 4.0 requires the
  attribution to travel WITH the data, not just be reachable via a link.
  Matches rba-mcp's `DataResponse.attribution` shape. The string points
  at https://www.abs.gov.au/about/copyright-and-creative-commons.
- **Fix: cache self-heals on corruption / schema-mismatch.**
  `Cache._ensure_init()` now catches `sqlite3.DatabaseError` on initial
  schema setup and deletes-and-recreates the file. Previously, a corrupt
  `~/.abs-mcp/cache.db` (partial write after a crash, older-version schema,
  or user accident) would leak `sqlite3.DatabaseError("file is not a
  database")` to the MCP tool surface — a raw library exception escaping
  the contract, against gate 4. The cache is a performance optimisation,
  not a source of truth, so silently recreating it is always safe.
  Mirrors rba-mcp 0.1.2.
- **Fix: defensive `last_n > 0` guard in `ABSClient.get_data`.**
  Previously, `if last_n:` falsy-checked `last_n=0` and silently dropped
  it, returning a full fetch. Today this is internal-only (`latest()`
  hardcodes 1, the public `get_data` tool doesn't expose `last_n`), but
  the latent footgun is sealed against future use.
- **New: `DataResponse.server_version`** field echoed in every response
  (parity with rba-mcp 0.1.5). Set from `importlib.metadata.version
  ("abs-mcp")`. Makes it trivial to verify which wheel served the call
  when debugging uvx cache staleness.
- **Tests**: +4 regressions (2 cache self-heal in `test_cache.py`, 1
  attribution + server_version in `test_shaping.py`, 1 last_n guard in
  `test_client.py`). 117 unit tests now (was 113).

## 0.2.9 (2026-05-11)

Curated-feature-promise fix surfaced by a real-user QA pass.

- **ABS_ANNUAL_ERP_ASGS2021.region now accepts the ~2,985 sub-state ASGS
  codes the YAML promised.** The YAML description said *"pass a raw ASGS
  2021 code for sub-state regions (greater capital cities, SA4/SA3/SA2).
  2,985 codes available"*, but the curated translate-filters path only
  accepted values that were in the YAML's 14-entry value map (or whose
  SDMX code was in that same map). Passing a real SA2 code like
  `101021010` (Queanbeyan-East) was rejected with "Unknown value" —
  exactly the kind of bug that would make a property-analytics user stop
  trusting the tool.
- **New `permissive: true` YAML flag on `CuratedDimension`** lets a dim
  opt in to accepting raw SDMX codes that match a strict shape
  (`[A-Z0-9_-]+` with at least one digit) without having to enumerate
  every code in the value map. The digit requirement is load-bearing: it
  rules out uppercase typos of curated keys (`NSW`, `QUEENSLAND`) which
  on a permissive dim would otherwise be sent to ABS as raw codes and
  surface as opaque 404s. Lowercase typos (`queensland`) also fall
  through to the existing curated "Try one of:" hint.
- URL-injection guard from 0.2.7 still applies — a permissive value with
  `?`, `&`, `=`, `/`, etc. is rejected at the boundary, not sent to ABS.
- 9 new regression tests covering the SA2 path, typo preservation,
  injection block on permissive dims, and that non-permissive dims still
  reject unknown values cleanly. 161 tests (was 152 in 0.2.7).

## 0.2.8 (2026-05-11)

Docs polish — the artifact every successful MCP launch had.

- **Hero screenshot in README** showing Claude Desktop answering "What's the unemployment rate in NSW?" with the line chart, state-comparison bar chart, and analysis. The proof-of-utility every visitor needs in the first scroll.
- **"How it works" section** with a tool-call detail screenshot showing Claude's reasoning + the `latest` MCP call.
- **Tightened README first paragraph** — leads with what's unlocked ("get real, current numbers — not 'I don't have access to that data'") and keeps SDMX as the "how" not the "what".
- **Fixed `__version__` drift.** `src/abs_mcp/__init__.py` now reads from `importlib.metadata.version("abs-mcp")` so it can never go stale relative to `pyproject.toml`.

## 0.2.7 (2026-05-11)

Iteration 4. URL-injection guard on user-supplied filter values and period
strings. The dataset_id guard from 0.2.4 covered the path-prefix vector but
the dot-separated SDMX key and the period query string were still open.

- **Non-curated filter values are now rejected if they contain URL-unsafe
  characters.** Calling `get_data("ALC", {"REGION": "x?foo=bar"})` used to
  flow the raw value into the SDMX URL path, where `?` truncated the key
  and the rest got interpreted as query parameters — silently changing the
  request shape. Same for `&`, `/`, `#`, `+`, `.`, `=`, `%`, space, and
  `;`. Now: `ValueError` listing the SDMX code shape (`[A-Za-z0-9_-]+`)
  and pointing at multi-value lists for the multi-value case. Curated
  dataflows were never vulnerable — values are validated against the YAML
  codelist before reaching the URL.
- **`start_period` / `end_period` are now shape-checked.** A user passing
  `start_period="2024&format=jsonstat"` used to inject an extra query
  parameter into the ABS request. Now restricted to the same URL-safe
  pattern (digits, dashes, letters — covers `YYYY`, `YYYY-MM`, `YYYY-Q1`,
  `YYYY-S1`, `YYYY-MM-DD`). Permissive on ABS-period shape (the API will
  still 4xx semantic garbage), strict on URL safety.
- 22 new parametrised regression tests (152 total, was 130 in 0.2.6).

## 0.2.6 (2026-05-11)

Iteration 3 of the robustness audit. Closes the last of the
curated-vs-non-curated path asymmetries.

- **Non-curated dataflows now reject unknown filter keys.** Calling
  `get_data("ALC", {"NOT_A_DIM": "X"})` used to silently drop the unknown
  key inside `build_sdmx_key` (which only iterates the DSD's `dim_order`)
  and return unfiltered data while the response echoed the bogus key in
  `query`. A user thought they had filtered when they hadn't — a
  correctness footgun for an analytics tool. Now: `ValueError` listing
  the dataflow's valid SDMX dimensions, and suggesting `describe_dataset`.
- **Non-curated `_resolve_filters` matches the curated contract.** Strips
  whitespace on filter values, rejects empty lists, rejects empty values.
  Previously you could pass `{"REGION": [""]}` or `{"REGION": "  AUS  "}`
  on a non-curated dataflow and silently produce a malformed SDMX key.
- **`describe_dataset` no longer silently truncates large codelists.**
  `catalog.py:140` capped codelist values at 200; SA2-level geography
  codelists routinely exceed this. Removed the cap — LLM agents can
  handle the longer list.
- **Documented the `_safe_value` contract** so future readers know why
  NaN / None / unparseable values are coerced to `None` (ABS publishes
  missing-data sentinels).
- 6 new regression tests (130 total, was 124 in 0.2.5).
- Verified gate 5: warm-cache `latest()` runs at ~22ms (gate <50ms),
  cold-cache 191ms (gate <2000ms).

## 0.2.5 (2026-05-11)

Iteration 2 of the robustness audit — closes the type-validation gaps that
0.2.4 missed, plus two latent error-handling cracks.

- **`format`, `start_period`, `end_period` now type-validated** at the tool
  boundary. 0.2.4 added guards for `dataset_id` / `query` / `limit` / `filters`
  but missed these three. `format=1` used to crash on `.lower()`,
  `start_period=2024` (int) used to crash on the `start > end` comparison,
  and `end_period=["2024"]` crashed even later inside Pydantic when the
  response model tried to coerce the value back into the period field.
  Now: clean `ValueError` with the valid shape examples.
- **Non-curated filter coercion.** `get_data("ALC", {"REGION": [1, 2]})`
  used to raise a bare `TypeError` from `"+".join([1, 2])` deep inside
  `build_sdmx_key`. The non-curated branch of `_resolve_filters` now
  coerces list elements to strings, matching the curated branch's contract.
- **`sdmx.read_sdmx` errors now wrap as `ABSAPIError`.** If ABS returns
  a 200 with a body that isn't valid SDMX (schema drift, an HTML error
  page slipping past status checks, a truncated response), the parse
  exception used to escape the `ABSAPIError` contract that the server
  tools catch — surfacing as an unstructured crash. Now wrapped.
- **`Cache._ensure_init` is now lock-guarded.** Two concurrent first
  calls used to both pass the `if self._initialized` check and both run
  the schema-creation script. SQL is `CREATE … IF NOT EXISTS`, so no
  data loss, but the double-execute on every cold start is wasteful.
  Added `asyncio.Lock` + double-checked locking.
- 6 new regression tests (124 total, was 118 in 0.2.4).

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
