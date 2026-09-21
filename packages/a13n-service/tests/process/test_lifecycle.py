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
from a13n_service.run_stream import RedisRunStream, RunDisplayStore
from a13n_service.secrets import SecretProtectionError
from a13n_service.settings import ProcessRole, Settings
from a13n_service.skills import SkillRuntimePreparer
from a13n_service.subagents.maintenance import SubagentMaintenance
from a13n_service.trace_query import TraceQueryCapabilities, TraceQueryProviderRegistry

from ..connectivity.connector_helpers import FakeConnectorBackend, fake_catalog


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
    connector_catalog = fake_catalog(FakeConnectorBackend())
    ingress_registry.register(
        AdapterDefinition(
            key="fake",
            config_versions=frozenset({"fake_v1"}),
            factory=Adapter,
        )
    )
    components = Components(
        ingress_adapter_registry=ingress_registry,
        connector_providers=connector_catalog,
    )

    control = snapshot_components(
        Settings(service={"role": ProcessRole.control}),
        components,
    )
    connectivity = snapshot_components(
        Settings(service={"role": ProcessRole.connectivity}),
        components,
    )
    worker = snapshot_components(
        Settings(service={"role": ProcessRole.worker}),
        components,
    )
    ingress_registry.register(
        AdapterDefinition(
            key="later",
            config_versions=frozenset({"fake_v1"}),
            factory=Adapter,
        )
    )
    with pytest.raises(TypeError):
        connector_catalog["later"] = replace(connector_catalog.require("fake_connector"), type="later")

    assert control.ingress_adapter_registry is not None
    assert control.ingress_adapter_registry.keys() == ("fake",)
    assert control.connector_providers is not None
    assert tuple(control.connector_providers) == ("fake_connector",)
    assert connectivity.ingress_adapter_registry is not None
    assert connectivity.ingress_adapter_registry.keys() == ("fake",)
    assert connectivity.connector_providers is None
    assert worker.ingress_adapter_registry is None
    assert worker.connector_providers is not None
    assert tuple(worker.connector_providers) == ("fake_connector",)


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
        if connectivity_data is not None:
            assert connectivity_data.event_connections is not None
            assert not connectivity_data.event_connections.is_draining()
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
        "a13n_service.process.connectivity.ConnectorProviderService",
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
            delivery = await client.post(
                "/connectivity/v1/accounts/acct_test/events", headers={"X-Request-ID": "req-draining"}
            )
            health = await client.get("/healthz")

        for response in [readiness, delivery]:
            assert response.status_code == 503
            assert response.json() == {
                "error": {
                    "code": "service_unavailable",
                    "message": "The service is temporarily unavailable.",
                    "details": {},
                    "request_id": response.headers["X-Request-ID"],
                }
            }
        assert delivery.headers["X-Request-ID"].startswith("req-")
        assert delivery.headers["X-Request-ID"] != "req-draining"
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
        assert isinstance(worker.state.runtime.worker.run_display, RunDisplayStore)


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
async def test_shutdown_stops_admission_before_waiting_in_composition_order(
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
        assert loop.is_draining()
        assert trace == ["environment draining", "subagents draining"]
        await execution_wait(loop)
        trace.append("execution stopped")

    def drain_environment(loop: EnvironmentMaintenanceLoop) -> None:
        if loop.is_draining():
            return
        assert trace == []
        trace.append("environment draining")
        environment_drain(loop)

    async def wait_environment(loop: EnvironmentMaintenanceLoop) -> None:
        await environment_wait(loop)
        trace.append("environment stopped")

    def drain_subagents(loop: SubagentMaintenance) -> None:
        if loop.is_draining():
            return
        assert trace == ["environment draining"]
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
        "environment draining",
        "subagents draining",
        "execution stopped",
        "environment stopped",
        "subagents stopped",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("role", tuple(ProcessRole))
async def test_environment_roles_share_one_response_reader_without_starting_one_for_connectivity(
    local_settings, tmp_path, role, monkeypatch
):
    import asyncio

    from a13n_service.environments.websocket.relay_runtime import RelayResponseRuntime
    from a13n_service.process import lifecycle

    started = asyncio.Event()
    built = []

    async def build_responses(settings, storage, catalog, stack):
        assert "websocket_envd" in catalog
        responses = RelayResponseRuntime(storage.redis, storage.redis, "test-origin")

        async def run():
            started.set()
            await responses._closed.wait()

        monkeypatch.setattr(responses, "run", run)
        stack.push_async_callback(responses.close)
        built.append(responses)
        return responses

    async def no_control_connection_listener(*args, **kwargs):
        return None

    # Role composition does not depend on a real Redis script or connection listener.
    monkeypatch.setattr(lifecycle, "build_relay_responses", build_responses)
    monkeypatch.setattr(
        "a13n_service.process.control.composition.build_client_connections", no_control_connection_listener
    )
    settings = local_settings(
        tmp_path / role.value,
        role=role,
        environment_provider_builtins=("websocket_envd",),
        environment_client_public_origin="wss://service.example",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        responses = runtime.shared.relay_responses
        if role is ProcessRole.connectivity:
            assert responses is None
            assert runtime.shared.devices.relay is None
            assert not built
            return
        assert built == [responses]
        async with asyncio.timeout(10):
            await started.wait()
        assert runtime.shared.devices.relay._runtime is responses
        if runtime.control is not None:
            assert runtime.control.environments.devices is runtime.shared.devices
        if runtime.worker is not None:
            assert runtime.worker.client_connections._responses is responses.responses
            assert runtime.worker.client_connections.instance_id == responses.instance_id
    assert responses.is_closed()


@pytest.mark.anyio
async def test_environment_response_runtime_starts_and_stops_with_real_redis(local_settings, tmp_path, redis_url):
    import asyncio
    from time import monotonic

    app = create_app(
        local_settings(
            tmp_path,
            redis_backend="redis",
            redis_url=redis_url,
            environment_provider_builtins=("websocket_envd",),
            environment_client_public_origin="wss://service.example",
        )
    )
    started_at = monotonic()
    try:
        async with app.router.lifespan_context(app):
            responses = app.state.runtime.shared.relay_responses
            assert responses is not None
            async with asyncio.timeout(10):
                while not responses.responses._running:
                    await asyncio.sleep(0)
            assert app.state.runtime.worker.client_connections._responses is responses.responses
        assert responses.is_closed()
    finally:
        print(f"Real Redis response runtime startup/lifespan: {monotonic() - started_at:.3f}s")
