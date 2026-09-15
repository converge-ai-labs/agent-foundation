from __future__ import annotations

import os
from pathlib import Path

import anyio
import httpx2
import pytest

from .infrastructure.client import LiveClient
from .infrastructure.config import load_config
from .infrastructure.dependencies import CONFIG_ENV, MODE_ENV


def pytest_addoption(parser):
    parser.addoption("--live-shared-labs", action="store_true", help="Reuse only the reviewed smoke-test labs")
    parser.addoption("--infrastructure", choices=("docker", "external"), default=None)
    parser.addoption("--infrastructure-config", type=Path)
    parser.addoption("--live", action="store_true", help="Run real local Foundation HTTP journeys")
    parser.addoption("--live-round-two", action="store_true", help="Run isolated process and dependency fault journeys")
    parser.addoption("--live-management", action="store_true", help="Run isolated Service/Harness management journeys")
    parser.addoption("--live-plugin-image", action="store_true", help="Build and run a custom plugin Worker image")
    parser.addoption("--live-providers", action="store_true", help="Run configured real-provider integration journeys")
    parser.addoption("--live-environments", action="store_true", help="Run the five-backend Environment matrix")
    parser.addoption("--live-performance", action="store_true", help="Measure bounded PG, S3 and Service operations")
    parser.addoption("--performance-profile", help="TOML concurrency matrix and per-operation latency budgets")
    parser.addoption("--live-long-session", action="store_true", help="Verify real sequential history and compaction")
    parser.addoption(
        "--session-message-bytes", type=int, default=1024, help="ASCII padding bytes per real input/output"
    )
    parser.addoption("--session-runs", default="1,1000,10000", help="Checkpoints along one real sequential Run chain")


def pytest_configure(config):
    # The suite launcher passes explicit settings through the subprocess environment.
    # Direct pytest invocations may select the same mode through command-line flags.
    config._live_infrastructure_environment = {key: os.environ.get(key) for key in (MODE_ENV, CONFIG_ENV)}
    if mode := config.getoption("--infrastructure"):
        os.environ[MODE_ENV] = mode
        os.environ.pop(CONFIG_ENV, None)
    if path := config.getoption("--infrastructure-config"):
        if os.environ.get(MODE_ENV, "docker") != "external":
            raise pytest.UsageError("--infrastructure-config requires --infrastructure=external")
        os.environ[CONFIG_ENV] = str(path.resolve())


def pytest_unconfigure(config):
    for key, value in getattr(config, "_live_infrastructure_environment", {}).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live-shared-labs"):
        return
    from .ci import SMOKE_GROUPS, TEST_ROOT

    distributed = bool(getattr(config.option, "numprocesses", 0) or hasattr(config, "workerinput"))
    allowed = {selection for group in SMOKE_GROUPS.values() for selection in group}
    for item in items:
        if not item.path.is_relative_to(TEST_ROOT):
            raise pytest.UsageError(f"Shared labs do not support this unreviewed journey: {item.nodeid}")
        selection = str(item.path.relative_to(TEST_ROOT)) + "::" + item.originalname
        if selection not in allowed:
            raise pytest.UsageError(f"Shared labs do not support this unreviewed journey: {item.nodeid}")
        if distributed and selection not in SMOKE_GROUPS["core"]:
            raise pytest.UsageError(f"Parallel shared labs support only reviewed Core smoke journeys: {item.nodeid}")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if report.when == "call":
        item.live_call_report = report


def backend_scope(*, fixture_name, config):
    return "session" if config.getoption("--live-shared-labs") else "function"


@pytest.fixture(scope="session")
async def shared_labs(anyio_backend):
    from .infrastructure.shared_labs import SharedLabs

    pool = SharedLabs()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def selected_shared_labs(request):
    return request.getfixturevalue("shared_labs") if request.config.getoption("--live-shared-labs") else None


