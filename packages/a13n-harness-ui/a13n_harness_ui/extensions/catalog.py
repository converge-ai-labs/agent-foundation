"""Process-local discovery and selected construction for Harness UI extensions."""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

from a13n_harness.capabilities import DocumentsCapability, ToolReviewConfig, WebCapability
from a13n_harness.capabilities.codeact import CodeActCapability, CodeActConfig
from a13n_harness.capabilities.context import (
    CompactionCapability,
    CompactionPolicy,
    FileContextCapability,
    FileContextConfiguration,
    HandoffCapability,
    HandoffConfiguration,
    RuntimeContextCapability,
    RuntimeContextConfiguration,
)
from a13n_harness.capabilities.documents import DocumentsConfiguration
from a13n_harness.capabilities.interaction import UserInteractionCapability
from a13n_harness.capabilities.native_image_generation import NativeImageGenerationCapability
from a13n_harness.capabilities.skills import FileSkillSource, SkillManager, SkillsCapability, SkillsPolicy
from a13n_harness.capabilities.web import WebConfiguration
from a13n_harness.capabilities.working_state import WorkingStateCapability, WorkingStateConfiguration
from a13n_harness.capability_types import CapabilityTypeCatalog, first_party_declarative_capability_types
from a13n_harness.environment import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentRunExtensionFactory,
    EnvironmentRunExtensionFactoryCatalog,
    build_environment_run_extension_factory_catalog,
    discover_environment_run_extension_factory_references,
)
from a13n_harness.errors import DefinitionError
from a13n_harness.plugin_factories import (
    HarnessPluginFactory,
    HarnessPluginFactoryCatalog,
    build_harness_plugin_factory_catalog,
    discover_harness_plugin_factory_references,
)
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment import EnvironmentProviderDefinition
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_harness.providers.plugins import load_provider_plugins
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError, model_validator
from pydantic_ai.capabilities import CAPABILITY_TYPES, AbstractCapability, NativeTool
from pydantic_ai.native_tools import ImageGenerationTool

from a13n_harness_ui.environment_paths import BUILTIN_SKILLS_PATH, BUILTIN_SKILLS_SOURCE_ID, EnvironmentPathLayout
from a13n_harness_ui.errors import CompositionError

from .environment_adapters import (
    EnvironmentProjectAdapter,
    LocalEnvdProjectAdapter,
    NativeProjectAdapter,
)

_CAPABILITY_ENTRY_POINT_GROUP = "a13n_harness_ui.capabilities"
_BUILTIN_PROVIDER_KEYS = frozenset({"direct_local", "local_envd", "docker", "e2b", "http_envd", "websocket_envd"})
_BUILTIN_CAPABILITIES: dict[str, type[AbstractCapability[Any]]] = {
    "dynamic_environment": DynamicEnvironmentCapability,
    "codeact": CodeActCapability,
    "compaction": CompactionCapability,
    "file_context": FileContextCapability,
    "handoff": HandoffCapability,
    "runtime_context": RuntimeContextCapability,
    "documents": DocumentsCapability,
    "skills": SkillsCapability,
    "web": WebCapability,
    "native_image_generation": NativeImageGenerationCapability,
    "working_state": WorkingStateCapability,
    "user_interaction": UserInteractionCapability,
    **{
        name: item
        for item in first_party_declarative_capability_types()
        if (name := item.get_serialization_name()) is not None
    },
    **{name: item for name, item in CAPABILITY_TYPES.items() if name is not None},
}


