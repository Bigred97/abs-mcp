"""Polish audit — checks what an LLM/user actually experiences.

Six dimensions:
  1. Tool schema quality (descriptions, types, required fields)
  2. describe_dataset completeness for all 10 curated
  3. search_datasets relevance for common AU queries
  4. Performance under sustained load (100 sequential warm-cache calls)
  5. Error-message actionability
  6. Cross-dataflow consistency

Run: PYTHONPATH=src .venv/bin/python tests/_audit.py
"""
from __future__ import annotations

import asyncio
import time

from fastmcp import Client

from abs_mcp import server


async def section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


async def audit_tool_schemas():
    await section("1. TOOL SCHEMAS — what does an LLM see?")
    async with Client(server.mcp) as c:
        tools = await c.list_tools()
        for t in sorted(tools, key=lambda x: x.name):
            schema = t.inputSchema or {}
            props = schema.get("properties", {})
            required = schema.get("required", [])
            desc_len = len(t.description or "")
            print(f"\n[{t.name}]  desc_len={desc_len}")
            if desc_len < 50:
                print("  WARN: description is short — LLM may not know when to call this")
            if not props and t.name not in ("list_curated",):
                print("  WARN: no input properties documented")
            for pname, pspec in props.items():
                ptype = pspec.get("type") or pspec.get("anyOf", "?")
                pdesc = pspec.get("description", "")
                req = "*" if pname in required else " "
                pdesc_short = pdesc[:60] + "..." if len(pdesc) > 60 else pdesc
                print(f"  {req} {pname:15} {str(ptype)[:30]:30}  {pdesc_short}")


async def audit_describe_all_curated():
    await section("2. describe_dataset COMPLETENESS — every curated dataflow")
    issues = []
    for ds_id in server.list_curated():
        try:
            d = await server.describe_dataset(dataset_id=ds_id)
            visible_dims = [dim.name for dim in d.dimensions]
            n_total_values = sum(len(dim.values) for dim in d.dimensions)
            print(f"  {ds_id:30} dims={visible_dims}  total_values={n_total_values}  desc_len={len(d.description)}")
            if not d.description or len(d.description) < 30:
                issues.append(f"{ds_id}: description too short ({len(d.description)} chars)")
            if not visible_dims:
                issues.append(f"{ds_id}: no visible dimensions exposed!")
            if n_total_values == 0:
                issues.append(f"{ds_id}: no filter values exposed!")
            for dim in d.dimensions:
                if not dim.values:
                    issues.append(f"{ds_id}.{dim.name}: empty values list")
        except Exception as e:
            issues.append(f"{ds_id}: describe failed — {e}")
        await server.reset_client_for_tests()
    if issues:
        print("\n  ISSUES:")
        for i in issues:
            print(f"   - {i}")
    return issues


async def audit_search_relevance():
    await section("3. search_datasets RELEVANCE — common queries find the right dataflow")
    expected = {
        "unemployment": "LF",
        "labour force": "LF",
        "inflation": "CPI",
        "consumer prices": "CPI",
        "wage growth": "WPI",
        "earnings": "AWE",
        "job vacancies": "JV",
        "gdp": "ANA_AGG",
        "national accounts": "ANA_AGG",
        "population": ["ABS_ANNUAL_ERP_ASGS2021", "ERP_Q"],
        "building approvals": "BA_GCCSA",
        "housing finance": "LEND_HOUSING",
        "mortgage": "LEND_HOUSING",
    }
    issues = []
    for query, want in expected.items():
        try:
            results = await server.search_datasets(query=query, limit=5)
            top_ids = [r.id for r in results]
            wants = want if isinstance(want, list) else [want]
            found_at = next((i for i, w in enumerate(top_ids) if w in wants), -1)
            if found_at == -1:
                issues.append(f"'{query}' → {wants} NOT in top 5: {top_ids}")
                print(f"  MISS  '{query:25}'  expected {wants}, top5={top_ids}")
            elif found_at == 0:
                print(f"  TOP   '{query:25}'  → {top_ids[0]}")
            else:
                print(f"  OK#{found_at+1} '{query:25}'  → {top_ids[0]} (wanted {wants}, found at rank {found_at+1})")
        except Exception as e:
            issues.append(f"'{query}': {e}")
        await server.reset_client_for_tests()
    return issues


