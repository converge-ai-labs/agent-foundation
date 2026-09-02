"""Trusted process-local catalogs used by Agent UI composition resolution."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from a13n_environment_provider import (
    ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP,
    DirectLocalEnvironmentProvider,
    Environment,
    EnvironmentProvider,
    EnvironmentState,
    LocalEnvdEnvironmentProvider,
    build_environment_provider_catalog,
)
from a13n_environment_provider import (
    __version__ as environment_provider_version,
)
from a13n_harness.errors import PluginError
from a13n_harness.plugin_factories import (
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryRegistration,
    build_harness_plugin_factory_catalog,
)
from pydantic import BaseModel, ConfigDict, JsonValue

from a13n_ui import __version__ as agent_ui_version
from a13n_ui.errors import CompositionError
from a13n_ui.model_adapters import PydanticAiModelAdapter, model_adapter_registration

from .models import DependencyLock, ResolvedEnvironmentProfile

NATIVE_BINDER_KEY = "a13n.native-workspace"
LOCAL_EIP_BINDER_KEY = "a13n.local-eip-workspace"
NATIVE_PROVIDER_KEY = "a13n.direct-local"
LOCAL_EIP_PROVIDER_KEY = "a13n.local-envd"
MCP_ADAPTER_KEY = "a13n.mcp"


class ValidatedProfileConfiguration(BaseModel):
    """Marker base for package-owned credential-free profile templates."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class _EmptyProfileConfiguration(ValidatedProfileConfiguration):
    pass


@dataclass(frozen=True, slots=True)
class ProviderRuntime:
    """One fresh Provider instance and its process-local runtime collaborator."""

    provider: EnvironmentProvider
    collaborator: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, EnvironmentProvider):
            raise TypeError("provider runtime requires an EnvironmentProvider")


class EnvironmentWorkspaceBinder(ABC):
    """Trusted package boundary for static profile validation and later folder binding."""

    @property
    @abstractmethod
    def key(self) -> str: ...

    @property
    @abstractmethod
    def provider_key(self) -> str: ...

    @property
    @abstractmethod
    def provider_schema_version(self) -> str: ...

    @abstractmethod
    def validate_profile(
        self,
        *,
        provider_configuration: Mapping[str, JsonValue],
        profile_configuration: Mapping[str, JsonValue],
    ) -> ValidatedProfileConfiguration:
        """Validate and normalize a folder-independent profile without I/O."""

    @abstractmethod
    async def bind(
        self,
        *,
        profile: ResolvedEnvironmentProfile,
        folder: Path,
        state: EnvironmentState | None,
        runtime: ProviderRuntime,
    ) -> Environment:
        """Construct one fresh inert Provider adapter for a normalized folder."""


class NativeWorkspaceBinder(EnvironmentWorkspaceBinder):
    @property
    def key(self) -> str:
        return NATIVE_BINDER_KEY

    @property
    def provider_key(self) -> str:
        return NATIVE_PROVIDER_KEY

    @property
    def provider_schema_version(self) -> str:
        return "1"

    def validate_profile(
        self,
        *,
        provider_configuration: Mapping[str, JsonValue],
        profile_configuration: Mapping[str, JsonValue],
    ) -> ValidatedProfileConfiguration:
        return _empty_profile(provider_configuration, profile_configuration, kind="Native")

    async def bind(
        self,
        *,
        profile: ResolvedEnvironmentProfile,
        folder: Path,
        state: EnvironmentState | None,
        runtime: ProviderRuntime,
    ) -> Environment:
        _require_runtime(profile, runtime, DirectLocalEnvironmentProvider)
        shell = _host_shell()
        shell_profiles: list[JsonValue] = (
            [] if shell is None else [{"profile_id": "default", "executable": str(shell), "allow_login": True}]
        )
        environment_keys = list[JsonValue](sorted(os.environ))
        value: dict[str, JsonValue] = {
            "environment_id": _environment_id("native", folder),
            "root": {"path": str(folder), "read_only": False},
            "shell_profiles": shell_profiles,
            "allowed_environment_keys": environment_keys,
        }
        configuration = runtime.provider.validate_configuration(
            schema_version=profile.provider_schema_version,
            value=value,
        )
        return runtime.provider.create_environment(
            configuration=configuration,
            state=state,
            runtime=runtime.collaborator,
        )


