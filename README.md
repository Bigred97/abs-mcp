# abs-mcp

An MCP server that wraps the [Australian Bureau of Statistics Data API](https://data.api.abs.gov.au/) and hides SDMX behind plain-English tools. Ask Claude "What's the unemployment rate in NSW?" and get a real answer with a source link, instead of a wall of SDMX codes.

Five tools, hand-curated mappings for Labour Force, CPI, Estimated Resident Population, Building Approvals, and Lending Indicators.

## Install

```bash
# After publish:
uvx abs-mcp

# Local dev install:
uv pip install -e .
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "abs": {
      "command": "uvx",
      "args": ["abs-mcp"]
    }
  }
}
```

For a local checkout (before PyPI publish):

```json
{
  "mcpServers": {
    "abs": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/abs-mcp", "abs-mcp"]
    }
  }
}
```

Restart Claude Desktop. The `abs` server appears in the tools panel with five tools.

### Cursor

Add to `~/.cursor/mcp.json` (or workspace `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "abs": {
      "command": "uvx",
      "args": ["abs-mcp"]
    }
  }
}
```

## Tools

| Tool | What it does |
|---|---|
| `search_datasets(query, limit=10)` | Fuzzy-search ABS dataflow names. Returns the top matches. |
| `describe_dataset(dataset_id)` | Plain-English description of a dataflow's dimensions and values. |
| `get_data(dataset_id, filters, start_period, end_period, format)` | Query a dataflow with filters. Returns clean records (default), grouped series, or CSV. |
| `latest(dataset_id, filters)` | Just the most recent observation(s) — wraps `get_data` with `lastNObservations=1`. |
| `list_curated()` | The five dataflow IDs that have hand-curated plain-English support. |

## Curated dataflows

For these five, `filters` accepts plain-English values (e.g. `"region": "nsw"` instead of `"REGION": "1"`):

- **LF** — Labour Force, monthly: employment, unemployment, participation by state/sex
- **CPI** — Consumer Price Index, quarterly inflation by capital city and category
- **ABS_ANNUAL_ERP_ASGS2021** — Estimated Resident Population, annual by state and sub-state geography
- **BA_GCCSA** — Building Approvals, monthly by state/capital region and building type
- **LEND_HOUSING** — Lending Indicators, monthly housing finance commitments by purpose, lender, and state

Any other ABS dataflow still works — pass raw SDMX dimension IDs and codes.

## Worked examples

**"What's the current unemployment rate in NSW?"**

Claude calls:
```
latest(dataset_id="LF", filters={"region": "nsw", "measure": "unemployment_rate"})
```

Returns:
```json
{
  "dataset_id": "LF",
  "dataset_name": "Labour Force",
  "query": {"region": "nsw", "measure": "unemployment_rate"},
  "period": {"start": "2026-03", "end": "2026-03"},
  "records": [
    {
      "period": "2026-03",
      "value": 4.2,
      "dimensions": {"measure": "Unemployment rate", "region": "New South Wales", "sex": "Persons"}
    }
  ],
  "source": "Australian Bureau of Statistics",
  "abs_url": "https://www.abs.gov.au/statistics/labour/employment-and-unemployment/labour-force-australia"
}
```

**"Show me NSW housing approvals over the last two years"**

```
get_data(dataset_id="BA_GCCSA", filters={"region": "nsw", "measure": "dwelling_units"}, start_period="2024")
```

**"Compare quarterly CPI in Sydney vs Melbourne"**

```
get_data(dataset_id="CPI", filters={"region": ["sydney", "melbourne"], "measure": "change_year"}, start_period="2023")
```

## Period formats

ABS uses different period formats per dataflow:
- Monthly (LF, BA_GCCSA, LEND_HOUSING): `"2026-03"`
- Quarterly (CPI): `"2024-Q1"`
- Annual (ABS_ANNUAL_ERP_ASGS2021): `"2024"`

Pass `start_period` / `end_period` in the matching format.

## Development

```bash
git clone https://github.com/hvass97/abs-mcp.git
cd abs-mcp
uv sync --extra dev
uv pip install -e .

# Unit tests (no network)
uv run pytest

# Live integration tests (hits real ABS API)
uv run pytest -m live
```

The SQLite cache lives at `~/.abs-mcp/cache.db`. Catalogue refreshes every 24h, codelists every 7 days, data responses every hour, latest 15 minutes. Delete the file to force a refresh.

## How it differs from existing ABS MCP servers

The one existing community option (`seansoreilly/abs`) exposes a single `query_dataset` tool that passes raw SDMX through. This package offers semantic tools and curated mappings for the highest-value dataflows so an LLM can answer real questions without you needing to know what `M13.3.1599.20.1.M` means.

## License

MIT — Harry Vass, 2026.
