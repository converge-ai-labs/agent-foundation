import asyncio
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import Components, create_app
from a13n_service.plugins.commands import PluginRuntimeCatalogSnapshot, PluginRuntimeCommand
from a13n_service.plugins.materialization import PluginRuntimeMaterializer
from a13n_service.plugins.on_demand import OnDemandPluginRuntime
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.plugins.runtime import PluginRuntimeLock
from a13n_service.plugins.runtime_commands import PluginRuntimeCommandCoordinator
from a13n_service.settings import ProcessRole
from anyio import sleep_forever


class _UnusedPluginRuntimeCandidateResolver:
    async def resolve_candidate(
        self,
        *,
        operation_id: str,
        command: PluginRuntimeCommand,
        catalog: PluginRuntimeCatalogSnapshot,
    ) -> PluginRuntimeLock:
        del operation_id, command, catalog
        raise AssertionError("no Plugin Runtime command was expected")

    async def require_candidate(self, *, runtime_lock_digest: str) -> PluginRuntimeLock:
        del runtime_lock_digest
        raise AssertionError("no Plugin Runtime command was expected")


class _UnusedPluginRuntimeStagingAuthority:
    async def stage_candidate(self, *, operation_id: str, runtime_lock: PluginRuntimeLock) -> str:
        del operation_id, runtime_lock
        raise AssertionError("no Plugin Runtime command was expected")

    async def activate_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
        staging_token: str,
        runtime_generation: int,
    ) -> None:
        del operation_id, runtime_lock, staging_token, runtime_generation
        raise AssertionError("no Plugin Runtime command was expected")

    async def abort_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
        staging_token: str | None,
    ) -> None:
        del operation_id, runtime_lock, staging_token
        raise AssertionError("no Plugin Runtime command was expected")


@pytest.fixture
def coordinator_started(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    started = asyncio.Event()

    async def record_start(_coordinator: PluginRuntimeCommandCoordinator) -> None:
        started.set()
        await sleep_forever()

    monkeypatch.setattr(PluginRuntimeCommandCoordinator, "run", record_start)
    return started


@pytest.mark.anyio
async def test_on_demand_import_failure_removes_worker_readiness(
    local_settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(local_settings(tmp_path, role=ProcessRole.worker))

    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        assert runtime.worker is not None
        assert isinstance(runtime.worker.plugin_runtime, OnDemandPluginRuntime)
        monkeypatch.setattr(OnDemandPluginRuntime, "ready", property(lambda _: False))
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"detail": "plugin runtime unavailable"}


@pytest.mark.anyio
async def test_lifespan_rejects_partial_plugin_runtime_coordination(local_settings, tmp_path: Path) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            role=ProcessRole.control,
            plugin_runtime_mode="runner",
        ),
        components=Components(
            plugin_runtime_candidate_resolver=_UnusedPluginRuntimeCandidateResolver(),
        ),
    )

    with pytest.raises(RuntimeError, match="requires both a candidate resolver and staging authority"):
        async with app.router.lifespan_context(app):
            pytest.fail("lifespan unexpectedly started")


@pytest.mark.anyio
async def test_lifespan_rejects_runner_coordination_in_on_demand_mode(local_settings, tmp_path: Path) -> None:
    app = create_app(
        local_settings(tmp_path, role=ProcessRole.control),
        components=Components(
            plugin_runtime_candidate_resolver=_UnusedPluginRuntimeCandidateResolver(),
            plugin_runtime_staging_authority=_UnusedPluginRuntimeStagingAuthority(),
        ),
    )

    with pytest.raises(RuntimeError, match="configured outside runner mode"):
        async with app.router.lifespan_context(app):
            pytest.fail("lifespan unexpectedly started")


@pytest.mark.anyio
async def test_lifespan_wires_durable_plugin_runtime_coordinator(
    local_settings,
    tmp_path: Path,
    coordinator_started: asyncio.Event,
) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            role=ProcessRole.control,
            plugin_runtime_mode="runner",
            plugin_runtime_command_poll_interval_seconds=0.01,
            plugin_runtime_command_lease_seconds=4,
        ),
        components=Components(
            plugin_runtime_candidate_resolver=_UnusedPluginRuntimeCandidateResolver(),
            plugin_runtime_staging_authority=_UnusedPluginRuntimeStagingAuthority(),
        ),
    )

    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        assert runtime.control is not None
        await coordinator_started.wait()


@pytest.mark.anyio
async def test_lifespan_builds_default_plugin_runtime_candidate_resolver(
    local_settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_resolvers: list[_UnusedPluginRuntimeCandidateResolver] = []

    def build_candidate_resolver(*args: object, **kwargs: object) -> _UnusedPluginRuntimeCandidateResolver:
        del args, kwargs
        resolver = _UnusedPluginRuntimeCandidateResolver()
        candidate_resolvers.append(resolver)
        return resolver

    monkeypatch.setattr(
        "a13n_service.process.control.plugin.DurableRuntimeCandidateResolver",
        build_candidate_resolver,
    )
    app = create_app(
        local_settings(
            tmp_path,
            role=ProcessRole.control,
            plugin_runtime_mode="runner",
            plugin_runtime_command_poll_interval_seconds=0.01,
            plugin_runtime_command_lease_seconds=4,
            plugin_runtime_default_index_url="https://user:index-secret@packages.example/simple",
        ),
        components=Components(
            plugin_runtime_staging_authority=_UnusedPluginRuntimeStagingAuthority(),
        ),
    )

    async with app.router.lifespan_context(app):
        control = app.state.runtime.control
        assert control is not None
        assert len(candidate_resolvers) == 1


@pytest.mark.anyio
async def test_all_in_one_runner_mode_uses_local_supervisor_as_staging_authority(
    runner_settings,
    coordinator_started: asyncio.Event,
) -> None:
    app = create_app(runner_settings)

    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        assert runtime.control is not None
        assert runtime.worker is not None
        assert isinstance(runtime.worker.plugin_runtime, PluginRunnerSupervisor)
        await coordinator_started.wait()


@pytest.mark.anyio
async def test_worker_runner_mode_owns_supervisor_without_control_coordinator(runner_settings) -> None:
    app = create_app(runner_settings.model_copy(update={"role": ProcessRole.worker}))

    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        assert runtime.control is None
        assert runtime.worker is not None
        assert isinstance(runtime.worker.plugin_materializer, PluginRuntimeMaterializer)
        assert isinstance(runtime.worker.plugin_runtime, PluginRunnerSupervisor)


@pytest.mark.anyio
async def test_runner_rejects_single_process_storage(local_settings, tmp_path: Path) -> None:
    app = create_app(local_settings(tmp_path, role=ProcessRole.worker, plugin_runtime_mode="runner"))
    with pytest.raises(RuntimeError, match="requires PostgreSQL, Redis, and shared S3"):
        async with app.router.lifespan_context(app):
            pytest.fail("Runner started with uncoordinated storage")