@pytest.fixture
async def long_session(request):
    if not request.config.getoption("--live-long-session"):
        pytest.skip("Opt in with make live-test-session; no compaction infrastructure starts by default")
    message_bytes = request.config.getoption("--session-message-bytes")
    try:
        runs = sorted(set(int(value) for value in request.config.getoption("--session-runs").split(",")))
        assert runs and all(1 <= value <= 100000 for value in runs)
    except (ValueError, AssertionError) as error:
        raise pytest.UsageError("--session-runs requires integers between 1 and 100000") from error
    if not 1 <= message_bytes <= 2048:
        raise pytest.UsageError("Require message bytes 1..2048")
    from .infrastructure.round_two_lab import open_lab

    async with open_lab(long_session={"message_bytes": message_bytes, "context_window": 32768}) as lab:
        lab.client.http.timeout = httpx2.Timeout(120)
        yield lab, runs


@pytest.fixture(scope=backend_scope)
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def live(request, selected_shared_labs):
    if not request.config.getoption("--live"):
        pytest.skip("Opt in with make live-test or --live; no live network calls are made by default")
    if selected_shared_labs is not None:
        async with selected_shared_labs.case("core", request) as lab:
            await lab.client.preflight()
            yield lab.client
        return
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
async def round_two(request, selected_shared_labs):
    if not request.config.getoption("--live-round-two"):
        pytest.skip("Opt in with make live-test-round-two; no fault injection runs by default")
    if selected_shared_labs is not None:
        async with selected_shared_labs.case("round-two", request) as lab:
            yield lab
        return
    from .infrastructure.round_two_lab import open_lab

    async with open_lab() as lab:
        yield lab


@pytest.fixture
async def management(request, selected_shared_labs):
    if not request.config.getoption("--live-management"):
        pytest.skip("Opt in with make live-test-management; no management resources are changed by default")
    from .infrastructure.management_support import ManagementJourney
    from .infrastructure.round_two_lab import open_lab

    if selected_shared_labs is not None:
        async with selected_shared_labs.case("management", request) as lab:
            yield ManagementJourney(lab)
        return
    async with open_lab(suite="management") as lab:
        yield ManagementJourney(lab)


@pytest.fixture
async def run_faults(request):
    if not request.config.getoption("--live-round-two"):
        pytest.skip("Opt in with --live-round-two for process and persistence fault tests")
    from .infrastructure.round_two_lab import open_lab
    from .run_recovery.run_fault_support import RunFaultJourney

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
    from .control.control_support import ControlJourney
    from .infrastructure.round_two_lab import open_lab

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
    if not any(
        request.config.getoption(option)
        for option in ("--live", "--live-round-two", "--live-management", "--live-providers")
    ):
        pytest.skip("Opt in with a live-test target; private Provider configuration is not read by offline checks")
    from .providers.provider_config import load_provider_settings

    section = selection
    configuration = (
        load_provider_settings(upstream_model=upstream_model)
        if upstream_model is not None
        else load_provider_settings()
    )
    settings = getattr(configuration, section)
    if settings is None:
        pytest.skip(f"Optional {section} Provider is not configured; existing defaults are unchanged")
    if upstream_model is not None:
        if settings.provider != "openrouter":
            pytest.skip("The GPT/Gemini/Claude matrix requires model.provider=openrouter")
    from .providers.real_providers import configured_provider_lab

    async with configured_provider_lab("search" if section == "brave_search" else section, settings) as configured:
        yield configured


@pytest.fixture(scope="module")
def e2b_settings(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in with --live-environments for real E2B lifecycle tests")
    from .providers.provider_config import load_provider_settings

    settings = load_provider_settings().environment
    if settings is None:
        pytest.skip("Configure the optional E2B environment section")
    return settings


@pytest.fixture
async def e2b_sandboxes(e2b_settings):
    from .environment.e2b_support import E2BSandboxes

    sandboxes = E2BSandboxes(e2b_settings)
    try:
        yield sandboxes
    finally:
        with anyio.CancelScope(shield=True), anyio.fail_after(180):
            await sandboxes.cleanup()
