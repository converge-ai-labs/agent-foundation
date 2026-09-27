"""Immutable recipes captured before Host credential resolution for one UI Run."""

from __future__ import annotations

from typing import Literal, Self, get_args, get_origin

from a13n_harness.toolsets.file_media import NativeInputMediaKind
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from a13n_harness_ui.configuration import McpTransport, ModelAuthentication
from a13n_harness_ui.configuration.models import (
    AgentToolProxy,
    DeviceResource,
    ModelCharacteristics,
    SidekickConfiguration,
)
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection, validate_environment_selection
from a13n_harness_ui.model_thinking import ThinkingSelection


class CompositionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_tuples(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name, field in cls.model_fields.items():
            if isinstance(normalized.get(name), list) and _contains_tuple(field.annotation):
                normalized[name] = tuple(normalized[name])
        return normalized


class DependencyProvenance(CompositionModel):
    kind: Literal[
        "capability",
        "harness_plugin",
        "environment_provider",
        "environment_run_extension",
        "environment_adapter",
    ]
    key: str = Field(min_length=1, max_length=200)
    source: Literal["installed", "host"]
    class_module: str = Field(min_length=1, max_length=512)
    class_qualname: str = Field(min_length=1, max_length=512)
    import_target: str | None = Field(default=None, min_length=1, max_length=1024)
    distribution_name: str | None = Field(default=None, min_length=1, max_length=256)
    distribution_version: str | None = Field(default=None, min_length=1, max_length=128)


class ResolvedModelRecipe(CompositionModel):
    model_id: str = Field(min_length=1, max_length=128)
    route: str = Field(min_length=3, max_length=512)
    authentication: ModelAuthentication
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    model_configuration: dict[str, JsonValue] = Field(default_factory=dict)
    model_characteristics: ModelCharacteristics | None = None
    thinking_override: ThinkingSelection | None = Field(default=None, exclude_if=lambda value: value is None)


class ResolvedCapabilityRecipe(CompositionModel):
    capability: str = Field(min_length=1, max_length=200)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    model: ResolvedModelRecipe | None = None


class ResolvedPluginRecipe(CompositionModel):
    plugin_id: str = Field(min_length=1, max_length=128)
    plugin_key: str = Field(min_length=1, max_length=200)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)


class ResolvedMcpRecipe(CompositionModel):
    server_id: str = Field(min_length=1, max_length=128)
    transport: McpTransport
    apps_enabled: bool = Field(default=False, exclude_if=lambda value: not value)


class ResolvedRunExtensionRecipe(CompositionModel):
    extension_id: str = Field(min_length=1, max_length=128)
    extension_key: str = Field(min_length=1, max_length=200)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)


