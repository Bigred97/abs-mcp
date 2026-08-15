"""Regression tests for the per-thread `_client` cache.

Pinned to the fix for the intermittent `RuntimeError: Event loop is closed`
observed in production on ausdata-api (a multi-loop host that wraps this
MCP and calls `asyncio.run(_get_data_impl(...))` from a worker thread per
request).

Root cause was a module-global `_client` that bound to the FIRST event
loop to construct it; subsequent calls from other worker threads (each
with a fresh loop) tripped httpx's internal asyncio resources with
`RuntimeError: Event loop is closed`. The fix moves the cache to
`threading.local()` so each thread gets its own client bound to its own
loop.
"""
from __future__ import annotations

import asyncio
import threading

from abs_mcp import server


def test_client_is_per_thread():
    """Each thread gets its own client instance.

    Confirms the fix for the `Event loop is closed` bug when gateways
    call us from worker threads with fresh loops per invocation.
    """
    # Clear the calling thread's client so the assertions don't pick up
    # state from earlier tests in the same process.
    asyncio.run(server.reset_client_for_tests())

    # Hold the actual client OBJECTS, not id() snapshots: a freed client's
    # address can be reused by the next allocation, so comparing ids of
    # dead objects flakes (~1 in 10 gate runs). Live references make `is`
    # comparisons sound.
    clients_by_thread: dict[int, object] = {}
    errors: list[BaseException] = []

    def capture() -> None:
        try:
            # Each worker thread runs its own fresh loop, mirroring the
            # ausdata-api gateway invocation pattern.
            client = asyncio.run(server._get_client())
            clients_by_thread[threading.get_ident()] = client
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=capture)
    t2 = threading.Thread(target=capture)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"thread workers raised: {errors!r}"
    assert len(clients_by_thread) == 2, "expected two distinct worker threads"
    c1, c2 = clients_by_thread.values()
    assert c1 is not c2, "expected distinct client instances per thread"


def test_reset_client_for_tests_is_thread_scoped():
    """`reset_client_for_tests` clears only the calling thread's client.

    Other threads' clients must remain intact; otherwise a test running
    in parallel could yank the client out from under a concurrent thread.
    """
    asyncio.run(server.reset_client_for_tests())

    # Hold OBJECTS, never bare id() values: the pre-reset main client and
    # the dead worker thread's client get garbage-collected, and CPython
    # reuses freed addresses — id(new) == id(old) then fails the identity
    # asserts spuriously (observed 1-in-10 in the release gate). Keeping
    # references pins the addresses, making identity comparison sound.
    worker_clients: list[object] = []

    established = threading.Event()
    main_reset_done = threading.Event()
    worker_resampled = threading.Event()

    def worker() -> None:
        async def _do() -> None:
            c = await server._get_client()
            worker_clients.append(c)
            established.set()
            main_reset_done.wait(timeout=5.0)
            c2 = await server._get_client()
            worker_clients.append(c2)
            worker_resampled.set()

        asyncio.run(_do())

    t = threading.Thread(target=worker)
    t.start()
    assert established.wait(timeout=5.0), "worker did not establish a client"

    main_client_before = asyncio.run(server._get_client())
    asyncio.run(server.reset_client_for_tests())
    main_reset_done.set()

    assert worker_resampled.wait(timeout=5.0), "worker did not re-sample its client"
    t.join()

    assert len(worker_clients) == 2
    assert worker_clients[0] is worker_clients[1], (
        "worker thread's client was cleared by a reset on a different thread"
    )
    main_client_after = asyncio.run(server._get_client())
    assert main_client_after is not main_client_before
    assert main_client_after is not worker_clients[0]
