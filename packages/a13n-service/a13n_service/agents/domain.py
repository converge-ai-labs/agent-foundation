"""Public and durable values owned by Agent management."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from a13n_harness import HarnessModelCharacteristics
from a13n_harness.capabilities import ToolReviewConfig
from a13n_harness.tools.client import ClientToolDefinition
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_ai.usage import UsageLimits

from a13n_service.connectivity.selection_domain import (
    ConnectionRunSelection,
    ConnectionToolSelection,
)
from a13n_service.digests import Sha256Digest
from a13n_service.iam.domain import ActorRef
from a13n_service.ids import ObjectId, new_object_id
from a13n_service.labels import Labels
from a13n_service.memory.domain import MemoryConfiguration
from a13n_service.models.domain import ModelExecutionSnapshot, ModelKey
from a13n_service.models.settings import validate_settings_bounds
from a13n_service.resource_keys import ResourceKey
from a13n_service.secrets.domain import SecretKey
from a13n_service.skills.domain import SkillKey, SkillRevisionLock

from .toolsets import ToolsetOverrides, Toolsets, default_toolsets

BoundedKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
PluginKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")]
JsonObject = dict[str, JsonValue]


def new_agent_id() -> str:
    """Allocate one stable Agent identifier."""

    return new_object_id("ap")


def new_agent_revision_id() -> str:
    """Allocate one globally unique AgentRevision identifier."""

    return new_object_id("apr")


def normalize_agent_name(value: str) -> str:
    """Normalize and validate one user-visible Agent name."""

    normalized = unicodedata.normalize("NFC", value)
    if not 1 <= len(normalized) <= 128:
        raise ValueError("name must contain between 1 and 128 Unicode scalar values")
    if normalized != normalized.strip():
        raise ValueError("name must not contain leading or trailing whitespace")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in normalized):
        raise ValueError("name must not contain control or surrogate characters")
    return normalized


AgentName = Annotated[str, AfterValidator(normalize_agent_name)]
AgentDescription = Annotated[str, StringConstraints(max_length=4096)]


class AgentSource(StrEnum):
    builtin = "builtin"
    custom = "custom"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentModelCharacteristics(StrictModel):
    """Agent-owned context policy layered over Model declarations."""

    context_window_tokens: int | None = Field(default=None, gt=0)
    proactive_context_management_threshold: float | None = Field(default=0.65, ge=0.0, le=1.0)
    compact_threshold: float = Field(default=0.90, gt=0.0, le=1.0)


class AgentModel(StrictModel):
    model_key: ModelKey
    settings: Annotated[JsonObject, AfterValidator(validate_settings_bounds)] = Field(default_factory=dict)
    characteristics: AgentModelCharacteristics = Field(default_factory=AgentModelCharacteristics)


class PluginSelection(StrictModel):
    instance_name: BoundedKey
    plugin_key: PluginKey
    config: JsonObject = Field(default_factory=dict)


class SkillSelection(StrictModel):
    skill_key: SkillKey
    version: int | None = Field(default=None, ge=1)


class ChildEnvironmentPolicy(StrictModel):
    mode: Literal["none", "shared", "dedicated"] = "shared"
    template_revision_id: ObjectId | None = None

    @model_validator(mode="after")
    def validate_template_config(self) -> ChildEnvironmentPolicy:
        if (self.mode == "dedicated") != (self.template_revision_id is not None):
            raise ValueError("only dedicated children require a template revision")
        return self


class DelegationContextPolicy(StrictModel):
    include_task: bool = True
    history: Literal["none", "summary", "selected"] = "none"
    task_state: Literal["shared", "isolated"] = "shared"


class SubagentSelection(StrictModel):
    agent_id: ObjectId
    version: int | None = Field(default=None, ge=1)
    description: Annotated[str, StringConstraints(max_length=4096)] | None = None
    context: DelegationContextPolicy = Field(default_factory=DelegationContextPolicy)
    usage_limits: UsageLimits | None = None
    environment: ChildEnvironmentPolicy = Field(default_factory=ChildEnvironmentPolicy)


class OutputVariant(StrictModel):
    name: BoundedKey
    description: Annotated[str, StringConstraints(max_length=4096)] | None = None
    schema_: JsonObject = Field(alias="schema")
    resources: dict[BoundedKey, JsonObject] = Field(default_factory=dict, max_length=128)


class OutputSpec(StrictModel):
    name: BoundedKey | None = None
    description: Annotated[str, StringConstraints(max_length=4096)] | None = None
    schema_: JsonObject | None = Field(default=None, alias="schema")
    resources: dict[BoundedKey, JsonObject] = Field(default_factory=dict, max_length=128)
    variants: tuple[OutputVariant, ...] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_shape(self) -> OutputSpec:
        if self.variants is None:
            if self.schema_ is None and self.resources:
                raise ValueError("resources require a top-level schema")
            return self
        if self.schema_ is not None or self.resources:
            raise ValueError("variants cannot be combined with a top-level schema or resources")
        if len(self.variants) < 2:
            raise ValueError("variants must contain at least two entries")
        names = tuple(item.name for item in self.variants)
        if len(names) != len(set(names)):
            raise ValueError("variant names must be unique")
        return self


class RetryConfig(StrictModel):
    tools: int = Field(default=0, ge=0, le=100)
    output: int = Field(default=0, ge=0, le=100)


class InputAdapterConfig(StrictModel):
    adapter_key: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")]
    config: JsonObject = Field(default_factory=dict)


class SecretRequirement(StrictModel):
    key: SecretKey
    description: Annotated[str, StringConstraints(max_length=2048)] | None = None
    required: bool = True


class ClientToolPolicy(StrictModel):
    name: BoundedKey
    required: bool = False


class A2ASkillProjection(StrictModel):
    id: BoundedKey
    name: AgentName
    description: Annotated[str, StringConstraints(max_length=4096)] | None = None


class ExtendedAgentCardPolicy(StrictModel):
    enabled: bool = False


class ProtocolLimits(StrictModel):
    max_input_bytes: int = Field(default=1024 * 1024, ge=1, le=64 * 1024 * 1024)
    max_output_bytes: int = Field(default=16 * 1024 * 1024, ge=1, le=256 * 1024 * 1024)
    max_event_bytes: int = Field(default=1024 * 1024, ge=1, le=16 * 1024 * 1024)


class ProtocolConfig(StrictModel):
    schema_version: Literal["1"] = "1"
    public_name: AgentName
    public_description: Annotated[str, StringConstraints(max_length=4096)] | None = None
    output_modes: tuple[Annotated[str, StringConstraints(min_length=1, max_length=128)], ...] = ("text",)
    input_data_schema: JsonObject | None = None
    state_schema: JsonObject | None = None
    context_schema: JsonObject | None = None
    client_tools: tuple[ClientToolPolicy, ...] = Field(default=(), max_length=128)
    event_visibility: tuple[BoundedKey, ...] = Field(default=(), max_length=128)
    a2a_skills: tuple[A2ASkillProjection, ...] = Field(default=(), max_length=128)
    extended_agent_card: ExtendedAgentCardPolicy | None = None
    limits: ProtocolLimits = Field(default_factory=ProtocolLimits)

    @field_validator("output_modes", "event_visibility")
    @classmethod
    def validate_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value


class AgentReviewer(ToolReviewConfig):
    """Reviewer selected by immutable managed Model ID, never a provider route."""

    model: ObjectId
    model_settings: Annotated[JsonObject, AfterValidator(validate_settings_bounds)] | None = None


class AgentConfig(StrictModel):
    default_environment_template_id: ObjectId | None = None
    toolsets: Toolsets = Field(default_factory=default_toolsets)
    memory: MemoryConfiguration | None = Field(default=None, exclude_if=lambda value: value is None)
    reviewer: AgentReviewer | None = Field(default=None, exclude_if=lambda value: value is None)
    subagent_mode: Literal["inline", "async"] = "inline"
    model: AgentModel
    instructions: Annotated[str, StringConstraints(max_length=256 * 1024)] = ""
    input_adapter: InputAdapterConfig
    plugins: tuple[PluginSelection, ...] = Field(default=(), max_length=128)
    skills: tuple[SkillSelection, ...] = Field(default=(), max_length=512)
    connection_tools: tuple[ConnectionToolSelection, ...] = Field(default=(), max_length=128)
    subagents: dict[BoundedKey, SubagentSelection] = Field(default_factory=dict, max_length=128)
    client_tools: tuple[ClientToolDefinition, ...] = Field(default=(), max_length=128)
    output_spec: OutputSpec | None = None
    retries: RetryConfig | None = None
    secret_requirements: tuple[SecretRequirement, ...] = Field(default=(), max_length=128)
    protocol: ProtocolConfig

    @model_validator(mode="after")
    def validate_unique_selections(self) -> AgentConfig:
        plugin_names = tuple(item.instance_name for item in self.plugins)
        skill_keys = tuple(item.skill_key for item in self.skills)
        client_tool_names = tuple(item.name for item in self.client_tools)
        secret_keys = tuple(item.key for item in self.secret_requirements)
        for label, values in (
            ("plugin instance names", plugin_names),
            ("Skill keys", skill_keys),
            ("client tool names", client_tool_names),
            ("Secret requirement keys", secret_keys),
            ("Connector connections", tuple(item.connection_id for item in self.connection_tools)),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        return self


class ModelOverride(StrictModel):
    model_key: ModelKey | None = None
    settings: Annotated[JsonObject, AfterValidator(validate_settings_bounds)] | None = None
    characteristics: AgentModelCharacteristics | None = None


class SubagentOverride(StrictModel):
    agent_id: ObjectId | None = None
    version: int | None = Field(default=None, ge=1)
    description: Annotated[str, StringConstraints(max_length=4096)] | None = None
    context: DelegationContextPolicy | None = None
    usage_limits: UsageLimits | None = None
    environment: ChildEnvironmentPolicy | None = None


class RetryOverride(StrictModel):
    tools: int | None = Field(default=None, ge=0, le=100)
    output: int | None = Field(default=None, ge=0, le=100)


class AgentRunOverride(StrictModel):
    toolsets: ToolsetOverrides | None = None
    memory: MemoryConfiguration | None = None
    reviewer: AgentReviewer | None = None
    model: ModelOverride | None = None
    instructions: Annotated[str, StringConstraints(max_length=256 * 1024)] | None = None
    plugins: tuple[PluginSelection, ...] | None = Field(default=None, max_length=128)
    skills: tuple[SkillSelection, ...] | None = Field(default=None, max_length=512)
    connection_tools: tuple[ConnectionToolSelection, ...] | None = Field(
        default=None,
        max_length=128,
    )
    subagents: dict[BoundedKey, SubagentOverride | None] | None = Field(default=None, max_length=128)
    client_tools: tuple[ClientToolDefinition, ...] | None = Field(default=None, max_length=128)
    output_spec: OutputSpec | None = None
    retries: RetryOverride | None = None


class ResolvedAgentModel(StrictModel):
    model_id: ObjectId
    model_key: ModelKey
    settings: Annotated[JsonObject, AfterValidator(validate_settings_bounds)]
    characteristics: AgentModelCharacteristics


class EffectiveAgentModel(StrictModel):
    execution: ModelExecutionSnapshot
    settings: Annotated[JsonObject, AfterValidator(validate_settings_bounds)]
    characteristics: HarnessModelCharacteristics


class ResolvedSkillBinding(StrictModel):
    skill_id: ObjectId
    skill_key: SkillKey
    version: int | None = Field(default=None, ge=1)


class ResolvedSubagentEdge(StrictModel):
    name: BoundedKey
    child_agent_id: ObjectId
    child_agent_revision_id: ObjectId
    description: str | None = Field(default=None, max_length=4096)
    context: DelegationContextPolicy
    usage_limits: UsageLimits | None = None
    environment: ChildEnvironmentPolicy


class _ResolvedContent[ResolvedModelT: BaseModel](StrictModel):
    resolved_model: ResolvedModelT
    connection_tools: tuple[ConnectionToolSelection, ...] = ()
    resolved_subagents: tuple[ResolvedSubagentEdge, ...] = ()


class ResolvedRevisionContent(_ResolvedContent[ResolvedAgentModel]):
    resolved_skills: tuple[ResolvedSkillBinding, ...] = ()


class ChildAgentExecution(StrictModel):
    agent_id: ObjectId
    revision_content_digest: Sha256Digest
    effective_config: EffectiveAgentConfig
    connection_selections: tuple[ConnectionRunSelection, ...] = ()


class EffectiveAgentConfig(_ResolvedContent[EffectiveAgentModel]):
    resolved_reviewer_model: EffectiveAgentModel | None = Field(default=None, exclude_if=lambda value: value is None)
    toolsets: Toolsets = Field(default_factory=default_toolsets)
    memory: MemoryConfiguration | None = Field(default=None, exclude_if=lambda value: value is None)
    reviewer: AgentReviewer | None = Field(default=None, exclude_if=lambda value: value is None)
    plugins: tuple[PluginSelection, ...] = Field(default=(), max_length=128)
    subagent_mode: Literal["inline", "async"] = "inline"
    child_configs: dict[ObjectId, ChildAgentExecution] = Field(default_factory=dict, max_length=128)
    schema_version: Literal["1"] = "1"
    instructions: str
    skills: tuple[SkillRevisionLock, ...] = ()
    input_adapter: InputAdapterConfig
    client_tools: tuple[ClientToolDefinition, ...] = ()
    output_spec: OutputSpec | None = None
    retries: RetryConfig | None = None
    secret_requirements: tuple[SecretRequirement, ...] = ()
    protocol: ProtocolConfig
    content_digest: Sha256Digest


ChildAgentExecution.model_rebuild()


class PreparedAgentPlugins(StrictModel):
    plugins: tuple[PluginSelection, ...] = Field(max_length=128)
    children: dict[ObjectId, PreparedAgentPlugins] = Field(default_factory=dict, max_length=128)

    def validate_for(self, config: EffectiveAgentConfig) -> None:
        """Require the complete accepted graph and ordered plugin identities."""

        if self.children.keys() != config.child_configs.keys() or tuple(
            (item.instance_name, item.plugin_key) for item in self.plugins
        ) != tuple((item.instance_name, item.plugin_key) for item in config.plugins):
            raise ValueError("Prepared plugins do not match the accepted Agent graph")
        for revision_id, child in config.child_configs.items():
            self.children[revision_id].validate_for(child.effective_config)


class Agent(StrictModel):
    system_purpose: Literal["configuration_assistant"] | None = None
    image_url: str | None = None
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    source: AgentSource
    name: AgentName
    key: ResourceKey
    description: str | None
    labels: Labels = Field(default_factory=dict)
    default_revision_id: ObjectId | None
    enabled: bool
    archived_at: datetime | None
    duplicated_from_agent_id: ObjectId | None
    duplicated_from_revision_id: ObjectId | None
    created_by: ActorRef
    updated_by: ActorRef
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def revision_source_is_coherent(self) -> Agent:
        if self.system_purpose == "configuration_assistant":
            if self.source is not AgentSource.builtin or self.default_revision_id is not None:
                raise ValueError("The configuration assistant has one stable identity without Revisions")
        elif self.default_revision_id is None:
            raise ValueError("Ordinary Agents require a default Revision")
        return self


class AgentRevision(StrictModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    agent_id: ObjectId
    version: int = Field(ge=1)
    config: AgentConfig
    config_digest: Sha256Digest
    resolved_model: ResolvedAgentModel
    resolved_skills: tuple[ResolvedSkillBinding, ...]
    connection_tools: tuple[ConnectionToolSelection, ...] = ()
    resolved_subagents: tuple[ResolvedSubagentEdge, ...]
    content_digest: Sha256Digest
    source_revision_id: ObjectId | None
    change_summary: Annotated[str, StringConstraints(max_length=2048)] | None = None
    created_by: ActorRef
    created_at: datetime


class AgentCollection(StrictModel):
    items: tuple[Agent, ...]
    next_cursor: str | None


class AgentRevisionCollection(StrictModel):
    items: tuple[AgentRevision, ...]
    next_cursor: str | None


class BuiltinAgentRegistration(StrictModel):
    """One distribution-owned Agent definition resolved for a specific Workspace."""

    agent_id: ObjectId
    system_actor_id: ObjectId
    name: AgentName
    description: AgentDescription | None = None
    config: AgentConfig


class CreateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: AgentName
    key: ResourceKey | None = None
    description: AgentDescription | None = None
    labels: Labels = Field(default_factory=dict)
    config: AgentConfig


class UpdateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: AgentName | None = None
    key: ResourceKey | None = None
    description: AgentDescription | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateAgentRequest:
        changed = self.model_fields_set.intersection({"name", "key", "description"})
        if not changed:
            raise ValueError("at least one metadata field must be supplied")
        for field in ("name", "key"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class CreateAgentRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: AgentConfig
    change_summary: Annotated[str, StringConstraints(max_length=2048)] | None = None


class SetDefaultAgentRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DuplicateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: AgentName
    key: ResourceKey | None = None
    description: AgentDescription | None = None
    labels: Labels = Field(default_factory=dict)


class AgentRevisionCreateResult(StrictModel):
    agent: Agent
    revision: AgentRevision
