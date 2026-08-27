from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironmentProviderFactory,
    DirectLocalProviderRuntime,
    EnvironmentAttachmentConcurrency,
    EnvironmentLifecycleCapabilities,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProvider,
    EnvironmentProviderError,
    EnvironmentProviderFactory,
    EnvironmentProviderResourceState,
    EnvironmentProviderRuntime,
    EnvironmentProviderSpec,
    EnvironmentReconciliationResult,
    EnvironmentResource,
    EnvironmentResourceAllocation,
    build_environment_provider_factory_catalog,
    discover_environment_provider_factory_references,
)
from pydantic import BaseModel, ConfigDict


class _Configuration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str


class _Runtime(EnvironmentProviderRuntime):
    pass


_CAPABILITIES = EnvironmentLifecycleCapabilities(
    pause_modes=frozenset(),
    resource_allocation=EnvironmentResourceAllocation.SINGLE_FROM_SPEC,
    attachment_concurrency=EnvironmentAttachmentConcurrency.SHARED,
)
_MISMATCHED_CAPABILITIES = EnvironmentLifecycleCapabilities(
    pause_modes=frozenset(),
    resource_allocation=EnvironmentResourceAllocation.SINGLE_FROM_SPEC,
    attachment_concurrency=EnvironmentAttachmentConcurrency.SINGLE,
)


class _Managed(EnvironmentResource):
    @property
    def state(self) -> EnvironmentProviderResourceState:
        raise NotImplementedError

    @asynccontextmanager
    async def acquire_attachment(self) -> AsyncGenerator[Any]:
        raise NotImplementedError
        yield


class _Provider(EnvironmentProvider):
    @property
    def lifecycle_capabilities(self) -> EnvironmentLifecycleCapabilities:
        return _CAPABILITIES

    async def create(self, *, operation: EnvironmentOperationContext):
        raise NotImplementedError

    async def resume(self, state, *, operation: EnvironmentOperationContext):
        raise NotImplementedError

    async def pause(
        self,
        environment,
        *,
        operation: EnvironmentOperationContext,
        mode: EnvironmentPauseMode = EnvironmentPauseMode.FULL,
    ):
        raise NotImplementedError

    async def destroy(self, state, *, operation: EnvironmentOperationContext) -> None:
        raise NotImplementedError

    async def reconcile(
        self,
        operation: EnvironmentOperationContext,
        *,
        last_known_state: EnvironmentProviderResourceState | None,
    ) -> EnvironmentReconciliationResult:
        raise NotImplementedError


class _Factory(EnvironmentProviderFactory):
    configurations: ClassVar[list[_Configuration]] = []

    @classmethod
    def provider_key(cls) -> str:
        return "test.sandbox"

    @classmethod
    def supported_schema_versions(cls) -> frozenset[str]:
        return frozenset({"1"})

    @classmethod
    def configuration_model(cls, schema_version: str) -> type[BaseModel]:
        assert schema_version == "1"
        return _Configuration

    def lifecycle_capabilities(
        self,
        configuration: BaseModel,
    ) -> EnvironmentLifecycleCapabilities:
        assert isinstance(configuration, _Configuration)
        return _CAPABILITIES

    def create_provider(
        self,
        configuration: BaseModel,
        *,
        runtime: EnvironmentProviderRuntime,
    ) -> EnvironmentProvider:
        assert isinstance(configuration, _Configuration)
        assert isinstance(runtime, _Runtime)
        self.configurations.append(configuration)
        return _Provider()


class _MismatchedProvider(_Provider):
    @property
    def lifecycle_capabilities(self) -> EnvironmentLifecycleCapabilities:
        return _MISMATCHED_CAPABILITIES


class _MismatchedFactory(_Factory):
    @classmethod
    def provider_key(cls) -> str:
        return "test.mismatch"

    def create_provider(
        self,
        configuration: BaseModel,
        *,
        runtime: EnvironmentProviderRuntime,
    ) -> EnvironmentProvider:
        assert isinstance(configuration, _Configuration)
        assert isinstance(runtime, _Runtime)
        return _MismatchedProvider()


