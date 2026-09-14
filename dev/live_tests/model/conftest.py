from urllib.parse import urlsplit

import pytest

from ..infrastructure.round_two_lab import free_origin, open_lab
from .support import ModelJourney


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def model_lab(request):
    if not request.config.getoption("--live-management"):
        pytest.skip("Opt in with --live-management for real model-management HTTP journeys")
    async with open_lab(suite="management", local_connectors=False) as lab:
        root = lab.root / "model-peer"
        root.mkdir(mode=0o700)
        origin = free_origin()
        peer = await lab.spawn("dev.live_tests.model.peer", "--root", str(root), "--port", str(urlsplit(origin).port))
        await lab.ready(peer, origin)
        try:
            yield ModelJourney(lab, origin)
        finally:
            for directory in root.iterdir():
                (directory / "release").touch()
