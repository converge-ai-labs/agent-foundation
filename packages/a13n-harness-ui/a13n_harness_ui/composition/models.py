"""Immutable recipes captured before Host credential resolution for one UI Run."""

from __future__ import annotations

from typing import ClassVar, Literal, Self, get_args, get_origin

from a13n_harness import RunConfiguration, ToolExecutionConfig
from a13n_harness.toolsets.file_media import NativeInputMediaKind
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from a13n_harness_ui.configuration import McpTransport, ModelAuthentication
from a13n_harness_ui.configuration.models import (
    AcpAgentResource,
    AgentCreationConfiguration,
    AgentToolSearch,
    BackgroundCompactionConfiguration,
    DeviceResource,
    ModelCharacteristics,
    SubagentsConfiguration,
    SubscriptionGroupResource,
    WebProviderResource,
    lift_legacy_agent_creation,
    mcp_roots_enabled,
)
from a13n_harness_ui.connectors.models import Capture as ConnectorCapture
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection, validate_environment_selection
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.mcp_oauth.models import OAuthBinding
from a13n_harness_ui.model_thinking import ThinkingSelection
from a13n_harness_ui.notification_origin import BrowserId
from a13n_harness_ui.surfaces import RunModelOverrides


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
        "model_provider",
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
    base_model: str | None = Field(
        default=None, pattern=r"^[^\s:]+:[^\s:]+$", max_length=512, exclude_if=lambda v: v is None
    )
    authentication: ModelAuthentication
    model_provider: DependencyProvenance | None = Field(default=None, exclude_if=lambda value: value is None)
    subscription_group: SubscriptionGroupResource | None = Field(default=None, exclude_if=lambda value: value is None)
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    model_configuration: dict[str, JsonValue] = Field(default_factory=dict)
    model_characteristics: ModelCharacteristics | None = None
    thinking_override: ThinkingSelection | None = Field(default=None, exclude_if=lambda value: value is None)


class ResolvedCapabilityRecipe(CompositionModel):
    capability: str = Field(min_length=1, max_length=200)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    model: ResolvedModelRecipe | None = None
    guardian_credits: bool = Field(default=False, exclude_if=lambda value: not value)


class ResolvedPluginRecipe(CompositionModel):
    plugin_id: str = Field(min_length=1, max_length=128)
    plugin_key: str = Field(min_length=1, max_length=200)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)


class ResolvedMcpRecipe(CompositionModel):
    server_id: str = Field(min_length=1, max_length=128)
    transport: McpTransport
    expose_roots: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    object_ref_tools: dict[str, bool] = Field(default_factory=dict, exclude_if=lambda value: not value)
    on_error: Literal["skip", "raise"] = Field(default="skip", exclude_if=lambda value: value == "skip")
    oauth_binding: OAuthBinding | None = Field(default=None, exclude_if=lambda value: value is None)
    tools: tuple[str, ...] | None = Field(default=None, exclude_if=lambda value: value is None)
    apps_enabled: bool = Field(default=False, exclude_if=lambda value: not value)
    # Historical recipes were all generically selected, including Apps-enabled ones.
    generic_selected: bool = Field(default=True, exclude_if=lambda value: value)
    host_owned: bool = Field(default=False, exclude_if=lambda value: not value)
    protocol: Literal["auto", "legacy", "2026-07-28"] = Field(default="auto", exclude_if=lambda value: value == "auto")

    @property
    def roots_enabled(self) -> bool:
        return mcp_roots_enabled(self.transport, self.expose_roots)

    def binding_key(self, *, apps: bool | None = None) -> str:
        base = (
            self.transport.model_dump_json(),
            self.protocol,
            self.apps_enabled if apps is None else apps,
            self.roots_enabled,
        )
        return repr(base if self.oauth_binding is None else (*base, self.oauth_binding.model_dump_json()))


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


class ResolvedEnvironmentTarget(CompositionModel):
    """One Bot-accessible Project recipe, captured without opening its execution."""

    target_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1)
    project_id: str | None = None
    project_roots: tuple[str, ...] = Field(default=(), max_length=64)
    environment_profile: ResolvedEnvironmentProfile
    environment_bindings: tuple[ResolvedEnvironmentBinding, ...] = Field(default=(), max_length=64)
    default_environment: str | None = None
    session_versions: dict[str, int] = Field(default_factory=dict)
    unavailable: str | None = None


class ResolvedAgentNode(CompositionModel):
    source_kind: Literal["agent", "markdown", "memory", "live", "bot", "dynamic"]
    source_id: str = Field(min_length=1, max_length=128)
    roster_name: str = Field(min_length=1, max_length=128)
    # Bot retains its built-in identity while recording its execution-policy source.
    base_agent_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    system_prompt: tuple[str, ...] = Field(default=(), max_length=16)
    instructions: tuple[str, ...] = Field(default=(), max_length=16)
    # None preserves legacy captures; an empty tuple explicitly clears global guidance.
    global_guidance: tuple[str, ...] | None = Field(default=None, max_length=1)
    model: ResolvedModelRecipe
    capabilities: tuple[ResolvedCapabilityRecipe, ...] = Field(default=(), max_length=128)
    harness_plugins: tuple[ResolvedPluginRecipe, ...] = Field(default=(), max_length=128)
    # Up to 128 generic servers plus 128 independently selected WebUI Apps.
    mcp_servers: tuple[ResolvedMcpRecipe, ...] = Field(default=(), max_length=256)
    tools: tuple[str, ...] | None = Field(default=None, max_length=256)
    tool_search: AgentToolSearch | None = Field(default=None, exclude_if=lambda value: value is None)
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