class _FakeEntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self.value = f"test_provider:{getattr(target, '__name__', 'target')}"
        self.dist = SimpleNamespace(metadata={"Name": "test-provider"}, version="1.2.3")
        self._target = target
        self.load_count = 0

    def load(self) -> object:
        self.load_count += 1
        return self._target


def test_builtin_catalog_resolves_direct_local_without_metadata_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "a13n_environment_provider.factories._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("must not scan metadata")),
    )
    catalog = build_environment_provider_factory_catalog(builtin_keys=("a13n.direct-local",))

    assert isinstance(catalog.require("a13n.direct-local"), DirectLocalEnvironmentProviderFactory)
    provider = catalog.create_provider(
        EnvironmentProviderSpec(
            provider_key="a13n.direct-local",
            schema_version="1",
            parameters={
                "environment_id": "local-1",
                "root": {"path": str(tmp_path)},
            },
        ),
        runtime=DirectLocalProviderRuntime(),
    )
    assert isinstance(provider, EnvironmentProvider)


def test_extension_catalog_loads_only_explicitly_selected_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _FakeEntryPoint("test.sandbox", _Factory)
    unselected = _FakeEntryPoint("other.sandbox", RuntimeError)
    monkeypatch.setattr(
        "a13n_environment_provider.factories._entry_points",
        lambda: (unselected, selected),
    )

    catalog = build_environment_provider_factory_catalog(extension_keys=("test.sandbox",))
    spec = EnvironmentProviderSpec(
        provider_key="test.sandbox",
        schema_version="1",
        parameters={"name": "first"},
    )
    resolved = catalog.resolve_spec(spec)
    provider = catalog.create_provider(spec, runtime=_Runtime())

    assert resolved.lifecycle_capabilities == _CAPABILITIES
    assert isinstance(provider, _Provider)
    assert selected.load_count == 1
    assert unselected.load_count == 0
    assert catalog.registrations[0].distribution_name == "test-provider"


def test_discovery_reads_metadata_without_loading_target(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _FakeEntryPoint("test.sandbox", _Factory)
    monkeypatch.setattr(
        "a13n_environment_provider.factories._entry_points",
        lambda: (entry,),
    )

    references = discover_environment_provider_factory_references()

    assert references[0].provider_key == "test.sandbox"
    assert entry.load_count == 0


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"builtin_keys": ("a13n.local-envd",)}, "provider_factory_missing"),
        (
            {
                "builtin_keys": ("a13n.direct-local",),
                "explicit_factories": (DirectLocalEnvironmentProviderFactory(),),
            },
            "provider_factory_duplicate",
        ),
        (
            {"extension_keys": ("a13n.direct-local",)},
            "provider_factory_duplicate",
        ),
    ],
)
def test_catalog_rejects_placeholders_and_builtin_shadowing(
    kwargs: dict[str, Any],
    code: str,
) -> None:
    with pytest.raises(EnvironmentProviderError) as exc_info:
        build_environment_provider_factory_catalog(**kwargs)
    assert exc_info.value.code == code


def test_catalog_rejects_provider_capabilities_that_differ_from_factory_introspection() -> None:
    catalog = build_environment_provider_factory_catalog(
        explicit_factories=(_MismatchedFactory(),),
    )

    with pytest.raises(EnvironmentProviderError) as exc_info:
        catalog.create_provider(
            EnvironmentProviderSpec(
                provider_key="test.mismatch",
                schema_version="1",
                parameters={"name": "first"},
            ),
            runtime=_Runtime(),
        )

    assert exc_info.value.code == "provider_factory_target_invalid"


def test_catalog_rejects_unknown_schema_without_latest_fallback() -> None:
    catalog = build_environment_provider_factory_catalog(explicit_factories=(_Factory(),))
    with pytest.raises(EnvironmentProviderError) as exc_info:
        catalog.resolve_spec(
            EnvironmentProviderSpec(
                provider_key="test.sandbox",
                schema_version="2",
                parameters={"name": "first"},
            )
        )
    assert exc_info.value.code == "provider_schema_unsupported"
