from __future__ import annotations

import anyio
import httpx2
import pytest

from .client import LiveClient
from .config import load_config


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="Run real local Foundation HTTP journeys")
    parser.addoption("--live-round-two", action="store_true", help="Run isolated process and dependency fault journeys")
    parser.addoption("--live-management", action="store_true", help="Run isolated Service/Harness management journeys")
    parser.addoption("--live-providers", action="store_true", help="Run configured real-provider integration journeys")
    parser.addoption("--live-slack", action="store_true", help="Authorize OpenConnector Slack and run a read-only tool")
    parser.addoption("--live-environments", action="store_true", help="Run the five-backend Environment matrix")
    parser.addoption("--live-performance", action="store_true", help="Run disposable long-session latency measurements")
    parser.addoption(
        "--session-message-bytes", type=int, default=1024, help="ASCII padding bytes per real input/output"
    )
    parser.addoption("--session-runs", default="1,1000,10000", help="Checkpoints along one real sequential Run chain")
    parser.addoption("--session-samples", type=int, default=5, help="Measured repetitions per operation, after warmup")


@pytest.fixture
async def long_session(request):
    if not request.config.getoption("--live-performance"):
        pytest.skip("Opt in with make live-test-performance; no benchmark infrastructure starts by default")
    message_bytes = request.config.getoption("--session-message-bytes")
    samples = request.config.getoption("--session-samples")
    try:
        runs = sorted(set(int(value) for value in request.config.getoption("--session-runs").split(",")))
        assert runs and all(1 <= value <= 100000 for value in runs)
    except (ValueError, AssertionError) as error:
        raise pytest.UsageError("--session-runs requires integers between 1 and 100000") from error
    if not 1 <= message_bytes <= 2048 or not 1 <= samples <= 1000:
        raise pytest.UsageError("Require message bytes 1..2048, samples 1..1000")
    from .round_two_lab import open_lab

    async with open_lab(long_session={"message_bytes": message_bytes, "context_window": 32768}) as lab:
        lab.client.http.timeout = httpx2.Timeout(120)
        yield lab, runs, samples


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


@pytest.fixture
async def run_faults(request):
    if not request.config.getoption("--live-round-two"):
        pytest.skip("Opt in with --live-round-two for process and persistence fault tests")
    from .round_two_lab import open_lab
    from .run_fault_support import RunFaultJourney

    options = getattr(request, "param", {})
    async with open_lab(suite="management", run_faults=options) as lab:
        journey = RunFaultJourney(lab)
        await journey.setup()
        try:
            yield journey
        finally:
            journey.close_barriers()


@pytest.fixture
async def control(request):
    if not request.config.getoption("--live-round-two"):
        pytest.skip("Opt in with --live-round-two for control transition and concurrency journeys")
    from .control_support import ControlJourney
    from .round_two_lab import open_lab

    options = {"control": getattr(request, "param", {}), "identity_management": True}
    async with open_lab(suite="management", run_faults=options) as lab:
        journey = ControlJourney(lab)
        await journey.setup()
        try:
            yield journey
        finally:
            journey.close_barriers()


@pytest.fixture
async def multiworker(management):
    """Three owned one-slot Workers; retain the management opt-in and cleanup boundary."""
    management.live.timeout = 180
    await management.lab.start_worker()
    await management.lab.start_worker()
    return management


@pytest.fixture
async def configured_provider(request):
    selection, upstream_model = request.param if isinstance(request.param, tuple) else (request.param, None)
    slack = selection == "slack"
    if slack and not request.config.getoption("--live-slack"):
        pytest.skip("Opt in with --live-slack; this journey requires interactive Slack OAuth")
    if not slack and not any(
        request.config.getoption(option)
        for option in ("--live", "--live-round-two", "--live-management", "--live-providers")
    ):
        pytest.skip("Opt in with a live-test target; private Provider configuration is not read by offline checks")
    from .provider_config import OpenConnectorSettings, load_provider_settings

    section = "connector" if slack else selection
    settings = getattr(load_provider_settings(), section)
    if slack and (not isinstance(settings, OpenConnectorSettings) or "slack" not in settings.services):
        pytest.fail("--live-slack requires an openconnector configuration with slack in services")
    if settings is None:
        pytest.skip(f"Optional {section} Provider is not configured; existing defaults are unchanged")
    if upstream_model is not None:
        if settings.provider != "openrouter":
            pytest.skip("The GPT/Gemini/Claude matrix requires model.provider=openrouter")
        settings = settings.model_copy(update={"model": upstream_model})
    from .real_providers import configured_provider_lab

    async with configured_provider_lab("search" if section == "brave_search" else section, settings) as configured:
        yield configured


@pytest.fixture(scope="module")
def e2b_settings(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in with --live-environments for real E2B lifecycle tests")
    from .provider_config import load_provider_settings

    settings = load_provider_settings().environment
    if settings is None:
        pytest.skip("Configure the optional E2B environment section")
    return settings


@pytest.fixture
async def e2b_sandboxes(e2b_settings):
    from .e2b_support import E2BSandboxes

    sandboxes = E2BSandboxes(e2b_settings)
    try:
        yield sandboxes
    finally:
        with anyio.CancelScope(shield=True), anyio.fail_after(180):
            await sandboxes.cleanup()
