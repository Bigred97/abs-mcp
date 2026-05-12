# Changelog

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