class LocalEipWorkspaceBinder(EnvironmentWorkspaceBinder):
    @property
    def key(self) -> str:
        return LOCAL_EIP_BINDER_KEY

    @property
    def provider_key(self) -> str:
        return LOCAL_EIP_PROVIDER_KEY

    @property
    def provider_schema_version(self) -> str:
        return "1"

    def validate_profile(
        self,
        *,
        provider_configuration: Mapping[str, JsonValue],
        profile_configuration: Mapping[str, JsonValue],
    ) -> ValidatedProfileConfiguration:
        return _empty_profile(provider_configuration, profile_configuration, kind="Local EIP")

    async def bind(
        self,
        *,
        profile: ResolvedEnvironmentProfile,
        folder: Path,
        state: EnvironmentState | None,
        runtime: ProviderRuntime,
    ) -> Environment:
        _require_runtime(profile, runtime, LocalEnvdEnvironmentProvider)
        shell = _host_shell()
        trusted_roots: list[JsonValue] = [] if shell is None else [str(shell.parent)]
        shell_profiles: list[JsonValue] = (
            [] if shell is None else [{"profile_id": "default", "executable": str(shell), "allow_login": True}]
        )
        value: dict[str, JsonValue] = {
            "environment_id": _environment_id("local-eip", folder),
            "workspace": {"path": str(folder), "read_only": False},
            "execution_network": "deny",
            "trusted_executable_roots": trusted_roots,
            "shell_profiles": shell_profiles,
        }
        configuration = runtime.provider.validate_configuration(
            schema_version=profile.provider_schema_version,
            value=value,
        )
        return runtime.provider.create_environment(
            configuration=configuration,
            state=state,
            runtime=runtime.collaborator,
        )


@dataclass(frozen=True, slots=True)
class BinderCatalogEntry:
    binder: EnvironmentWorkspaceBinder
    lock: DependencyLock


@dataclass(frozen=True, slots=True)
class ProviderCatalogEntry:
    provider: EnvironmentProvider
    lock: DependencyLock


class WorkspaceBinderCatalog:
    """Immutable explicit catalog of Host-approved workspace binders."""

    def __init__(self, entries: Iterable[BinderCatalogEntry]) -> None:
        mapped: dict[str, BinderCatalogEntry] = {}
        for entry in entries:
            if not isinstance(entry.binder, EnvironmentWorkspaceBinder):
                raise TypeError("binder must implement EnvironmentWorkspaceBinder")
            if entry.lock.dependency_kind != "workspace-binder" or entry.lock.key != entry.binder.key:
                raise ValueError("binder lock does not match its implementation")
            _require_lock_implementation(entry.lock, entry.binder)
            if entry.binder.key in mapped:
                raise ValueError("workspace binder keys must be unique")
            mapped[entry.binder.key] = entry
        self._entries = MappingProxyType(mapped)

    def require(self, key: str) -> BinderCatalogEntry:
        entry = self._entries.get(key)
        if entry is None:
            raise CompositionError(
                "The selected Environment workspace binder is unavailable.",
                code="workspace_binder_missing",
                details={"binder_key": key},
            )
        return entry


class TrustedProviderCatalog:
    """Immutable selected Environment Provider instances with exact provenance."""

    def __init__(self, entries: Iterable[ProviderCatalogEntry]) -> None:
        mapped: dict[str, ProviderCatalogEntry] = {}
        for entry in entries:
            if not isinstance(entry.provider, EnvironmentProvider):
                raise TypeError("provider must be an EnvironmentProvider")
            if entry.lock.dependency_kind != "provider" or entry.lock.key != entry.provider.key:
                raise ValueError("provider lock does not match its implementation")
            _require_lock_implementation(entry.lock, entry.provider)
            if entry.provider.key in mapped:
                raise ValueError("Environment Provider keys must be unique")
            mapped[entry.provider.key] = entry
        self._entries = MappingProxyType(mapped)

    def require(self, key: str) -> ProviderCatalogEntry:
        entry = self._entries.get(key)
        if entry is None:
            raise CompositionError(
                "The selected Environment Provider is unavailable.",
                code="environment_provider_missing",
                details={"provider_key": key},
            )
        return entry


def builtin_workspace_binder_catalog(
    *,
    extensions: Iterable[BinderCatalogEntry] = (),
) -> WorkspaceBinderCatalog:
    """Build the two release-owned binders plus explicit Host-approved extensions."""

    native = NativeWorkspaceBinder()
    local_eip = LocalEipWorkspaceBinder()
    return WorkspaceBinderCatalog(
        (
            BinderCatalogEntry(native, _app_lock("workspace-binder", native.key, native)),
            BinderCatalogEntry(local_eip, _app_lock("workspace-binder", local_eip.key, local_eip)),
            *tuple(extensions),
        )
    )