class ResolvedAcpNode(CompositionModel):
    source_kind: Literal["acp_agent"] = "acp_agent"
    source_id: str
    roster_name: str
    resource: AcpAgentResource
    global_guidance: tuple[str, ...] = ()
    mcp_servers: tuple[ResolvedMcpRecipe, ...] = Field(default=(), max_length=256)
    shell_review: ResolvedCapabilityRecipe | None = Field(default=None, exclude_if=lambda value: value is None)
    model: ClassVar[None] = None
    children: ClassVar[tuple[()]] = ()
    capabilities: ClassVar[tuple[()]] = ()
    harness_plugins: ClassVar[tuple[()]] = ()
    tools: ClassVar[None] = None
    tool_search: ClassVar[None] = None
    base_agent_id: ClassVar[None] = None


class ResolvedRunComposition(CompositionModel):
    origin_browser_id: BrowserId | None = Field(default=None, exclude_if=lambda value: value is None)
    schema_version: Literal["1"] = "1"
    web_providers: tuple[WebProviderResource, ...] = Field(default=(), exclude_if=lambda value: not value)
    package_prompt_revision: str = Field(min_length=1, max_length=128)
    generation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    thread_id: str = Field(min_length=1, max_length=80)
    thread_configuration_version: int = Field(ge=1)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    project_roots: tuple[str, ...] = Field(default=(), max_length=64)
    memory_organization: bool = Field(default=False, exclude_if=lambda value: not value)
    background_compaction: BackgroundCompactionConfiguration = Field(
        default_factory=lambda: BackgroundCompactionConfiguration(enabled=False),
        exclude_if=lambda value: value == BackgroundCompactionConfiguration(enabled=False),
    )
    # Missing in old captures means memory was not attached to that Run.
    memory_enabled: bool = Field(default=False, exclude_if=lambda value: not value)
    # Missing in old captures keeps the Inbox tool those Runs already had.
    inbox_enabled: bool = Field(default=True, exclude_if=lambda value: value)
    # Missing in old captures keeps root creation tools when an Agent or creation Model exists.
    can_create_threads: bool = Field(default=True, exclude_if=lambda value: value)
    run_configuration: RunConfiguration = Field(
        default_factory=RunConfiguration, exclude_if=lambda value: value == RunConfiguration()
    )
    tool_execution: ToolExecutionConfig = Field(default_factory=ToolExecutionConfig)
    agent_creation: AgentCreationConfiguration = Field(
        default_factory=AgentCreationConfiguration,
        exclude_if=lambda value: value == AgentCreationConfiguration(),
    )
    subagents: SubagentsConfiguration = Field(default_factory=SubagentsConfiguration)

    role: Literal["ordinary", "coordinator", "worker"] | None = None
    coordinator_thread_id: str | None = None
    # The explicit Model selection that a later admission may inherit. The effective Model lives on root.
    model_overrides: RunModelOverrides | None = Field(default=None, exclude_if=lambda value: value is None)
    media_understanding: dict[NativeInputMediaKind, ResolvedModelRecipe] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    content_plugins: tuple[ResolvedContentPlugin, ...] = Field(default=(), max_length=256)
    connectors: tuple[ConnectorCapture, ...] = Field(default=(), exclude_if=lambda value: not value)
    root: ResolvedAgentNode | ResolvedAcpNode
    environment_profile: ResolvedEnvironmentProfile
    # Local Envd admission adds Thread grants to environment_profile. Retain the input for new children.
    base_environment_profile: ResolvedEnvironmentProfile | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    environment_bindings: tuple[ResolvedEnvironmentBinding, ...] = Field(default=(), max_length=64)
    default_environment: str | None = Field(default=None, min_length=1, max_length=63)
    session_versions: dict[str, int] = Field(default_factory=dict)
    # Bot target Session selections are separate from its native workspace.
    environment_scope: str | None = Field(default=None, exclude_if=lambda value: value is None)
    environment_targets: tuple[ResolvedEnvironmentTarget, ...] = Field(default=(), exclude_if=lambda value: not value)
    environment_run_extensions: tuple[ResolvedRunExtensionRecipe, ...] = Field(default=(), max_length=128)
    dependencies: tuple[DependencyProvenance, ...] = ()

    def require_native_root(self) -> ResolvedAgentNode:
        if isinstance(self.root, ResolvedAcpNode):
            raise CompositionError("This operation requires a native Agent.", code="native_agent_required")
        return self.root

    def agent_nodes(self) -> tuple[ResolvedAgentNode, ...]:
        values: list[ResolvedAgentNode] = []
        pending = [self.root] if isinstance(self.root, ResolvedAgentNode) else []
        while pending:
            node = pending.pop()
            values.append(node)
            pending.extend(reversed(tuple(item.definition for item in node.children)))
        return tuple(values)

    # Historical Run snapshots can still carry retired preferences or subagents.dynamic model policy.
    @model_validator(mode="before")
    @classmethod
    def _read_previous_capture(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = (
            {key: item for key, item in value.items() if key != "webui_sidekick"}
            if "webui_sidekick" in value
            else value
        )
        return lift_legacy_agent_creation(normalized)

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
