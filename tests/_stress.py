"""Customer-style stress test — probes the API across 8 categories.

Each test produces a one-line PASS/FAIL/EXPECTED-FAIL line. Triaged manually:
  PASS          — works as expected
  EXPECTED-FAIL — the API raised a clean ValueError with a useful message
                  (this is good — surfacing bad input cleanly is correct)
  REAL BUG      — crashed, hung, returned wrong shape, or rejected valid input

Run: PYTHONPATH=src .venv/bin/python tests/_stress.py
"""
from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any

from abs_mcp import server


def fmt_result(label: str, result: Any) -> str:
    if hasattr(result, "records"):
        n = len(result.records)
        unit = result.unit or ""
        first = ""
        if n:
            r0 = result.records[0]
            if hasattr(r0, "value"):
                first = f" first={r0.value} {unit} ({r0.period})"
            else:
                first = f" first={str(r0)[:60]}"
        return f"PASS  [{label}] records={n}{first}"
    if isinstance(result, list):
        return f"PASS  [{label}] list[{len(result)}]"
    rs = str(result)
    if len(rs) > 80:
        rs = rs[:80] + "..."
    return f"PASS  [{label}] {rs}"


def fmt_error(label: str, exc: BaseException, expected: bool) -> str:
    msg = str(exc).split("\n")[0]
    if len(msg) > 100:
        msg = msg[:100] + "..."
    tag = "EXPECTED-FAIL" if expected else "REAL BUG"
    return f"{tag}  [{label}] {type(exc).__name__}: {msg}"


async def probe(label: str, coro, *, expect_fail: bool = False) -> str:
    try:
        result = await coro
        return fmt_result(label, result)
    except Exception as e:
        return fmt_error(label, e, expected=expect_fail)
    finally:
        await server.reset_client_for_tests()


async def run_serial(cases: list) -> list[str]:
    out = []
    for c in cases:
        if len(c) == 2:
            label, coro = c
            expect_fail = False
        else:
            label, coro, expect_fail = c
        out.append(await probe(label, coro, expect_fail=expect_fail))
    return out


async def run_concurrent(label_coro_pairs, concurrency: int = 5) -> list[str]:
    """Run N coroutines concurrently using a single shared client (no resets between)."""
    results: list[str] = []
    sem = asyncio.Semaphore(concurrency)

    async def one(label: str, coro):
        async with sem:
            try:
                r = await coro
                return fmt_result(label, r)
            except Exception as e:
                return fmt_error(label, e, expected=False)

    coros = [one(l, c) for l, c in label_coro_pairs]
    results = await asyncio.gather(*coros, return_exceptions=False)
    await server.reset_client_for_tests()
    return results