class ResolvedContentPlugin(CompositionModel):
    plugin_id: str = Field(min_length=8, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    commit: str = ""
    path: str = Field(min_length=1, max_length=4096)
    skills_path: str | None = Field(default=None, min_length=1, max_length=4096)


class ResolvedEnvironmentProfile(CompositionModel):
    profile_id: str = Field(min_length=1, max_length=128)
    behavior_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_key: str = Field(min_length=1, max_length=200)
    provider_configuration: dict[str, JsonValue] = Field(default_factory=dict)
    adapter_key: str = Field(min_length=1, max_length=200)
    adapter_configuration: dict[str, JsonValue] = Field(default_factory=dict)


class ResolvedEnvironmentBinding(CompositionModel):
    selection: EnvironmentBindingSelection
    device: DeviceResource

    @model_validator(mode="after")
    def _matching_device(self) -> Self:
        if self.selection.device_id != self.device.id:
            raise ValueError("Captured binding must name its Device resource")
        return self


class ResolvedAgentNode(CompositionModel):
    source_kind: Literal["agent", "markdown", "memory"]
    source_id: str = Field(min_length=1, max_length=128)
    roster_name: str = Field(min_length=1, max_length=128)
    system_prompt: tuple[str, ...] = Field(default=(), max_length=16)
    instructions: tuple[str, ...] = Field(default=(), max_length=16)
    # None preserves legacy captures; an empty tuple explicitly clears global guidance.
    global_guidance: tuple[str, ...] | None = Field(default=None, max_length=1)
    model: ResolvedModelRecipe
    capabilities: tuple[ResolvedCapabilityRecipe, ...] = Field(default=(), max_length=128)
    harness_plugins: tuple[ResolvedPluginRecipe, ...] = Field(default=(), max_length=128)
    mcp_servers: tuple[ResolvedMcpRecipe, ...] = Field(default=(), max_length=128)
    tools: tuple[str, ...] | None = Field(default=None, max_length=256)
    tool_proxy: AgentToolProxy | None = Field(default=None, exclude_if=lambda value: value is None)
    children: tuple[ResolvedSubagent, ...] = Field(default=(), max_length=256)

    @model_validator(mode="before")
    @classmethod
    def _decode_combined_prompt(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("system_prompt") is None:
            # Historical captures froze the entire system prompt in instructions.
            # Decode it once without consulting today's release-owned prompt.
            return {**value, "system_prompt": value.get("instructions", ()), "instructions": ()}
        return value

    @model_validator(mode="after")
    def _unique_members(self) -> Self:
        names = tuple(item.name for item in self.children)
        if len(names) != len(set(names)):
            raise ValueError("resolved immediate roster names must be unique")
        for values in (
            tuple(item.capability for item in self.capabilities if item.capability != "NativeTool"),
            tuple(item.plugin_id for item in self.harness_plugins),
            tuple(item.server_id for item in self.mcp_servers),
        ):
            if len(values) != len(set(values)):
                raise ValueError("resolved Agent selections must be unique")
        return self


class ResolvedSubagent(CompositionModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4096)
    instruction: str | None = Field(default=None, min_length=1, max_length=16 * 1024)
    source_kind: Literal["agent", "markdown"]
    source_id: str = Field(min_length=1, max_length=128)
    definition: ResolvedAgentNode


class ResolvedRunComposition(CompositionModel):
    schema_version: Literal["1"] = "1"
    package_prompt_revision: str = Field(min_length=1, max_length=128)
    generation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    thread_id: str = Field(min_length=1, max_length=80)
    thread_configuration_version: int = Field(ge=1)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    project_roots: tuple[str, ...] = Field(default=(), max_length=64)
    memory_organization: bool = Field(default=False, exclude_if=lambda value: not value)
    # Missing in old captures means memory was not attached to that Run.
    memory_enabled: bool = Field(default=False, exclude_if=lambda value: not value)
    webui_sidekick: SidekickConfiguration | None = None
    role: Literal["ordinary", "coordinator", "worker"] | None = None
    coordinator_thread_id: str | None = None
    media_understanding: dict[NativeInputMediaKind, ResolvedModelRecipe] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    content_plugins: tuple[ResolvedContentPlugin, ...] = Field(default=(), max_length=256)
    root: ResolvedAgentNode
    environment_profile: ResolvedEnvironmentProfile
    environment_bindings: tuple[ResolvedEnvironmentBinding, ...] = Field(default=(), max_length=64)
    default_environment: str | None = Field(default=None, min_length=1, max_length=63)
    environment_run_extensions: tuple[ResolvedRunExtensionRecipe, ...] = Field(default=(), max_length=128)
    dependencies: tuple[DependencyProvenance, ...] = ()

    @model_validator(mode="after")
    def _valid_environment_selection(self) -> Self:
        validate_environment_selection(
            tuple(item.selection for item in self.environment_bindings),
            self.default_environment,
            local_root_count=len(self.project_roots),
        )
        return self


def _contains_tuple(annotation: object) -> bool:
    if get_origin(annotation) is tuple:
        return True
    return any(_contains_tuple(argument) for argument in get_args(annotation))


ResolvedAgentNode.model_rebuild()
ResolvedSubagent.model_rebuild()

__all__ = [
    "DependencyProvenance",
    "ResolvedAgentNode",
    "ResolvedCapabilityRecipe",
    "ResolvedContentPlugin",
    "ResolvedEnvironmentProfile",
    "ResolvedMcpRecipe",
    "ResolvedModelRecipe",
    "ResolvedPluginRecipe",
    "ResolvedRunComposition",
    "ResolvedRunExtensionRecipe",
    "ResolvedSubagent",
]
