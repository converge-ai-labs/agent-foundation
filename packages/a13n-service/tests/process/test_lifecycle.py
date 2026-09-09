from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import Components, create_app
from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop
from a13n_service.hooks.management import HookSubscriptionService
from a13n_service.interactions.worker import WorkerExecutionLoop
from a13n_service.lifecycle.service import LifecycleEventService
from a13n_service.process.background import run_critical_component
from a13n_service.process.components import snapshot_components
from a13n_service.run_stream import RedisRunStream, RunReplayStore
from a13n_service.secrets import SecretProtectionError
from a13n_service.settings import ProcessRole, Settings
from a13n_service.skills import SkillRuntimePreparer
from a13n_service.subagents.maintenance import SubagentMaintenance
from a13n_service.trace_query import TraceQueryCapabilities, TraceQueryProviderRegistry

from ..connectivity.connector_helpers import FakeConnectorBackend, fake_registry


@pytest.mark.anyio
async def test_critical_component_normal_return_is_a_process_failure() -> None:
    async def returns() -> None:
        return None

    with pytest.raises(RuntimeError, match="returned unexpectedly: test component"):
        await run_critical_component("test component", returns)


@pytest.mark.anyio
async def test_critical_component_can_return_after_its_expected_drain() -> None:
    async def returns() -> None:
        return None

    await run_critical_component("drained component", returns, lambda: True)


def test_connectivity_registries_are_copied_only_for_owning_roles() -> None:
    class Adapter:
        provider_key = "fake"
        config_versions = frozenset({"fake_v1"})

    ingress_registry = AdapterRegistry[IngressAdapter]()
    connector_registry = fake_registry(FakeConnectorBackend())
    ingress_registry.register(
        AdapterDefinition(
            key="fake",
            config_versions=frozenset({"fake_v1"}),
            factory=Adapter,
        )
    )
    components = Components(
        ingress_adapter_registry=ingress_registry,
        connector_provider_registry=connector_registry,
    )

    control = snapshot_components(
        Settings(_env_file=None, role=ProcessRole.control),
        components,
    )
    connectivity = snapshot_components(
        Settings(_env_file=None, role=ProcessRole.connectivity),
        components,
    )
    worker = snapshot_components(
        Settings(_env_file=None, role=ProcessRole.worker),
        components,
    )
    ingress_registry.register(
        AdapterDefinition(
            key="later",
            config_versions=frozenset({"fake_v1"}),
            factory=Adapter,
        )
    )
    connector_registry.register(replace(connector_registry.require("fake_connector"), type="later"))

    assert control.ingress_adapter_registry is not None
    assert control.ingress_adapter_registry.keys() == ("fake",)
    assert control.connector_provider_registry is not None
    assert tuple(item.type for item in control.connector_provider_registry.definitions()) == ("fake_connector",)
    assert connectivity.ingress_adapter_registry is not None
    assert connectivity.ingress_adapter_registry.keys() == ("fake",)
    assert connectivity.connector_provider_registry is None
    assert worker.ingress_adapter_registry is None
    assert worker.connector_provider_registry is not None
    assert tuple(item.type for item in worker.connector_provider_registry.definitions()) == ("fake_connector",)


@pytest.mark.anyio
async def test_lifespan_constructs_storage_once_and_readiness_uses_it(local_settings, tmp_path: Path) -> None:
    app = create_app(local_settings(tmp_path))

    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        assert runtime.control is not None
        assert runtime.worker is not None
        from a13n_service.connectivity.execution import ExternalToolRuntime

        assert isinstance(runtime.worker.external_tools, ExternalToolRuntime)
        assert isinstance(runtime.worker.skill_runtime, SkillRuntimePreparer)
        assert isinstance(runtime.worker.environment_maintenance, EnvironmentMaintenanceLoop)
        assert runtime.control.trace_queries is not None

        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/readyz")

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "role": "all"}


