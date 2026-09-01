"""Public and durable values owned by Agent Preset management."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from a13n_environment_provider import EnvironmentProviderSpec
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
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import new_object_id
from a13n_service.model_configs.domain import ModelExecutionSnapshot
from a13n_service.secrets.domain import SecretCredentialSource, SecretKey

ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
BoundedKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
PluginKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$", min_length=3, max_length=128),
]
JsonObject = dict[str, JsonValue]
_MODEL_SETTING_KEYS = frozenset(ModelSettings.__annotations__)


def _validate_model_settings(value: JsonObject) -> JsonObject:
    unknown = sorted(set(value) - _MODEL_SETTING_KEYS)
    if unknown:
        raise ValueError(f"unsupported ModelSettings fields: {', '.join(unknown)}")
    return value


def new_agent_preset_id() -> str:
    """Allocate one stable AgentPreset identifier."""

    return new_object_id("ap")


def new_agent_preset_revision_id() -> str:
    """Allocate one globally unique AgentPresetRevision identifier."""

    return new_object_id("apr")


def normalize_preset_name(value: str) -> str:
    """Normalize and validate one user-visible Preset name."""

    normalized = unicodedata.normalize("NFC", value)
    if not 1 <= len(normalized) <= 128:
        raise ValueError("name must contain between 1 and 128 Unicode scalar values")
    if normalized != normalized.strip():
        raise ValueError("name must not contain leading or trailing whitespace")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in normalized):
        raise ValueError("name must not contain control or surrogate characters")
    return normalized


PresetName = Annotated[str, AfterValidator(normalize_preset_name)]
PresetDescription = Annotated[str, StringConstraints(max_length=4096)]


class AgentPresetSource(StrEnum):
    builtin = "builtin"
    custom = "custom"


class AgentPresetLifecycleState(StrEnum):
    enabled = "enabled"
    disabled = "disabled"
    archived = "archived"


class PluginRuntimeMode(StrEnum):
    on_demand = "on_demand"
    runner = "runner"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentModelConfig(StrictModel):
    model_config_id: ObjectId
    settings: JsonObject = Field(default_factory=dict)
    characteristics: HarnessModelCharacteristics = Field(default_factory=HarnessModelCharacteristics)

    _settings_are_native = field_validator("settings")(_validate_model_settings)


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
    skill_revision_id: ObjectId


class ConnectorSelection(StrictModel):
    connector_revision_id: ObjectId
    connection_id: ObjectId | None = None
    tools: tuple[BoundedKey, ...] | None = Field(default=None, max_length=256)

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and (not value or len(value) != len(set(value))):
            raise ValueError("tools must be a non-empty unique tuple or null")
        return value


class EnvironmentSelection(StrictModel):
    environment_revision_id: ObjectId


class ChildEnvironmentPolicy(StrictModel):
    mode: Literal["none", "shared_root", "dedicated"] = "none"


class DelegationContextPolicy(StrictModel):
    include_task: bool = True
    history: Literal["none", "summary", "selected"] = "none"
    task_state: Literal["shared", "isolated"] = "shared"


class SubagentSelection(StrictModel):
    agent_preset_id: ObjectId
    revision: int | None = Field(default=None, ge=1)
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
    name: PresetName
    description: Annotated[str, StringConstraints(max_length=4096)] | None = None


class ExtendedAgentCardPolicy(StrictModel):
    enabled: bool = False


class ProtocolLimits(StrictModel):
    max_input_bytes: int = Field(default=1024 * 1024, ge=1, le=64 * 1024 * 1024)
    max_output_bytes: int = Field(default=16 * 1024 * 1024, ge=1, le=256 * 1024 * 1024)
    max_event_bytes: int = Field(default=1024 * 1024, ge=1, le=16 * 1024 * 1024)


class ProtocolConfig(StrictModel):
    schema_version: Literal["1"] = "1"
    public_name: PresetName
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


class AgentPresetConfig(StrictModel):
    model: AgentModelConfig
    instructions: Annotated[str, StringConstraints(max_length=256 * 1024)] = ""
    input_adapter: InputAdapterConfig
    plugins: tuple[PluginSelection, ...] = Field(default=(), max_length=128)
    skills: tuple[SkillSelection, ...] = Field(default=(), max_length=512)
    connectors: dict[BoundedKey, ConnectorSelection] = Field(default_factory=dict, max_length=128)
    environment: EnvironmentSelection | None = None
    subagents: dict[BoundedKey, SubagentSelection] = Field(default_factory=dict, max_length=128)
    client_tools: tuple[ClientToolDefinition, ...] = Field(default=(), max_length=128)
    output_spec: OutputSpec | None = None
    retries: RetryConfig | None = None
    secret_requirements: tuple[SecretRequirement, ...] = Field(default=(), max_length=128)
    asset_publication: AssetPublicationConfig | None = None
    protocol: ProtocolConfig

    @model_validator(mode="after")
    def validate_unique_selections(self) -> AgentPresetConfig:
        plugin_names = tuple(item.instance_name for item in self.plugins)
        skill_ids = tuple(item.skill_revision_id for item in self.skills)
        client_tool_names = tuple(item.name for item in self.client_tools)
        secret_keys = tuple(item.key for item in self.secret_requirements)
        for label, values in (
            ("plugin instance names", plugin_names),
            ("Skill revisions", skill_ids),
            ("client tool names", client_tool_names),
            ("Secret requirement keys", secret_keys),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        return self


class EnvironmentCredentialBinding(StrictModel):
    requirement_key: BoundedKey
    credential: SecretCredentialSource


class InlineEnvironmentSelection(StrictModel):
    provider: EnvironmentProviderSpec
    credential_bindings: tuple[EnvironmentCredentialBinding, ...] = Field(default=(), max_length=64)
    access: Literal["read_only", "read_write", "full"] = "full"


EnvironmentOverride = EnvironmentSelection | InlineEnvironmentSelection


class ModelOverride(StrictModel):
    model_config_id: ObjectId | None = None
    settings: JsonObject | None = None
    characteristics: HarnessModelCharacteristics | None = None

    @field_validator("settings")
    @classmethod
    def validate_settings(cls, value: JsonObject | None) -> JsonObject | None:
        return None if value is None else _validate_model_settings(value)


class ConnectorOverride(StrictModel):
    connector_revision_id: ObjectId | None = None
    connection_id: ObjectId | None = None
    tools: tuple[BoundedKey, ...] | None = Field(default=None, max_length=256)
    headers: dict[str, str] | None = Field(default=None, max_length=64, repr=False)


class SubagentOverride(StrictModel):
    agent_preset_id: ObjectId | None = None
    revision: int | None = Field(default=None, ge=1)
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
    connectors: dict[BoundedKey, ConnectorOverride | None] | None = Field(default=None, max_length=128)
    environment: EnvironmentOverride | None = None
    subagents: dict[BoundedKey, SubagentOverride | None] | None = Field(default=None, max_length=128)
    client_tools: tuple[ClientToolDefinition, ...] | None = Field(default=None, max_length=128)
    output_spec: OutputSpec | None = None
    retries: RetryOverride | None = None


class ResolvedAgentModelConfig(StrictModel):
    execution: ModelExecutionSnapshot
    settings: JsonObject
    characteristics: HarnessModelCharacteristics

    _settings_are_native = field_validator("settings")(_validate_model_settings)


class ResolvedPluginVersion(StrictModel):
    instance_name: BoundedKey
    plugin_id: ObjectId
    plugin_version_id: ObjectId
    plugin_key: PluginKey
    distribution_name: str = Field(min_length=1, max_length=256)
    distribution_version: str = Field(min_length=1, max_length=256)
    top_level_package: str = Field(min_length=1, max_length=256)
    wheel_digest: Sha256Digest
    config: JsonObject = Field(default_factory=dict)


class ResolvedSkillSelection(StrictModel):
    skill_revision_id: ObjectId
    skill_name: str = Field(min_length=1, max_length=256)
    content_digest: Sha256Digest


class FrozenConnectorTool(StrictModel):
    name: BoundedKey
    tool_id: str = Field(min_length=1, max_length=256)
    description: str = Field(max_length=16 * 1024)
    parameters_json_schema: JsonObject
    effects: tuple[str, ...] = Field(default=(), max_length=64)
    credential_audiences: tuple[str, ...] = Field(default=(), max_length=64)
    idempotency: str = Field(default="none", min_length=1, max_length=64)
    output_policy: JsonObject = Field(default_factory=dict)


class ConnectorProviderContractLock(StrictModel):
    provider_key: str = Field(min_length=1, max_length=200)
    contract_version: str = Field(min_length=1, max_length=200)


class ResolvedConnectorSelection(StrictModel):
    name: BoundedKey
    connector_revision_id: ObjectId
    connection_id: ObjectId | None = None
    tools: tuple[FrozenConnectorTool, ...] = Field(min_length=1, max_length=256)
    provider_lock: ConnectorProviderContractLock
    sensitive_binding_keys: tuple[str, ...] = Field(default=(), max_length=64)


class EnvironmentExecutionConfig(StrictModel):
    schema_version: Literal["1"] = "1"
    source_environment_revision_id: ObjectId | None = None
    provider: EnvironmentProviderSpec
    provider_package_revision_id: ObjectId | None = None
    provider_lock: JsonObject
    credential_bindings: tuple[EnvironmentCredentialBinding, ...] = Field(default=(), max_length=64)
    access: Literal["read_only", "read_write", "full"]
    logical_digest_sha256: Sha256Digest


class ResolvedSubagentEdge(StrictModel):
    name: BoundedKey
    child_agent_preset_id: ObjectId
    child_agent_preset_revision_id: ObjectId
    description: str | None = Field(default=None, max_length=4096)
    context: DelegationContextPolicy
    usage_limits: UsageLimits | None = None
    environment: ChildEnvironmentPolicy


class ResolvedRevisionContent(StrictModel):
    resolved_model: ResolvedAgentModelConfig
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...] = ()
    runtime_lock_digest: Sha256Digest
    resolved_skills: tuple[ResolvedSkillSelection, ...] = ()
    resolved_connectors: tuple[ResolvedConnectorSelection, ...] = ()
    resolved_environment: EnvironmentExecutionConfig | None = None
    resolved_subagents: tuple[ResolvedSubagentEdge, ...] = ()


class EffectiveAgentConfig(ResolvedRevisionContent):
    schema_version: Literal["1"] = "1"
    instructions: str
    input_adapter: InputAdapterConfig
    client_tools: tuple[ClientToolDefinition, ...] = ()
    output_spec: OutputSpec | None = None
    retries: RetryConfig | None = None
    secret_requirements: tuple[SecretRequirement, ...] = ()
    asset_publication: AssetPublicationConfig | None = None
    protocol: ProtocolConfig
    content_digest: Sha256Digest


class AgentPreset(StrictModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    source: AgentPresetSource
    name: PresetName
    description: str | None
    lifecycle_state: AgentPresetLifecycleState
    resource_version: int = Field(ge=1)
    config: AgentPresetConfig
    active_revision_id: ObjectId | None
    config_base_revision_id: ObjectId | None
    duplicated_from_preset_id: ObjectId | None
    duplicated_from_revision_id: ObjectId | None
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
    has_unpublished_changes: bool


class AgentPresetRevision(StrictModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    agent_preset_id: ObjectId
    revision_number: int = Field(ge=1)
    plugin_runtime_mode: PluginRuntimeMode
    config: AgentPresetConfig
    resolved_model: ResolvedAgentModelConfig
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...]
    runtime_lock_digest: Sha256Digest
    resolved_skills: tuple[ResolvedSkillSelection, ...]
    resolved_connectors: tuple[ResolvedConnectorSelection, ...]
    resolved_environment: EnvironmentExecutionConfig | None
    resolved_subagents: tuple[ResolvedSubagentEdge, ...]
    content_digest: Sha256Digest
    source_revision_id: ObjectId | None
    created_by: PrincipalRef
    created_at: datetime


class AgentPresetCollection(StrictModel):
    items: tuple[AgentPreset, ...]
    next_cursor: str | None


class AgentPresetRevisionCollection(StrictModel):
    items: tuple[AgentPresetRevision, ...]
    next_cursor: str | None


class CreateAgentPresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: PresetName
    description: PresetDescription | None = None
    config: AgentPresetConfig


class PatchAgentPresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_resource_version: int = Field(ge=1)
    name: PresetName | None = None
    description: PresetDescription | None = None

    @model_validator(mode="after")
    def validate_change(self) -> PatchAgentPresetRequest:
        changed = self.model_fields_set.intersection({"name", "description"})
        if not changed:
            raise ValueError("at least one metadata field must be supplied")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class ReplaceAgentPresetConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_resource_version: int = Field(ge=1)
    config: AgentPresetConfig


class AgentPresetCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_resource_version: int = Field(ge=1)


class RollbackAgentPresetRequest(AgentPresetCommandRequest):
    source_revision_id: ObjectId


class DuplicateAgentPresetRequest(AgentPresetCommandRequest):
    name: PresetName
    description: PresetDescription | None = None


class AgentPresetPublishResult(StrictModel):
    preset: AgentPreset
    revision: AgentPresetRevision


def canonical_digest(value: BaseModel | JsonObject | tuple[object, ...]) -> str:
    """Return the canonical SHA-256 digest for one finite JSON value."""

    if isinstance(value, BaseModel):
        payload: object = value.model_dump(mode="json", by_alias=True)
    else:
        payload = value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()
