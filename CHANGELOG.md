# Changelog

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