def selected_provider_catalog(
    *,
    provider_keys: Iterable[str],
    explicit_entries: Iterable[ProviderCatalogEntry] = (),
) -> TrustedProviderCatalog:
    """Load only selected Providers and retain exact import/distribution provenance."""

    requested = tuple(dict.fromkeys(provider_keys))
    builtin_keys = tuple(key for key in requested if key in {NATIVE_PROVIDER_KEY, LOCAL_EIP_PROVIDER_KEY})
    extension_keys = tuple(key for key in requested if key not in set(builtin_keys))
    explicit = {entry.provider.key: entry for entry in explicit_entries}
    overlap = set(requested) & explicit.keys()
    if overlap:
        requested = tuple(key for key in requested if key not in overlap)
        builtin_keys = tuple(key for key in builtin_keys if key not in overlap)
        extension_keys = tuple(key for key in extension_keys if key not in overlap)

    entries: list[ProviderCatalogEntry] = []
    if builtin_keys:
        catalog = build_environment_provider_catalog(builtin_keys=builtin_keys)
        for key in builtin_keys:
            provider = catalog.require(key)
            entries.append(
                ProviderCatalogEntry(
                    provider=provider,
                    lock=DependencyLock(
                        dependency_kind="provider",
                        key=key,
                        class_module=type(provider).__module__,
                        class_qualname=type(provider).__qualname__,
                        distribution_name="a13n-environment-provider",
                        distribution_version=environment_provider_version,
                    ),
                )
            )
    entries.extend(_load_extension_providers(extension_keys))
    entries.extend(explicit.values())
    return TrustedProviderCatalog(entries)


def selected_plugin_catalog(
    plugin_keys: Iterable[str],
    *,
    catalog: HarnessPluginFactoryCatalog | None = None,
) -> HarnessPluginFactoryCatalog:
    """Build or verify the exact selected Harness Plugin factory catalog."""

    keys = tuple(dict.fromkeys(plugin_keys))
    try:
        selected = catalog or build_harness_plugin_factory_catalog(plugin_keys=keys)
        for key in keys:
            selected.require(key)
    except PluginError as exc:
        raise CompositionError(
            "A selected Harness Plugin factory could not be loaded.",
            code="plugin_catalog_invalid",
        ) from exc
    return selected


def plugin_dependency_lock(registration: HarnessPluginFactoryRegistration) -> DependencyLock:
    """Convert Harness registration provenance into a persisted exact lock."""

    distribution_name = registration.distribution_name or "a13n-ui"
    distribution_version = registration.distribution_version or agent_ui_version
    return DependencyLock(
        dependency_kind="plugin",
        key=registration.plugin_key,
        class_module=registration.class_module,
        class_qualname=registration.class_qualname,
        import_target=registration.import_target,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
    )


def pydantic_ai_adapter_lock() -> DependencyLock:
    registration = model_adapter_registration(PydanticAiModelAdapter.key)
    if registration is None:
        raise CompositionError(
            "The Agent UI Model adapter package provenance is unavailable.",
            code="model_adapter_missing",
        )
    return DependencyLock(
        dependency_kind="model-adapter",
        key=registration.adapter_key,
        class_module=PydanticAiModelAdapter.__module__,
        class_qualname=PydanticAiModelAdapter.__qualname__,
        distribution_name=registration.distribution_name,
        distribution_version=registration.distribution_version,
    )


def mcp_adapter_lock() -> DependencyLock:
    return _app_lock("mcp-adapter", MCP_ADAPTER_KEY, _McpAdapterMarker)


class _McpAdapterMarker:
    """Stable provenance marker for Agent UI MCP runtime reconstruction."""


def _require_runtime(
    profile: ResolvedEnvironmentProfile,
    runtime: ProviderRuntime,
    provider_type: type[EnvironmentProvider],
) -> None:
    if runtime.provider.key != profile.provider_key or not isinstance(runtime.provider, provider_type):
        raise CompositionError(
            "The reconstructed Environment Provider is incompatible with its workspace binder.",
            code="workspace_binder_incompatible",
            details={"binder_key": profile.binder_key, "provider_key": profile.provider_key},
        )


def _environment_id(prefix: str, folder: Path) -> str:
    digest = hashlib.sha256(os.fsencode(folder)).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _host_shell() -> Path | None:
    if os.name == "nt":
        return None
    configured = os.environ.get("SHELL")
    candidate = Path(configured) if configured else Path("/bin/sh")
    return Path(os.path.abspath(candidate.expanduser()))


