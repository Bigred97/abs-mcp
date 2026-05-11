"""Shared fixtures.

Resets the server's module-level ABSClient between tests so a closed event
loop from one test doesn't poison the next one.
"""
from __future__ import annotations

import pytest

from abs_mcp import server


@pytest.fixture(autouse=True)
async def _reset_server_client():
    yield
    await server.reset_client_for_tests()
