# Changelog

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
