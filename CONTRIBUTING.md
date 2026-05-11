# Contributing to abs-mcp

Thanks for considering a contribution. This is an indie open-source project — every PR is read.

## Quick start

```bash
git clone https://github.com/Bigred97/abs-mcp.git
cd abs-mcp
uv sync --extra dev
uv pip install -e .

# Unit tests (no network)
uv run pytest

# Live integration tests (hits the real ABS API)
uv run pytest -m live
```

## What kind of contribution helps?

| Most welcome | Be cautious |
|---|---|
| Bug fixes (with a regression test) | Adding new tools to the MCP surface |
| New curated dataflows (one YAML per dataflow in `src/abs_mcp/data/curated/`) | Refactors that touch >3 modules |
| Better error messages with actionable hints | Changes that break the public response shape |
| Docs / README improvements | Pulling in new dependencies |
| Performance fixes (with a benchmark) | Changes to the YAML schema |

## Adding a curated dataflow

1. Identify the ABS dataflow ID via `search_datasets()` against the live API
2. Inspect its data structure: `https://data.api.abs.gov.au/rest/datastructure/ABS/{id}?references=all`
3. Hand-write the YAML under `src/abs_mcp/data/curated/{ID}.yaml` following the pattern of existing files (LF.yaml is the cleanest reference)
4. Verify your default-dim values actually return data (some SDMX combinations exist as codelist entries but produce empty series)
5. Add a parametrised entry in `tests/test_integration.py::test_every_curated_dataflow_returns_useful_record` with the expected unit and a plausibility range
6. Run `uv run pytest -m live` and confirm green

## PR checklist

- [ ] All tests pass (`uv run pytest -m "not live"` minimum; `uv run pytest -m live` if you touched the API surface or added curation)
- [ ] New code has tests
- [ ] No new dependencies (or they're justified in the PR body)
- [ ] CHANGELOG.md updated under the Unreleased section
- [ ] If you changed default behaviour, the README "Worked examples" still produces the documented values

## Style

- Python 3.11+, `from __future__ import annotations` at file top
- Pydantic v2 models — use `Field(default_factory=...)` for mutable defaults
- Docstrings in module-level summary; functions only when non-obvious
- No comments restating the code; comments explain *why*

## Filing bugs

Use the bug-report issue template. Bugs filed via the template get triaged within a week; freeform issues may sit longer.

## Discussions vs Issues

- **Issue**: bug, feature request, security report
- **Discussion**: question, idea you're not sure about, sharing how you're using the package

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be kind.
