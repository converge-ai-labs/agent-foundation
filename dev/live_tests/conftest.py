from __future__ import annotations

import httpx2
import pytest

from .client import LiveClient
from .config import load_config


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="Run real local Foundation HTTP journeys")
    parser.addoption("--live-round-two", action="store_true", help="Run isolated process and dependency fault journeys")
    parser.addoption("--live-management", action="store_true", help="Run isolated Service/Harness management journeys")


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


@pytest.fixture
async def round_two(request):
    if not request.config.getoption("--live-round-two"):
        pytest.skip("Opt in with make live-test-round-two; no fault injection runs by default")
    from .round_two_lab import open_lab

    async with open_lab() as lab:
        yield lab


@pytest.fixture
async def management(request):
    if not request.config.getoption("--live-management"):
        pytest.skip("Opt in with make live-test-management; no management resources are changed by default")
    from .management_support import ManagementJourney
    from .round_two_lab import open_lab

    async with open_lab(suite="management") as lab:
        yield ManagementJourney(lab)
