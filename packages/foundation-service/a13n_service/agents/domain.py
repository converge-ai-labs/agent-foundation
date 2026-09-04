"""Public and durable values owned by Agent management."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from a13n_harness import HarnessModelCharacteristics
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

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import new_object_id
from a13n_service.models.domain import ModelExecutionSnapshot, ModelKey
from a13n_service.models.settings import validate_settings_bounds
from a13n_service.secrets.domain import SecretKey
from a13n_service.skills.domain import SkillKey, SkillRevisionLock

ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
BoundedKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
PluginKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$", min_length=3, max_length=128),
]
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


class PluginRuntimeMode(StrEnum):
    on_demand = "on_demand"
    runner = "runner"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentModel(StrictModel):
    model_key: ModelKey
    settings: Annotated[JsonObject, AfterValidator(validate_settings_bounds)] = Field(default_factory=dict)
    characteristics: HarnessModelCharacteristics = Field(default_factory=HarnessModelCharacteristics)


class OnDemandPluginSelection(StrictModel):
    mode: Literal[PluginRuntimeMode.on_demand] = PluginRuntimeMode.on_demand
    instance_name: BoundedKey
    plugin_version_id: ObjectId
    config: JsonObject = Field(default_factory=dict)


class RunnerPluginSelection(StrictModel):
    mode: Literal[PluginRuntimeMode.runner] = PluginRuntimeMode.runner
    instance_name: BoundedKey
    plugin_key: PluginKey
    config: JsonObject = Field(default_factory=dict)


PluginSelection = Annotated[OnDemandPluginSelection | RunnerPluginSelection, Field(discriminator="mode")]


class SkillSelection(StrictModel):
    skill_key: SkillKey
    version: int | None = Field(default=None, ge=1)


ToolKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]


def _unique_tool_keys(value: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if value is not None and len(value) != len(set(value)):
        raise ValueError("tool names must be unique")
    return value


ToolSelection = Annotated[tuple[ToolKey, ...] | None, Field(max_length=2048), AfterValidator(_unique_tool_keys)]


class ConnectorConnectionToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connector_connection_id: ObjectId
    tools: ToolSelection = None
    defer_loading: bool = False


class MCPConnectionToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mcp_connection_id: ObjectId
    tools: ToolSelection = None
    defer_loading: bool = False


class ChildEnvironmentPolicy(StrictModel):
    mode: Literal["none", "shared", "dedicated"] = "none"
    template_revision_id: ObjectId | None = None

    @model_validator(mode="after")
    def validate_recipe(self) -> ChildEnvironmentPolicy:
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


class AssetPublicationConfig(StrictModel):
    enabled: Literal[True] = True


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


class AgentConfig(StrictModel):
    model: AgentModel
    instructions: Annotated[str, StringConstraints(max_length=256 * 1024)] = ""
    input_adapter: InputAdapterConfig
    plugins: tuple[PluginSelection, ...] = Field(default=(), max_length=128)
    skills: tuple[SkillSelection, ...] = Field(default=(), max_length=512)
    connector_tools: tuple[ConnectorConnectionToolSelection, ...] = Field(default=(), max_length=128)
    mcp_tools: tuple[MCPConnectionToolSelection, ...] = Field(default=(), max_length=128)
    subagents: dict[BoundedKey, SubagentSelection] = Field(default_factory=dict, max_length=128)
    client_tools: tuple[ClientToolDefinition, ...] = Field(default=(), max_length=128)
    output_spec: OutputSpec | None = None
    retries: RetryConfig | None = None
    secret_requirements: tuple[SecretRequirement, ...] = Field(default=(), max_length=128)
    asset_publication: AssetPublicationConfig | None = None
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
            ("Connector connections", tuple(item.connector_connection_id for item in self.connector_tools)),
            ("MCP connections", tuple(item.mcp_connection_id for item in self.mcp_tools)),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        return self


class ModelOverride(StrictModel):
    model_key: ModelKey | None = None
    settings: Annotated[JsonObject, AfterValidator(validate_settings_bounds)] | None = None
    characteristics: HarnessModelCharacteristics | None = None


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
    model: ModelOverride | None = None
    instructions: Annotated[str, StringConstraints(max_length=256 * 1024)] | None = None
    plugins: tuple[PluginSelection, ...] | None = Field(default=None, max_length=128)
    skills: tuple[SkillSelection, ...] | None = Field(default=None, max_length=512)
    connector_tools: tuple[ConnectorConnectionToolSelection, ...] | None = Field(
        default=None,
        max_length=128,
    )
    mcp_tools: tuple[MCPConnectionToolSelection, ...] | None = Field(default=None, max_length=128)
    subagents: dict[BoundedKey, SubagentOverride | None] | None = Field(default=None, max_length=128)
    client_tools: tuple[ClientToolDefinition, ...] | None = Field(default=None, max_length=128)
    output_spec: OutputSpec | None = None
    retries: RetryOverride | None = None


class ResolvedAgentModel(StrictModel):
    model_id: ObjectId
    model_key: ModelKey
    settings: Annotated[JsonObject, AfterValidator(validate_settings_bounds)]
    characteristics: HarnessModelCharacteristics


class EffectiveAgentModel(StrictModel):
    execution: ModelExecutionSnapshot
    settings: Annotated[JsonObject, AfterValidator(validate_settings_bounds)]
    characteristics: HarnessModelCharacteristics


class ResolvedPluginVersion(StrictModel):
    instance_name: BoundedKey
    plugin_id: ObjectId
    plugin_version_id: ObjectId
    plugin_key: PluginKey
    distribution_name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=256)
    top_level_package: str = Field(min_length=1, max_length=256)
    wheel_digest: Sha256Digest
    config: JsonObject = Field(default_factory=dict)


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
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...] = ()
    runtime_lock_digest: Sha256Digest
    connector_tools: tuple[ConnectorConnectionToolSelection, ...] = ()
    mcp_tools: tuple[MCPConnectionToolSelection, ...] = ()
    resolved_subagents: tuple[ResolvedSubagentEdge, ...] = ()


class ResolvedRevisionContent(_ResolvedContent[ResolvedAgentModel]):
    resolved_skills: tuple[ResolvedSkillBinding, ...] = ()


class EffectiveAgentConfig(_ResolvedContent[EffectiveAgentModel]):
    schema_version: Literal["1"] = "1"
    instructions: str
    skills: tuple[SkillRevisionLock, ...] = ()
    input_adapter: InputAdapterConfig
    client_tools: tuple[ClientToolDefinition, ...] = ()
    output_spec: OutputSpec | None = None
    retries: RetryConfig | None = None
    secret_requirements: tuple[SecretRequirement, ...] = ()
    asset_publication: AssetPublicationConfig | None = None
    protocol: ProtocolConfig
    content_digest: Sha256Digest


class Agent(StrictModel):
    default_environment_template_id: ObjectId | None = None
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    source: AgentSource
    name: AgentName
    description: str | None
    version: int = Field(ge=1)
    current_revision_id: ObjectId
    enabled: bool
    archived_at: datetime | None
    duplicated_from_agent_id: ObjectId | None
    duplicated_from_revision_id: ObjectId | None
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class AgentRevision(StrictModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    agent_id: ObjectId
    version: int = Field(ge=1)
    plugin_runtime_mode: PluginRuntimeMode
    config: AgentConfig
    config_digest: Sha256Digest
    resolved_model: ResolvedAgentModel
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...]
    runtime_lock_digest: Sha256Digest
    resolved_skills: tuple[ResolvedSkillBinding, ...]
    connector_tools: tuple[ConnectorConnectionToolSelection, ...] = ()
    mcp_tools: tuple[MCPConnectionToolSelection, ...] = ()
    resolved_subagents: tuple[ResolvedSubagentEdge, ...]
    content_digest: Sha256Digest
    source_revision_id: ObjectId | None
    created_by: PrincipalRef
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
    default_environment_template_id: ObjectId | None = None
    model_config = ConfigDict(extra="forbid")

    name: AgentName
    description: AgentDescription | None = None
    config: AgentConfig


class UpdateAgentRequest(BaseModel):
    default_environment_template_id: ObjectId | None = None
    model_config = ConfigDict(extra="forbid")

    name: AgentName | None = None
    description: AgentDescription | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateAgentRequest:
        changed = self.model_fields_set.intersection({"name", "description", "default_environment_template_id"})
        if not changed:
            raise ValueError("at least one metadata field must be supplied")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class CreateAgentRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    config: AgentConfig


class RestoreAgentRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class DuplicateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: AgentName
    description: AgentDescription | None = None


class AgentRevisionCreateResult(StrictModel):
    agent: Agent
    revision: AgentRevision


def canonical_digest(value: BaseModel | JsonObject | tuple[object, ...]) -> str:
    """Return the canonical SHA-256 digest for one finite JSON value."""

    if isinstance(value, BaseModel):
        payload: object = value.model_dump(mode="json", by_alias=True)
    else:
        payload = value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()
