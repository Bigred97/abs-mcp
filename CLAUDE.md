# abs-mcp

Sister MCP in the Australian Public Data stack. See `../CLAUDE.md` for
portfolio-wide conventions; this file captures repo-specific details
plus the cross-sister discipline.

## Source

| | |
|--|--|
| Source agency | Australian Bureau of Statistics (ABS) |
| Source URL | https://data.api.abs.gov.au/ |
| Data format | SDMX-XML 2.1 via the ABS Data API |
| Licence | CC-BY 4.0 International |
| Licence URL | https://creativecommons.org/licenses/by/4.0/ |
| Python module | `abs_mcp` |
| PyPI package | `abs-mcp` |
| GitHub | https://github.com/Bigred97/abs-mcp |

## Curated datasets (13)

LF · CPI · WPI · JV · AWE · ANA_AGG · BA_GCCSA · LEND_HOUSING · ERP_Q · ABS_ANNUAL_ERP_ASGS2021 · C21_G02_SA2 · C21_G02_POA · RT (historical, ceased Jul 2025)

## Repo-specific module set

Required (every sister): `server.py`, `models.py`, `curated.py`, `client.py`, `cache.py`, `shaping.py`, `data/curated/*.yaml`

Repo-specific extras:
- `catalog.py — search ranking across ~1,200 ABS dataflows`

## Repo-specific gotchas

- Uses the `sdmx1` Python library to parse SDMX-XML — kept out of every other sister's deps.
- Hidden dimensions (e.g. SEX, AGE, ADJUSTMENT) carry defaults so users don't have to know SDMX boilerplate.
- Curated value keys are lowercase plain-English (`nsw`, `unemployment_rate`); raw SDMX codes also accepted as escape hatch.
- `permissive: true` on `ABS_ANNUAL_ERP_ASGS2021.region` lets ASGS sub-state codes (SA2/SA3/SA4) pass through without enumeration.

---

## The core 5-tool surface (uniform across sisters — mandatory)

The 5 below are the uniform brand. Additional tools (e.g. `top_n`, `stats`) are
allowed where the data shape genuinely needs them — they must use the same
`Annotated[Field]` discipline and `DataResponse` envelope as the core 5.

1. `search_*(query, limit)` — fuzzy search across known datasets/tables/locations
2. `describe_*(id)` — schema + filter values + source URL
3. `get_data(id, filters, start_period, end_period, format)` — query
4. `latest(id, filters)` — current snapshot (caps to `limit` for register data)
5. `list_curated()` — enumerate supported IDs

Every parameter uses `Annotated[Type, Field(description=..., examples=[...])]`.
This is the Glama Tool Definition Quality requirement — non-negotiable.

## Trust contract (every DataResponse carries)

```
source             "Australian Bureau of Statistics (ABS)"
source_url         https://data.api.abs.gov.au/
attribution        full CC-BY 4.0 International attribution string with licence URL
retrieved_at       UTC timestamp
server_version     importlib.metadata.version("abs-mcp")
stale              True when serving cached fallback after upstream error
stale_reason       human-readable when stale=True
truncated_at       int | None — set when latest() caps a large response
```

## The 5 quality dimensions (audit every release against these)

1. **Semantic Clarity** — verb-noun tool names, Annotated[Field] with examples, rich docstrings (Examples + When to use + Returns blocks), `pattern=` constraints where IDs have known shapes
2. **Data Pruning** — <10k tokens for typical responses, `latest()` caps register dumps via `limit` + `truncated_at`, no leaked SDMX/Excel boilerplate dims in records
3. **Cross-Agency Joining** — uniform period format conventions (YYYY / YYYY-MM / YYYY-Q1 / YYYY-S1 / YYYY-MM-DD); standardise on ASGS, postcode, ABN, ANZSIC where the data supports it
4. **Reliability + Caching** — SQLite cache TTLs (15min latest / 1h data / 24h catalogue / 7d structure), self-heal on `sqlite3.DatabaseError`, **graceful degradation**: when upstream fails, fall back to last cached payload and set `stale=True, stale_reason="..."` rather than raising
5. **Deterministic Error Handling** — every `ValueError` carries a "Try X" / "Did you mean X?" / "Valid options: ..." hint that suggests the correction, not just describes the rejection

## Test taxonomy

Required: `test_cache.py`, `test_curated.py`, `test_server_validation.py`, `test_shaping.py`, `test_integration.py` (live, `@pytest.mark.live`)
Recommended: `test_client.py`, `test_mcp_protocol.py`, `test_discovery.py`, `test_resilience.py`, `test_edge_inputs.py`, `test_concurrency.py`

Zero-flake bar: full unit suite must run 10× consecutively green before tagging a release.

## Release workflow (Trusted Publishing via OIDC, no API tokens in CI)

```
1. Bump version in pyproject.toml (semver)
2. Update CHANGELOG.md (latest entry at top, semver headings)
3. uv run pytest × 10 — zero flakes
4. git commit -am "X.Y.Z: <one-line reason>"
5. git tag -a vX.Y.Z -m "X.Y.Z: <reason>"
6. git push origin main vX.Y.Z
7. release.yml fires → builds → OIDC publish → PyPI
```

PyPI new-project rate limit: 5/day per account; not an issue for existing
projects (only counts NEW package names).

## Anti-patterns — DO NOT do these

- Don't add tools that duplicate or rename the core 5; their names/shapes are fixed. Extras are allowed only where the data shape genuinely needs them (e.g. `top_n`, `stats`) and must follow the same `Annotated[Field]` + `DataResponse` discipline
- Don't add new top-level dependencies beyond what other sisters use (httpx, pydantic, fastmcp, aiosqlite, rapidfuzz, pyyaml, + parsing-library if needed)
- Don't bundle large XLSX/CSV fixtures in the wheel; cache at runtime
- Don't ship without 10 consecutive zero-flake pytest runs
- Don't echo PyPI tokens / PATs in tool output, commit messages, or CHANGELOG
- Don't classify a slow source API (>2s cold) as a bug; only flag >10s or actual errors
- Don't widen scope mid-audit-loop; loops are fix-only

## Common operations

```bash
cd .                                                       # in the repo
uv sync --extra dev                                        # install deps
uv run pytest                                              # unit tests
uv run pytest -m live                                      # live tests too
uvx --refresh --from abs-mcp==<ver> python -c "..."         # smoke a published wheel
```
