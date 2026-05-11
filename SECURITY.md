# Security Policy

## Supported versions

Only the latest minor release of `abs-mcp` is supported with security fixes. Older versions should be upgraded.

| Version | Supported |
|---|---|
| latest 0.2.x | ✅ |
| < 0.2.0 | ❌ |

## Reporting a vulnerability

**Do not file public GitHub issues for security vulnerabilities.**

Privately report via GitHub's [Security Advisories](https://github.com/Bigred97/abs-mcp/security/advisories/new) flow, or email `hvass97@gmail.com` with subject `[abs-mcp security]`.

Include:
- A clear description of the vulnerability
- A reproducer (minimal MCP call sequence or input that triggers it)
- The version of `abs-mcp` you tested against (`abs_mcp.__version__`)
- Your suggested fix, if you have one

You'll get an acknowledgement within 72 hours. Critical issues will be fixed and a patch release published within 7 days; lower-severity issues within 30 days. You'll be credited in the release notes unless you ask otherwise.

## Threat model

`abs-mcp` runs locally as an MCP stdio subprocess of your MCP client (Claude Desktop, Cursor, etc.). It:

- Reads no local files except its own SQLite cache at `~/.abs-mcp/cache.db`
- Makes outbound HTTPS requests only to `https://data.api.abs.gov.au/`
- Has URL-injection guards on every user-supplied identifier (table IDs, series IDs, period strings, filter values)
- Does not execute arbitrary code from untrusted input

The most realistic attack surfaces are:
- A malformed dataflow ID or filter value that escapes URL encoding (mitigated by `_DATASET_ID_PATTERN` and `_PERIOD_SAFE_PATTERN` regex guards)
- A compromised `pip install` chain (mitigated by the standard PyPI signing and the MIT-licensed open-source repo)
- A crafted SDMX response from a man-in-the-middle ABS API (mitigated by HTTPS + the SDMX parser raising on schema drift)

If you find an attack vector outside this list, please report it.