async def audit_performance():
    await section("4. PERFORMANCE — 50 warm-cache reads")
    # Warm the cache once
    await server.latest(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate","sex":"persons"})
    # Time 50 identical calls
    t0 = time.time()
    for _ in range(50):
        await server.latest(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate","sex":"persons"})
    elapsed = time.time() - t0
    per_call_ms = (elapsed / 50) * 1000
    print(f"  50 warm-cache calls: {elapsed:.2f}s  →  {per_call_ms:.1f}ms/call")
    if per_call_ms > 500:
        print(f"  WARN: per-call latency is high (>{500}ms)")
    elif per_call_ms < 50:
        print(f"  OK: warm cache is fast ({per_call_ms:.0f}ms — sub-50ms target met)")
    await server.reset_client_for_tests()
    # Test 10 different curated dataflows in a row (mix of warm/cold caches)
    t0 = time.time()
    for ds_id in server.list_curated():
        try:
            await server.latest(dataset_id=ds_id, filters={})
        except Exception:
            pass  # Some may fail without filters; we just want the perf signal
    elapsed = time.time() - t0
    print(f"  10 distinct dataflows: {elapsed:.2f}s ({elapsed/10*1000:.0f}ms/call)")


async def audit_error_messages():
    await section("5. ERROR MESSAGES — actionability")
    cases = [
        ("unknown filter dim", server.get_data(dataset_id="LF", filters={"state":"nsw"})),
        ("unknown filter value", server.get_data(dataset_id="LF", filters={"region":"queensland"})),
        ("garbage dataset id", server.describe_dataset(dataset_id="THIS_DOES_NOT_EXIST")),
        ("invalid format", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, format="JSON")),
        ("end<start period", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="2025", end_period="2020")),
        ("empty query", server.search_datasets(query="", limit=5)),
    ]
    issues = []
    for label, coro in cases:
        try:
            await coro
            issues.append(f"{label}: did NOT raise (silent acceptance)")
            print(f"  SILENT {label}")
        except Exception as e:
            msg = str(e)
            actionable_signals = ["Try", "did you mean", "Valid options", "Try one of", "Try search_datasets"]
            has_hint = any(s.lower() in msg.lower() for s in actionable_signals)
            print(f"  {'GOOD ' if has_hint else 'WEAK '} {label}: {msg[:90]}")
            if not has_hint:
                issues.append(f"{label}: error message lacks actionable hint — {msg[:80]}")
        await server.reset_client_for_tests()
    return issues


async def audit_cross_dataflow_consistency():
    await section("6. CROSS-DATAFLOW CONSISTENCY")
    issues = []
    queries = [
        ("LF", {"region":"nsw","measure":"unemployment_rate","sex":"persons"}),
        ("CPI", {"region":"australia","measure":"change_year"}),
        ("WPI", {"region":"australia","measure":"change_year","adjustment":"seasonally_adjusted"}),
        ("JV", {"region":"nsw","measure":"vacancies"}),
        ("BA_GCCSA", {"region":"nsw","measure":"dwelling_units"}),
        ("LEND_HOUSING", {"region":"nsw","measure":"value"}),
        ("WPI", {"region":"nsw"}),
        ("AWE", {"region":"australia"}),
        ("ANA_AGG", {"series":"gdp","measure":"change"}),
        ("ERP_Q", {"region":"nsw"}),
        ("ABS_ANNUAL_ERP_ASGS2021", {"region":"nsw","region_type":"states"}),
    ]
    for ds_id, filters in queries:
        try:
            r = await server.latest(dataset_id=ds_id, filters=filters)
            obs = r.records[0] if r.records else None
            checks = {
                "has_value": obs and obs.value is not None,
                "has_period": obs and bool(obs.period),
                "has_unit": obs and bool(obs.unit),
                "has_response_unit": bool(r.unit),
                "has_abs_url": bool(r.abs_url),
                "has_dataset_name": bool(r.dataset_name),
                "no_default_keys": not any(k.startswith("_default") for k in r.query),
            }
            failures = [k for k, ok in checks.items() if not ok]
            if failures:
                print(f"  ISSUES {ds_id:30} {filters}  missing: {failures}")
                issues.append(f"{ds_id}: missing {failures}")
            else:
                print(f"  OK     {ds_id:30} value={obs.value} unit={obs.unit}")
        except Exception as e:
            issues.append(f"{ds_id}: {e}")
            print(f"  FAIL   {ds_id:30} {e}")
        await server.reset_client_for_tests()
    return issues


async def main():
    all_issues: list[str] = []
    await audit_tool_schemas()
    all_issues += await audit_describe_all_curated()
    all_issues += await audit_search_relevance()
    await audit_performance()
    all_issues += await audit_error_messages()
    all_issues += await audit_cross_dataflow_consistency()

    print("\n" + "="*70)
    print("AUDIT SUMMARY")
    print("="*70)
    if not all_issues:
        print("  No issues found.")
    else:
        print(f"  {len(all_issues)} issues:")
        for i in all_issues:
            print(f"   - {i}")


if __name__ == "__main__":
    asyncio.run(main())