class SkillsConfiguration(BaseModel):
    """Harness UI source additions for the built-in Harness Skills Capability."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    roots: tuple[str, ...] = Field(default=(), max_length=128)

    @model_validator(mode="before")
    @classmethod
    def _normalize_roots(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if isinstance(normalized.get("roots"), list):
            normalized["roots"] = tuple(normalized["roots"])
        return normalized

    @model_validator(mode="after")
    def _unique_roots(self) -> SkillsConfiguration:
        if len(set(self.roots)) != len(self.roots):
            raise ValueError("Skill roots must be unique")
        return self


class CatalogReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: Literal["capability", "harness_plugin", "environment_provider", "environment_run_extension"]
    key: str = Field(min_length=1, max_length=200)
    source: Literal["pydantic", "harness", "harness_ui", "installed", "host"]
    distribution_name: str | None = None
    distribution_version: str | None = None
    import_target: str | None = None
    configurable: bool


@dataclass(frozen=True, slots=True)
class ImplementationReference:
    key: str
    source: Literal["installed", "host"]
    implementation_type: type[object]
    import_target: str | None = None
    distribution_name: str | None = None
    distribution_version: str | None = None


@dataclass(frozen=True, slots=True)
class SelectedCapability:
    key: str
    capability: AbstractCapability[Any]
    implementation: ImplementationReference


class HarnessUiExtensionCatalog:
    """One immutable metadata snapshot with selected-only import boundaries."""

    def __init__(
        self,
        *,
        host_capabilities: dict[str, type[AbstractCapability[Any]]] | None = None,
        host_providers: tuple[EnvironmentProviderDefinition, ...] = (),
        provider_plugins: tuple[str, ...] = (),
        host_adapters: tuple[EnvironmentProjectAdapter, ...] = (),
        host_plugin_factories: tuple[HarnessPluginFactory, ...] = (),
        host_run_extension_factories: tuple[EnvironmentRunExtensionFactory, ...] = (),
    ) -> None:
        self._host_capabilities = MappingProxyType(dict(host_capabilities or {}))
        self._host_providers = host_providers
        self._provider_plugins = load_provider_plugins(provider_plugins)
        self._providers = dict(
            ProviderCatalog(
                (
                    *BUILT_IN_ENVIRONMENT_PROVIDERS,
                    *(definition for plugin in self._provider_plugins for definition in plugin.manifest.environment),
                )
            )
        )
        self._providers.update((definition.type, definition) for definition in host_providers)
        self._host_plugins = host_plugin_factories
        self._host_run_extensions = host_run_extension_factories
        adapters = (NativeProjectAdapter(), LocalEnvdProjectAdapter(), *host_adapters)
        mapped_adapters: dict[str, EnvironmentProjectAdapter] = {}
        for adapter in adapters:
            if not isinstance(adapter, EnvironmentProjectAdapter):
                raise TypeError("host adapters must implement EnvironmentProjectAdapter")
            if adapter.key in mapped_adapters:
                raise ValueError("Environment adapter keys must be unique")
            mapped_adapters[adapter.key] = adapter
        self._adapters = MappingProxyType(mapped_adapters)
        self._host_adapter_keys = frozenset(item.key for item in host_adapters)
        self._capability_entry_points: dict[str, tuple[importlib.metadata.EntryPoint, ...]] = {}
        self._ambiguous: dict[str, frozenset[str]] = {}
        self._references: tuple[CatalogReference, ...] = ()
        self.refresh()

    @property
    def references(self) -> tuple[CatalogReference, ...]:
        return tuple(
            item.model_copy(deep=True)
            for item in self._references
            if item.kind != "capability" or item.key != "ToolPermissionsCapability"
        )

    def refresh(self) -> tuple[CatalogReference, ...]:
        """Refresh process-local package metadata without replacing imported Host code."""

        capability_entry_points = _capability_entry_points()
        discovered = self._discover(capability_entry_points)
        grouped: dict[tuple[str, str], int] = {}
        for item in discovered:
            identity = (item.kind, item.key)
            grouped[identity] = grouped.get(identity, 0) + 1
        ambiguous = {
            kind: frozenset(
                key for (selected_kind, key), count in grouped.items() if selected_kind == kind and count > 1
            )
            for kind in (
                "capability",
                "harness_plugin",
                "environment_provider",
                "environment_run_extension",
            )
        }
        references = tuple(
            item.model_copy(update={"configurable": False}) if item.key in ambiguous[item.kind] else item
            for item in discovered
        )
        self._capability_entry_points = capability_entry_points
        self._ambiguous = ambiguous
        self._references = references
        return self.references

    def capabilities(
        self,
        selections: tuple[tuple[str, dict[str, JsonValue]], ...],
        *,
        path_layout: EnvironmentPathLayout | None = None,
    ) -> tuple[SelectedCapability, ...]:
        result: list[SelectedCapability] = []
        custom_types: list[type[AbstractCapability[Any]]] = []
        for key, configuration in selections:
            self._require_unambiguous("capability", key)
            capability_type = _BUILTIN_CAPABILITIES.get(key)
            source: Literal["installed", "host"] = "installed"
            import_target = distribution_name = distribution_version = None
            if capability_type is None and key in self._host_capabilities:
                capability_type = self._host_capabilities[key]
                source = "host"
                custom_types.append(capability_type)
            elif capability_type is None:
                matches = self._capability_entry_points.get(key, ())
                if len(matches) != 1:
                    raise CompositionError(
                        "A selected Capability is unavailable or ambiguous.",
                        code="capability_catalog_invalid",
                        details={"capability": key},
                    )
                entry = matches[0]
                try:
                    loaded = entry.load()
                except Exception as exc:
                    raise CompositionError(
                        "A selected Capability could not be loaded.",
                        code="capability_catalog_invalid",
                        details={"capability": key},
                    ) from exc
                if not isinstance(loaded, type) or not issubclass(loaded, AbstractCapability):
                    raise CompositionError("A Capability entry point is invalid.", code="capability_catalog_invalid")
                capability_type = cast(type[AbstractCapability[Any]], loaded)
                if capability_type.get_serialization_name() != key:
                    raise CompositionError(
                        "A Capability entry-point key is invalid.", code="capability_catalog_invalid"
                    )
                custom_types.append(capability_type)
                import_target = entry.value
                if entry.dist is not None:
                    distribution_name = entry.dist.metadata.get("Name")
                    distribution_version = entry.dist.version
            if capability_type in custom_types:
                try:
                    CapabilityTypeCatalog.from_types(custom_types)
                except DefinitionError as exc:
                    raise CompositionError(
                        "A selected Capability type is not eligible for declarative configuration.",
                        code="capability_catalog_invalid",
                        details={"capability": key},
                    ) from exc
                if capability_type.get_serialization_name() != key:
                    raise CompositionError(
                        "A Capability catalog key does not match its serialization name.",
                        code="capability_catalog_invalid",
                        details={"capability": key},
                    )
            try:
                capability = _construct_capability(
                    capability_type,
                    configuration,
                    path_layout=path_layout,
                )
            except (TypeError, ValidationError, ValueError) as exc:
                raise CompositionError(
                    "Capability configuration is invalid.",
                    code="capability_configuration_invalid",
                    details={"capability": key},
                ) from exc
            result.append(
                SelectedCapability(
                    key=key,
                    capability=capability,
                    implementation=ImplementationReference(
                        key=key,
                        source=source,
                        implementation_type=capability_type,
                        import_target=import_target,
                        distribution_name=distribution_name,
                        distribution_version=distribution_version,
                    ),
                )
            )
        return tuple(result)

    def plugin_catalog(self, keys: tuple[str, ...]) -> HarnessPluginFactoryCatalog:
        for key in keys:
            self._require_unambiguous("harness_plugin", key)
        host = {item.plugin_key(): item for item in self._host_plugins}
        selected = tuple(key for key in dict.fromkeys(keys) if key not in host)
        explicit = tuple(host[key] for key in dict.fromkeys(keys) if key in host)
        return build_harness_plugin_factory_catalog(plugin_keys=selected, explicit_factories=explicit)

    def provider_catalog(self, keys: tuple[str, ...]) -> ProviderCatalog[EnvironmentProviderDefinition]:
        for key in keys:
            self._require_unambiguous("environment_provider", key)
        return ProviderCatalog(self._providers[key] for key in dict.fromkeys(keys))

    def provider_reference(self, key: str) -> CatalogReference:
        return next(item for item in self.references if item.kind == "environment_provider" and item.key == key)

    def run_extension_catalog(self, keys: tuple[str, ...]) -> EnvironmentRunExtensionFactoryCatalog:
        for key in keys:
            self._require_unambiguous("environment_run_extension", key)
        host = {item.extension_key(): item for item in self._host_run_extensions}
        selected = tuple(key for key in dict.fromkeys(keys) if key not in host)
        explicit = tuple(host[key] for key in dict.fromkeys(keys) if key in host)
        return build_environment_run_extension_factory_catalog(
            extension_keys=selected,
            explicit_factories=explicit,
        )

    def _require_unambiguous(self, kind: str, key: str) -> None:
        if key in self._ambiguous.get(kind, frozenset()):
            raise CompositionError(
                "A selected catalog key is ambiguous.",
                code="extension_catalog_ambiguous",
                details={"kind": kind, "key": key},
            )

    def environment_adapter(self, key: str, provider_key: str) -> EnvironmentProjectAdapter:
        adapter = self._adapters.get(key)
        if adapter is None or adapter.provider_key != provider_key:
            raise CompositionError(
                "The selected Environment Provider has no compatible approved Harness UI adapter.",
                code="environment_adapter_missing",
                details={"provider_key": provider_key, "adapter_key": key},
            )
        return adapter

    def adapter_reference(self, key: str) -> ImplementationReference:
        adapter = self._adapters.get(key)
        if adapter is None:
            raise CompositionError(
                "The selected Environment adapter is unavailable.", code="environment_adapter_missing"
            )
        return ImplementationReference(
            key=key,
            source="host" if key in self._host_adapter_keys else "installed",
            implementation_type=type(adapter),
        )

    def provider_source(self, key: str) -> Literal["installed", "host"]:
        return "host" if any(item.type == key for item in self._host_providers) else "installed"

    def plugin_source(self, key: str) -> Literal["installed", "host"]:
        return "host" if any(item.plugin_key() == key for item in self._host_plugins) else "installed"

    def run_extension_source(self, key: str) -> Literal["installed", "host"]:
        return "host" if any(item.extension_key() == key for item in self._host_run_extensions) else "installed"

    def _discover(
        self,
        capability_entry_points: dict[str, tuple[importlib.metadata.EntryPoint, ...]],
    ) -> tuple[CatalogReference, ...]:
        values: list[CatalogReference] = []
        for key in sorted(_BUILTIN_CAPABILITIES):
            values.append(CatalogReference(kind="capability", key=key, source=_builtin_source(key), configurable=True))
        for key in sorted(self._host_capabilities):
            values.append(CatalogReference(kind="capability", key=key, source="host", configurable=True))
        for key, entries in sorted(capability_entry_points.items()):
            for entry in entries:
                values.append(
                    CatalogReference(
                        kind="capability",
                        key=key,
                        source="installed",
                        distribution_name=entry.dist.metadata.get("Name") if entry.dist is not None else None,
                        distribution_version=entry.dist.version if entry.dist is not None else None,
                        import_target=entry.value,
                        configurable=len(entries) == 1,
                    )
                )
        adapter_providers = {item.provider_key for item in self._adapters.values()}
        for key in sorted(_BUILTIN_PROVIDER_KEYS):
            values.append(
                CatalogReference(
                    kind="environment_provider",
                    key=key,
                    source="harness_ui",
                    configurable=key in adapter_providers,
                )
            )
        for provider in self._host_providers:
            values.append(
                CatalogReference(
                    kind="environment_provider",
                    key=provider.type,
                    source="host",
                    configurable=provider.type in adapter_providers,
                )
            )
        for plugin in self._provider_plugins:
            for definition in plugin.manifest.environment:
                values.append(
                    CatalogReference(
                        kind="environment_provider",
                        key=definition.type,
                        source="installed",
                        distribution_name=plugin.distribution_name,
                        distribution_version=plugin.distribution_version,
                        import_target=plugin.import_target,
                        configurable=definition.type in adapter_providers,
                    )
                )
        for item in discover_harness_plugin_factory_references():
            values.append(
                CatalogReference(
                    kind="harness_plugin",
                    key=item.plugin_key,
                    source="installed",
                    distribution_name=item.distribution_name,
                    distribution_version=item.distribution_version,
                    import_target=item.import_target,
                    configurable=True,
                )
            )
        for item in self._host_plugins:
            values.append(
                CatalogReference(kind="harness_plugin", key=item.plugin_key(), source="host", configurable=True)
            )
        for item in discover_environment_run_extension_factory_references():
            values.append(
                CatalogReference(
                    kind="environment_run_extension",
                    key=item.extension_key,
                    source="installed",
                    distribution_name=item.distribution_name,
                    distribution_version=item.distribution_version,
                    import_target=item.import_target,
                    configurable=True,
                )
            )
        for item in self._host_run_extensions:
            values.append(
                CatalogReference(
                    kind="environment_run_extension", key=item.extension_key(), source="host", configurable=True
                )
            )
        return tuple(sorted(values, key=lambda item: (item.kind, item.key, item.source, item.import_target or "")))


def _construct_capability(
    capability_type: type[AbstractCapability[Any]],
    configuration: dict[str, JsonValue],
    *,
    path_layout: EnvironmentPathLayout | None,
) -> AbstractCapability[Any]:
    if capability_type is CodeActCapability:
        arguments: dict[str, Any] = dict(configuration)
        return CodeActCapability(CodeActConfig(**arguments))
    if capability_type is WorkingStateCapability:
        return WorkingStateCapability(WorkingStateConfiguration.model_validate(configuration))
    if capability_type is CompactionCapability:
        return CompactionCapability(CompactionPolicy.model_validate(configuration) if configuration else None)
    if capability_type is FileContextCapability:
        file_configuration: dict[str, Any] = dict(configuration)
        if isinstance(file_configuration.get("paths"), list):
            file_configuration["paths"] = tuple(file_configuration["paths"])
        return FileContextCapability(FileContextConfiguration.model_validate(file_configuration))
    if capability_type is HandoffCapability:
        return HandoffCapability(HandoffConfiguration.model_validate(configuration))
    if capability_type is RuntimeContextCapability:
        return RuntimeContextCapability(RuntimeContextConfiguration.model_validate(configuration))
    if capability_type is DynamicEnvironmentCapability:
        return DynamicEnvironmentCapability(DynamicEnvironmentConfiguration.model_validate(configuration))
    if capability_type is DocumentsCapability:
        return DocumentsCapability(DocumentsConfiguration.model_validate(configuration))
    if capability_type is WebCapability:
        return WebCapability(WebConfiguration.model_validate(configuration))
    if capability_type is ToolPermissionsCapability:
        permissions = dict(configuration)
        review = permissions.pop("review", None)
        review_config = None
        if review is not None:
            if not isinstance(review, dict):
                raise ValueError("Tool review must be an object")
            review_config = ToolReviewConfig.model_validate({"on_flagged": "approval_required", **review})
        return ToolPermissionsCapability(ToolPermissions.model_validate(permissions), review=review_config)
    if capability_type is SkillsCapability:
        return _construct_skills_capability(
            configuration,
            path_layout=path_layout
            or EnvironmentPathLayout(
                project_mounts=("/workspace",),
                user_skills="/environment/user-skills",
            ),
        )

    if capability_type is NativeImageGenerationCapability:
        from a13n_harness_ui.capability_runtime import save_native_image

        return NativeImageGenerationCapability(
            saver=save_native_image,
            tool=TypeAdapter(ImageGenerationTool).validate_python(configuration),
        )
    if capability_type is NativeTool:
        native_arguments: dict[str, Any] = dict(configuration)
        return NativeTool.from_spec(**native_arguments)

    arguments = dict(configuration)
    return capability_type(**arguments)


def _construct_skills_capability(
    configuration: dict[str, JsonValue],
    *,
    path_layout: EnvironmentPathLayout,
) -> SkillsCapability:
    if len(path_layout.project_mounts) > 64:
        raise ValueError("path_layout must contain at most 64 Project mounts")
    parsed = SkillsConfiguration.model_validate(configuration, strict=True)
    sources = [
        FileSkillSource(
            BUILTIN_SKILLS_SOURCE_ID,
            (BUILTIN_SKILLS_PATH,),
            required=True,
        ),
        FileSkillSource(
            "a13n-harness-ui:user-skills",
            (path_layout.user_skills,),
            required=False,
        ),
    ]
    sources.extend(
        FileSkillSource(
            f"a13n-harness-ui:content-plugin:{plugin_id}",
            (path,),
            required=False,
            skip_invalid=True,
        )
        for plugin_id, path in path_layout.content_plugin_skills
    )
    for index in range(len(path_layout.project_mounts), 1, -1):
        sources.append(
            FileSkillSource(
                f"a13n-harness-ui:project:workspace-{index}",
                (_join_mount_path(path_layout.project_mounts[index - 1], ".agents/skills"),),
                required=False,
            )
        )
    if path_layout.project_mounts:
        sources.append(
            FileSkillSource(
                "a13n-harness-ui:project:workspace",
                (_join_mount_path(path_layout.project_mounts[0], ".agents/skills"),),
                required=False,
            )
        )
    sources.extend(
        FileSkillSource(
            f"a13n-harness-ui:device:{alias}",
            (_join_mount_path(root, ".agents/skills"),),
            required=False,
        )
        for alias, root in path_layout.device_working_directories
    )
    sources.extend(
        FileSkillSource(
            f"a13n-harness-ui:explicit:{index}",
            (root,),
            required=True,
        )
        for index, root in enumerate(parsed.roots, start=1)
    )
    return SkillsCapability(
        SkillManager(
            sources,
            policy=SkillsPolicy(conflict="prefer_later"),
        )
    )


def _join_mount_path(root: str, suffix: str) -> str:
    separator = "" if root.endswith("/") else "/"
    return f"{root}{separator}{suffix}"


def _capability_entry_points() -> dict[str, tuple[importlib.metadata.EntryPoint, ...]]:
    try:
        entries = tuple(importlib.metadata.entry_points(group=_CAPABILITY_ENTRY_POINT_GROUP))
    except Exception as exc:
        raise CompositionError(
            "Capability metadata could not be enumerated.", code="capability_catalog_invalid"
        ) from exc
    grouped: dict[str, list[importlib.metadata.EntryPoint]] = {}
    for entry in entries:
        grouped.setdefault(entry.name, []).append(entry)
    return {key: tuple(value) for key, value in grouped.items()}


def _builtin_source(key: str) -> Literal["pydantic", "harness", "harness_ui"]:
    if key in CAPABILITY_TYPES:
        return "pydantic"
    harness_names = {item.get_serialization_name() for item in first_party_declarative_capability_types()}
    return "harness" if key in harness_names else "harness_ui"


__all__ = [
    "CatalogReference",
    "HarnessUiExtensionCatalog",
    "ImplementationReference",
    "SelectedCapability",
]
