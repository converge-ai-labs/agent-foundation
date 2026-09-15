"""Each Skill module owns an isolated lab; every case releases its own pending work."""

import pytest

from ..infrastructure.round_two_lab import open_lab
from .support import SkillJourney


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def skill_lab(request):
    if not request.config.getoption("--live-management"):
        pytest.skip("Opt in with --live-management for real Skill journeys")
    async with open_lab(suite="management", environment_workers=True, run_faults={"skills": True}) as lab:
        yield lab


@pytest.fixture
async def skills(skill_lab):
    journey = SkillJourney(skill_lab)
    try:
        yield journey
    finally:
        journey.release_all()
        await journey.live.cleanup()