@pytest.mark.anyio
@pytest.mark.parametrize("role", tuple(ProcessRole))
async def test_role_lifespan_installs_only_owned_connectivity_components(
    local_settings,
    tmp_path: Path,
    role: ProcessRole,
) -> None:
    app = create_app(local_settings(tmp_path / role.value, role=role))

    async with app.router.lifespan_context(app):
        serves_control = role in {ProcessRole.all, ProcessRole.control}
        serves_connectivity = role in {ProcessRole.all, ProcessRole.connectivity}
        serves_worker = role in {ProcessRole.all, ProcessRole.worker}
        runtime = app.state.runtime
        connectivity = runtime.connectivity
        assert (connectivity is not None) is (serves_control or serves_connectivity)
        connectivity_control = connectivity.control if connectivity is not None else None
        connectivity_data = connectivity.data if connectivity is not None else None
        assert (connectivity_control is not None) is serves_control
        assert (connectivity_data is not None) is serves_connectivity
        assert (runtime.worker is not None) is serves_worker


@pytest.mark.anyio
async def test_connectivity_role_does_not_build_control_adapters(
    local_settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("data-plane role built control-plane adapters")

    monkeypatch.setattr(
        "a13n_service.process.connectivity.built_in_connector_provider_registry",
        fail_if_called,
    )
    app = create_app(local_settings(tmp_path, role=ProcessRole.connectivity))

    async with app.router.lifespan_context(app):
        assert app.state.runtime.connectivity.control is None


@pytest.mark.anyio
async def test_drain_fails_readiness_before_rejecting_new_connectivity_work(local_settings, tmp_path: Path) -> None:
    app = create_app(local_settings(tmp_path, role=ProcessRole.connectivity))

    async with app.router.lifespan_context(app):
        app.state.runtime.status.draining = True
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            readiness = await client.get("/readyz")
            delivery = await client.post("/connectivity/v1/accounts/acct_test/events")
            health = await client.get("/healthz")

        assert readiness.status_code == 503
        assert readiness.json() == {"detail": "service not ready"}
        assert delivery.status_code == 503
        assert delivery.json() == {"detail": "service draining"}
        assert health.status_code == 200

    assert app.state.runtime.status.startup_complete is False
    assert app.state.runtime.status.draining is True


@pytest.mark.anyio
async def test_lifespan_wires_skill_components_only_to_their_process_roles(local_settings, tmp_path: Path) -> None:
    control = create_app(local_settings(tmp_path / "control", role=ProcessRole.control))
    async with control.router.lifespan_context(control):
        assert control.state.runtime.control is not None
        assert control.state.runtime.worker is None
        assert isinstance(control.state.runtime.control.hook_subscriptions, HookSubscriptionService)
        assert isinstance(control.state.runtime.control.lifecycle_events, LifecycleEventService)

    worker = create_app(local_settings(tmp_path / "worker", role=ProcessRole.worker))
    async with worker.router.lifespan_context(worker):
        assert worker.state.runtime.control is None
        assert isinstance(worker.state.runtime.worker.skill_runtime, SkillRuntimePreparer)
        assert isinstance(worker.state.runtime.worker.run_stream, RedisRunStream)
        assert isinstance(worker.state.runtime.worker.run_replay, RunReplayStore)


@pytest.mark.anyio
async def test_trace_query_client_is_created_only_for_control_plane_roles(local_settings, tmp_path: Path) -> None:
    query_values = {
        "observability_query_provider": "langfuse",
        "observability_query_langfuse_base_url": "https://langfuse.example.com",
        "observability_query_langfuse_public_key": "pk-test",
        "observability_query_langfuse_secret_key": "sk-test",
    }
    control = create_app(local_settings(tmp_path / "control", role=ProcessRole.control, **query_values))
    worker = create_app(local_settings(tmp_path / "worker", role=ProcessRole.worker, **query_values))

    async with control.router.lifespan_context(control):
        assert control.state.runtime.control.trace_queries is not None
    async with worker.router.lifespan_context(worker):
        assert worker.state.runtime.control is None


@pytest.mark.anyio
async def test_distribution_registered_trace_query_provider_is_selected_only_by_control(
    local_settings, tmp_path: Path
) -> None:
    class Provider:
        capabilities = TraceQueryCapabilities()

    created: list[Provider] = []
    registry = TraceQueryProviderRegistry()

    def create_provider() -> Provider:
        provider = Provider()
        created.append(provider)
        return provider

    registry.register("custom", create_provider)  # type: ignore[arg-type]
    components = Components(trace_query_provider_registry=registry)
    control = create_app(
        local_settings(
            tmp_path / "control-custom",
            role=ProcessRole.control,
            observability_query_provider="custom",
        ),
        components=components,
    )
    worker = create_app(
        local_settings(
            tmp_path / "worker-custom",
            role=ProcessRole.worker,
            observability_query_provider="custom",
        ),
        components=components,
    )

    async with control.router.lifespan_context(control):
        assert len(created) == 1
        assert control.state.runtime.control.trace_queries is not None
    async with worker.router.lifespan_context(worker):
        assert len(created) == 1
        assert worker.state.runtime.control is None


def test_distribution_cannot_replace_the_builtin_langfuse_provider(local_settings, tmp_path: Path) -> None:
    registry = TraceQueryProviderRegistry()
    registry.register("langfuse", lambda: object())  # type: ignore[arg-type,return-value]

    with pytest.raises(ValueError, match="already registered"):
        create_app(
            local_settings(tmp_path),
            components=Components(trace_query_provider_registry=registry),
        )


@pytest.mark.anyio
async def test_lifespan_fails_closed_without_secret_master_key(local_settings, tmp_path: Path) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            secret_master_key_base64=None,
            secret_encryption_key_id=None,
        )
    )

    with pytest.raises(SecretProtectionError, match="A13N_SERVICE_SECRET_MASTER_KEY_BASE64"):
        async with app.router.lifespan_context(app):
            pytest.fail("lifespan unexpectedly started")


