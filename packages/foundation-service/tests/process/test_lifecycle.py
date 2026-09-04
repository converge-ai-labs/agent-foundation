from pathlib import Path

import httpx2
import pytest
from a13n_service.app import ServiceComponents, create_app
from a13n_service.connectivity.adapters import ConnectorAdapter, IngressAdapter
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.plugins.materialization import PluginRuntimeMaterializer
from a13n_service.plugins.on_demand import OnDemandPluginRuntime
from a13n_service.process.background import run_critical_component
from a13n_service.process.components import snapshot_service_components
from a13n_service.secrets import SecretProtectionError
from a13n_service.settings import ServiceRole, ServiceSettings
from a13n_service.skills import SkillRuntimePreparer
from a13n_service.trace_query import TraceQueryCapabilities, TraceQueryProviderRegistry

from .support import local_settings


@pytest.mark.anyio
async def test_critical_component_normal_return_is_a_process_failure() -> None:
    async def returns() -> None:
        return None

    with pytest.raises(RuntimeError, match="returned unexpectedly: test component"):
        await run_critical_component("test component", returns)


def test_connectivity_registries_are_copied_only_for_owning_roles() -> None:
    class Adapter:
        provider_key = "fake"
        driver_key = "fake"
        config_versions = frozenset({"fake_v1"})

    ingress_registry = AdapterRegistry[IngressAdapter]()
    connector_registry = AdapterRegistry[ConnectorAdapter]()
    ingress_registry.register(
        AdapterDefinition(
            key="fake",
            config_versions=frozenset({"fake_v1"}),
            factory=Adapter,
        )
    )
    connector_registry.register(
        AdapterDefinition(
            key="fake",
            config_versions=frozenset({"fake_v1"}),
            factory=Adapter,
        )
    )
    components = ServiceComponents(
        ingress_adapter_registry=ingress_registry,
        connector_adapter_registry=connector_registry,
    )

    control = snapshot_service_components(
        ServiceSettings(_env_file=None, role=ServiceRole.control),
        components,
    )
    connectivity = snapshot_service_components(
        ServiceSettings(_env_file=None, role=ServiceRole.connectivity),
        components,
    )
    worker = snapshot_service_components(
        ServiceSettings(_env_file=None, role=ServiceRole.worker),
        components,
    )
    ingress_registry.register(
        AdapterDefinition(
            key="later",
            config_versions=frozenset({"fake_v1"}),
            factory=Adapter,
        )
    )
    connector_registry.register(
        AdapterDefinition(
            key="later",
            config_versions=frozenset({"fake_v1"}),
            factory=Adapter,
        )
    )

    assert control.ingress_adapter_registry is not None
    assert control.ingress_adapter_registry.keys() == ("fake",)
    assert control.connector_adapter_registry is not None
    assert control.connector_adapter_registry.keys() == ("fake",)
    assert connectivity.ingress_adapter_registry is not None
    assert connectivity.ingress_adapter_registry.keys() == ("fake",)
    assert connectivity.connector_adapter_registry is None
    assert worker is components


@pytest.mark.anyio
async def test_lifespan_constructs_storage_once_and_readiness_uses_it(tmp_path: Path) -> None:
    app = create_app(local_settings(tmp_path))

    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        assert runtime.control is not None
        assert runtime.worker is not None
        assert isinstance(runtime.worker.skill_runtime, SkillRuntimePreparer)
        assert runtime.control.plugins is not None
        assert isinstance(runtime.worker.plugin_materializer, PluginRuntimeMaterializer)
        assert isinstance(runtime.worker.plugin_runtime, OnDemandPluginRuntime)
        assert runtime.control.trace_queries is not None

        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/readyz")

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "role": "all"}


@pytest.mark.anyio
@pytest.mark.parametrize("role", tuple(ServiceRole))
async def test_role_lifespan_installs_only_owned_connectivity_components(
    tmp_path: Path,
    role: ServiceRole,
) -> None:
    app = create_app(local_settings(tmp_path / role.value, role=role))

    async with app.router.lifespan_context(app):
        serves_control = role in {ServiceRole.all, ServiceRole.control}
        serves_connectivity = role in {ServiceRole.all, ServiceRole.connectivity}
        serves_worker = role in {ServiceRole.all, ServiceRole.worker}
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("data-plane role built control-plane adapters")

    monkeypatch.setattr(
        "a13n_service.process.connectivity.built_in_connector_adapter_registry",
        fail_if_called,
    )
    app = create_app(local_settings(tmp_path, role=ServiceRole.connectivity))

    async with app.router.lifespan_context(app):
        assert app.state.runtime.connectivity.control is None