async def main():
    sections: dict[str, list[str]] = {}

    # --- 1. Input validation: dataset_id quirks ---
    cases = [
        ("dataset_id lowercase", server.latest(dataset_id="lf", filters={"region":"nsw","measure":"unemployment_rate"})),
        ("dataset_id mixed case", server.latest(dataset_id="Lf", filters={"region":"nsw","measure":"unemployment_rate"})),
        ("dataset_id with whitespace", server.latest(dataset_id=" LF ", filters={"region":"nsw","measure":"unemployment_rate"}), True),
        ("dataset_id empty string", server.latest(dataset_id="", filters={"region":"nsw"}), True),
        ("dataset_id with special chars", server.latest(dataset_id="LF;DROP TABLE", filters={}), True),
        ("dataset_id 1000 chars", server.latest(dataset_id="A"*1000, filters={}), True),
        ("dataset_id only digits", server.latest(dataset_id="123", filters={}), True),
    ]
    sections["1. dataset_id"] = await run_serial(cases)

    # --- 2. Filter edge cases ---
    cases = [
        ("filters None", server.latest(dataset_id="LF", filters=None)),
        ("filters empty dict", server.latest(dataset_id="LF", filters={})),
        ("filter unknown dim", server.get_data(dataset_id="LF", filters={"state":"nsw"}), True),
        ("filter unknown value", server.get_data(dataset_id="LF", filters={"region":"atlantis"}), True),
        ("filter mixed case value", server.get_data(dataset_id="LF", filters={"region":"NSW"}), True),  # we lowercase keys but values? Let's see
        ("filter int value", server.get_data(dataset_id="LF", filters={"region": 1}, start_period="2025")),
        ("filter empty list", server.get_data(dataset_id="LF", filters={"region":[]}, start_period="2025")),
        ("filter single-elem list", server.get_data(dataset_id="LF", filters={"region":["nsw"], "measure":"unemployment_rate"}, start_period="2025")),
        ("filter multi-region", server.get_data(dataset_id="LF", filters={"region":["nsw","vic","qld"], "measure":"unemployment_rate"}, start_period="2025")),
        ("filter None value", server.get_data(dataset_id="LF", filters={"region": None}), True),
        ("filter 50 region values", server.get_data(dataset_id="LF", filters={"region":["nsw"]*50}, start_period="2025")),
        ("filter raw SDMX code passthrough", server.get_data(dataset_id="LF", filters={"region":"1", "measure":"M13"}, start_period="2025")),
        ("filter case-insensitive value (NSW vs nsw)", server.get_data(dataset_id="LF", filters={"region":"NSW"}), True),
    ]
    sections["2. filters"] = await run_serial(cases)

    # --- 3. Period edge cases ---
    cases = [
        ("start before data exists", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="1900")),
        ("start in far future", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="2099"), True),
        ("garbage period", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="banana"), True),
        ("end before start", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="2025", end_period="2020")),
        ("very wide range 1980-2026", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="1980", end_period="2026")),
        ("quarterly period for monthly dataflow", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="2024-Q1")),
        ("monthly period for quarterly dataflow", server.get_data(dataset_id="CPI", filters={"region":"australia","measure":"change_year"}, start_period="2024-03")),
        ("ISO date instead of period", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="2024-01-15"), True),
    ]
    sections["3. periods"] = await run_serial(cases)

    # --- 4. Format edge cases ---
    cases = [
        ("format=records explicit", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="2024", format="records")),
        ("format=series", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="2024", format="series")),
        ("format=csv", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="2024", format="csv")),
        ("format=invalid", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="2024", format="JSON")),
        ("format=CSV uppercase", server.get_data(dataset_id="LF", filters={"region":"nsw","measure":"unemployment_rate"}, start_period="2024", format="CSV")),
    ]
    sections["4. formats"] = await run_serial(cases)

    # --- 5. search_datasets edges ---
    cases = [
        ("search empty", server.search_datasets(query="", limit=5), True),
        ("search whitespace", server.search_datasets(query="   ", limit=5), True),
        ("search single char", server.search_datasets(query="a", limit=5)),
        ("search limit=0", server.search_datasets(query="cpi", limit=0)),
        ("search limit=10000", server.search_datasets(query="cpi", limit=10000)),
        ("search unicode", server.search_datasets(query="日本語", limit=5)),
        ("search emoji", server.search_datasets(query="🏠💰", limit=5)),
        ("search XSS attempt", server.search_datasets(query="<script>alert(1)</script>", limit=5)),
        ("search SQL injection", server.search_datasets(query="'; DROP TABLE--", limit=5)),
        ("search 1000-char query", server.search_datasets(query="x"*1000, limit=5)),
    ]
    sections["5. search"] = await run_serial(cases)

    # --- 6. describe_dataset edges ---
    cases = [
        ("describe LF curated", server.describe_dataset(dataset_id="LF")),
        ("describe random non-curated", server.describe_dataset(dataset_id="ALC")),
        ("describe with whitespace", server.describe_dataset(dataset_id=" LF "), True),
        ("describe huge codelist", server.describe_dataset(dataset_id="ABS_ANNUAL_ERP_LGA2024")),  # has many regions
        ("describe garbage", server.describe_dataset(dataset_id="X" * 100), True),
    ]
    sections["6. describe"] = await run_serial(cases)

    # --- 7. Cross-dataflow & combinations ---
    cases = []
    for ds_id in server.list_curated():
        cases.append((f"latest {ds_id} no filters", server.latest(dataset_id=ds_id, filters={})))
    sections["7. every curated × no filters"] = await run_serial(cases)

    # --- 8. Concurrent calls ---
    concurrent_calls = [
        (f"conc[{i}] {ds}", server.latest(dataset_id=ds, filters={"region":"nsw"} if ds not in ("ANA_AGG", "AWE") else {"region":"australia"}))
        for i, ds in enumerate(["LF","CPI","WPI","JV","BA_GCCSA","LEND_HOUSING","ERP_Q","ANA_AGG","AWE"] * 2)  # 18 calls
    ]
    print("\n=== 8. concurrent (18 calls, semaphore=5) ===")
    t0 = time.time()
    conc_results = await run_concurrent(concurrent_calls, concurrency=5)
    elapsed = time.time() - t0
    sections["8. concurrent (18 calls)"] = conc_results + [f"  -- elapsed {elapsed:.1f}s --"]

    # --- Print all ---
    for section, lines in sections.items():
        print(f"\n=== {section} ===")
        for line in lines:
            print(f"  {line}")

    # --- Counts ---
    all_lines = [l for ls in sections.values() for l in ls]
    n_real_bugs = sum(1 for l in all_lines if l.startswith("REAL BUG"))
    n_pass = sum(1 for l in all_lines if l.startswith("PASS"))
    n_expected = sum(1 for l in all_lines if l.startswith("EXPECTED-FAIL"))
    print(f"\n========== SUMMARY ==========")
    print(f"  PASS:           {n_pass}")
    print(f"  EXPECTED-FAIL:  {n_expected} (clean ValueErrors — these are good)")
    print(f"  REAL BUG:       {n_real_bugs}")


if __name__ == "__main__":
    asyncio.run(main())