@pytest.mark.anyio
async def test_control_lifespan_allows_noninteractive_connectivity_without_public_origin(
    local_settings, tmp_path: Path
) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            role=ProcessRole.control,
            connectivity_public_origin=None,
        )
    )

    async with app.router.lifespan_context(app):
        assert app.state is not None


@pytest.mark.anyio
@pytest.mark.parametrize("role", list(ProcessRole))
@pytest.mark.parametrize("enabled", [False, True])
async def test_price_updater_lifetime_belongs_only_to_enabled_execution_roles(
    local_settings, tmp_path, monkeypatch, role, enabled
):
    from contextlib import contextmanager

    from pydantic_ai import prices

    events = []

    @contextmanager
    def updater():
        events.append("start")
        try:
            yield
        finally:
            events.append("stop")

    monkeypatch.setattr(prices, "update_in_background", updater)
    app = create_app(local_settings(tmp_path, role=role, pricing_auto_update=enabled))
    expected = enabled and role in {ProcessRole.all, ProcessRole.worker}
    async with app.router.lifespan_context(app):
        assert events == (["start"] if expected else [])
    assert events == (["start", "stop"] if expected else [])


@pytest.mark.anyio
async def test_shutdown_preserves_execution_environment_and_subagent_order(
    local_settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    execution_wait = WorkerExecutionLoop.wait_stopped
    environment_drain = EnvironmentMaintenanceLoop.drain
    environment_wait = EnvironmentMaintenanceLoop.wait_stopped
    subagent_drain = SubagentMaintenance.drain
    subagent_wait = SubagentMaintenance.wait_stopped

    async def wait_execution(loop: WorkerExecutionLoop) -> None:
        assert app.state.runtime.status.draining
        await execution_wait(loop)
        trace.append("execution stopped")

    def drain_environment(loop: EnvironmentMaintenanceLoop) -> None:
        assert trace == ["execution stopped"]
        trace.append("environment draining")
        environment_drain(loop)

    async def wait_environment(loop: EnvironmentMaintenanceLoop) -> None:
        await environment_wait(loop)
        trace.append("environment stopped")

    def drain_subagents(loop: SubagentMaintenance) -> None:
        assert trace[-1] == "environment stopped"
        trace.append("subagents draining")
        subagent_drain(loop)

    async def wait_subagents(loop: SubagentMaintenance) -> None:
        await subagent_wait(loop)
        trace.append("subagents stopped")

    monkeypatch.setattr(WorkerExecutionLoop, "wait_stopped", wait_execution)
    monkeypatch.setattr(EnvironmentMaintenanceLoop, "drain", drain_environment)
    monkeypatch.setattr(EnvironmentMaintenanceLoop, "wait_stopped", wait_environment)
    monkeypatch.setattr(SubagentMaintenance, "drain", drain_subagents)
    monkeypatch.setattr(SubagentMaintenance, "wait_stopped", wait_subagents)
    app = create_app(local_settings(tmp_path))
    async with app.router.lifespan_context(app):
        pass
    assert trace == [
        "execution stopped",
        "environment draining",
        "environment stopped",
        "subagents draining",
        "subagents stopped",
    ]
