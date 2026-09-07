from __future__ import annotations

import httpx2
import pytest

from .client import LiveClient
from .config import load_config


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="Run real local Foundation HTTP journeys")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Opt in with make live-test or --live; no live network calls are made by default")
    config = load_config()
    async with httpx2.AsyncClient(
        base_url=config["control_url"],
        headers={"Authorization": f"Bearer {config['token']}"},
        timeout=15,
        trust_env=False,
        follow_redirects=False,
    ) as http:
        client = LiveClient(config, http)
        await client.preflight()
        try:
            yield client
        finally:
            await client.cleanup()