@pytest.mark.anyio
async def test_drain_fails_readiness_before_rejecting_new_connectivity_work(tmp_path: Path) -> None:
    app = create_app(local_settings(tmp_path, role=ServiceRole.connectivity))

    async with app.router.lifespan_context(app):
        app.state.runtime.status.draining = True
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            readiness = await client.get("/readyz")
            delivery = await client.post("/connectivity/v1/ingresses/ing_test/events")
            health = await client.get("/healthz")

        assert readiness.status_code == 503
        assert readiness.json() == {"detail": "service not ready"}
        assert delivery.status_code == 503
        assert delivery.json() == {"detail": "service draining"}
        assert health.status_code == 200

    assert app.state.runtime.status.startup_complete is False
    assert app.state.runtime.status.draining is True


@pytest.mark.anyio
async def test_lifespan_wires_skill_components_only_to_their_process_roles(tmp_path: Path) -> None:
    control = create_app(local_settings(tmp_path / "control", role=ServiceRole.control))
    async with control.router.lifespan_context(control):
        assert control.state.runtime.control is not None
        assert control.state.runtime.worker is None

    worker = create_app(local_settings(tmp_path / "worker", role=ServiceRole.worker))
    async with worker.router.lifespan_context(worker):
        assert worker.state.runtime.control is None
        assert isinstance(worker.state.runtime.worker.skill_runtime, SkillRuntimePreparer)


@pytest.mark.anyio
async def test_trace_query_client_is_created_only_for_control_plane_roles(tmp_path: Path) -> None:
    query_values = {
        "observability_query_provider": "langfuse",
        "observability_query_langfuse_base_url": "https://langfuse.example.com",
        "observability_query_langfuse_public_key": "pk-test",
        "observability_query_langfuse_secret_key": "sk-test",
    }
    control = create_app(local_settings(tmp_path / "control", role=ServiceRole.control, **query_values))
    worker = create_app(local_settings(tmp_path / "worker", role=ServiceRole.worker, **query_values))

    async with control.router.lifespan_context(control):
        assert control.state.runtime.control.trace_queries is not None
    async with worker.router.lifespan_context(worker):
        assert worker.state.runtime.control is None


@pytest.mark.anyio
async def test_distribution_registered_trace_query_provider_is_selected_only_by_control(tmp_path: Path) -> None:
    class Provider:
        capabilities = TraceQueryCapabilities()

    created: list[Provider] = []
    registry = TraceQueryProviderRegistry()

    def create_provider() -> Provider:
        provider = Provider()
        created.append(provider)
        return provider

    registry.register("custom", create_provider)  # type: ignore[arg-type]
    components = ServiceComponents(trace_query_provider_registry=registry)
    control = create_app(
        local_settings(
            tmp_path / "control-custom",
            role=ServiceRole.control,
            observability_query_provider="custom",
        ),
        components=components,
    )
    worker = create_app(
        local_settings(
            tmp_path / "worker-custom",
            role=ServiceRole.worker,
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


def test_distribution_cannot_replace_the_builtin_langfuse_provider(tmp_path: Path) -> None:
    registry = TraceQueryProviderRegistry()
    registry.register("langfuse", lambda: object())  # type: ignore[arg-type,return-value]

    with pytest.raises(ValueError, match="already registered"):
        create_app(
            local_settings(tmp_path),
            components=ServiceComponents(trace_query_provider_registry=registry),
        )


@pytest.mark.anyio
async def test_lifespan_fails_closed_without_secret_master_key(tmp_path: Path) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            secret_master_key_base64=None,
            secret_encryption_key_id=None,
        )
    )

    with pytest.raises(SecretProtectionError, match="FOUNDATION_SECRET_MASTER_KEY_BASE64"):
        async with app.router.lifespan_context(app):
            pytest.fail("lifespan unexpectedly started")


@pytest.mark.anyio
async def test_control_lifespan_requires_connectivity_public_origin(tmp_path: Path) -> None:
    app = create_app(
        local_settings(
            tmp_path,
            role=ServiceRole.control,
            connectivity_public_origin=None,
        )
    )

    with pytest.raises(ValueError, match="FOUNDATION_CONNECTIVITY_PUBLIC_ORIGIN"):
        async with app.router.lifespan_context(app):
            pytest.fail("lifespan unexpectedly started")