def _empty_profile(
    provider_configuration: Mapping[str, JsonValue],
    profile_configuration: Mapping[str, JsonValue],
    *,
    kind: str,
) -> _EmptyProfileConfiguration:
    if provider_configuration or profile_configuration:
        raise CompositionError(
            f"{kind} profile configuration is not supported by this Agent UI release.",
            code="environment_profile_configuration_invalid",
        )
    return _EmptyProfileConfiguration()


def _require_lock_implementation(lock: DependencyLock, implementation: object) -> None:
    implementation_type = implementation if isinstance(implementation, type) else type(implementation)
    if lock.class_module != implementation_type.__module__ or lock.class_qualname != implementation_type.__qualname__:
        raise ValueError("dependency lock class provenance does not match its implementation")


def _app_lock(
    kind: Literal["model-adapter", "plugin", "mcp-adapter", "provider", "workspace-binder"],
    key: str,
    implementation: object,
) -> DependencyLock:
    return DependencyLock(
        dependency_kind=kind,
        key=key,
        class_module=type(implementation).__module__
        if not isinstance(implementation, type)
        else implementation.__module__,
        class_qualname=type(implementation).__qualname__
        if not isinstance(implementation, type)
        else implementation.__qualname__,
        distribution_name="a13n-ui",
        distribution_version=agent_ui_version,
    )


def _load_extension_providers(keys: tuple[str, ...]) -> tuple[ProviderCatalogEntry, ...]:
    if not keys:
        return ()
    try:
        points = tuple(importlib.metadata.entry_points(group=ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP))
    except Exception as exc:
        raise CompositionError(
            "Environment Provider metadata could not be enumerated.",
            code="environment_provider_catalog_invalid",
        ) from exc
    grouped: dict[str, list[importlib.metadata.EntryPoint]] = {}
    for point in points:
        if point.name in keys:
            grouped.setdefault(point.name, []).append(point)
    entries: list[ProviderCatalogEntry] = []
    for key in keys:
        matches = grouped.get(key, [])
        if len(matches) != 1:
            code = "environment_provider_missing" if not matches else "environment_provider_ambiguous"
            raise CompositionError(
                "The selected Environment Provider entry point is missing or ambiguous.",
                code=code,
                details={"provider_key": key},
            )
        point = matches[0]
        try:
            loaded = point.load()
            provider = loaded() if isinstance(loaded, type) else loaded
            distribution = point.dist
            distribution_name = None if distribution is None else distribution.metadata.get("Name")
            distribution_version = None if distribution is None else distribution.version
        except Exception as exc:
            raise CompositionError(
                "The selected Environment Provider could not be loaded.",
                code="environment_provider_catalog_invalid",
                details={"provider_key": key},
            ) from exc
        if (
            not isinstance(provider, EnvironmentProvider)
            or provider.key != key
            or not isinstance(distribution_name, str)
            or not distribution_name
            or not isinstance(distribution_version, str)
            or not distribution_version
        ):
            raise CompositionError(
                "The selected Environment Provider has incomplete trusted provenance.",
                code="environment_provider_catalog_invalid",
                details={"provider_key": key},
            )
        entries.append(
            ProviderCatalogEntry(
                provider=provider,
                lock=DependencyLock(
                    dependency_kind="provider",
                    key=key,
                    class_module=type(provider).__module__,
                    class_qualname=type(provider).__qualname__,
                    import_target=point.value,
                    distribution_name=distribution_name,
                    distribution_version=distribution_version,
                ),
            )
        )
    return tuple(entries)


__all__ = [
    "LOCAL_EIP_BINDER_KEY",
    "LOCAL_EIP_PROVIDER_KEY",
    "MCP_ADAPTER_KEY",
    "NATIVE_BINDER_KEY",
    "NATIVE_PROVIDER_KEY",
    "BinderCatalogEntry",
    "EnvironmentWorkspaceBinder",
    "ProviderCatalogEntry",
    "ProviderRuntime",
    "TrustedProviderCatalog",
    "ValidatedProfileConfiguration",
    "WorkspaceBinderCatalog",
    "builtin_workspace_binder_catalog",
    "mcp_adapter_lock",
    "plugin_dependency_lock",
    "pydantic_ai_adapter_lock",
    "selected_plugin_catalog",
    "selected_provider_catalog",
]
