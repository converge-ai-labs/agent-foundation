"""Agent configuration as a revision freezes it, per-run overrides of it, and the agent API values.

Skill and subagent edges may omit `revision_id` on input; revision creation and override validation pin it
to the head's default revision, so a stored configuration always names exact revisions.
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from a13n_harness.capabilities import ToolReviewConfig
from a13n_harness.tools.client import ClientToolDefinition
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator
from pydantic_ai.usage import UsageLimits

from a13n_service.infra.ids import ObjectId
from a13n_service.infra.labels import Labels
from a13n_service.providers.model_settings import JsonSettings
from a13n_service.resources.agents.toolsets import ToolsetOverrides, Toolsets, default_toolsets
from a13n_service.resources.connections.schemas import ConnectionSelection
from a13n_service.resources.memories.schemas import MemoryMounts
from a13n_service.resources.models.schemas import MediaUnderstandingSelection
from a13n_service.resources.secrets.schemas import SecretRequirement

BoundedKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
PluginKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")]
Description = Annotated[str, StringConstraints(max_length=4096)]
Instructions = Annotated[str, StringConstraints(max_length=256 * 1024)]
AgentKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")]
AgentName = Annotated[str, StringConstraints(min_length=1, max_length=128)]
AgentDescription = Annotated[str, StringConstraints(max_length=8192)]
JsonObject = dict[str, JsonValue]


ModelSettings = JsonSettings


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Schemas(BaseModel):
    """Values with a `schema` field, which would shadow a `BaseModel` attribute under its own name."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_by_name=True, serialize_by_alias=True)


class AgentModelCharacteristics(_Frozen):
    """The agent's context policy, layered over what the model declares."""

    # Overrides the model's declared context window.
    context_window_tokens: int | None = Field(default=None, gt=0)
    proactive_context_management_threshold: float | None = Field(default=0.65, ge=0.0, le=1.0)
    compact_threshold: float = Field(default=0.90, gt=0.0, le=1.0)


class AgentModel(_Frozen):
    model_id: ObjectId
    # Native settings layered over the model's own defaults.
    settings: ModelSettings = Field(default_factory=dict)
    characteristics: AgentModelCharacteristics = Field(default_factory=AgentModelCharacteristics)


class SkillSelection(_Frozen):
    skill_id: ObjectId
    revision_id: ObjectId | None = None


class DelegationContextPolicy(_Frozen):
    include_task: bool = True
    history: Literal["none", "summary", "selected"] = "none"
    task_state: Literal["shared", "isolated"] = "shared"


class ChildEnvironmentPolicy(_Frozen):
    """What a child run mounts: no environment, the parent's, or a new one from `template_id`."""

    mode: Literal["none", "shared", "dedicated"] = "shared"
    template_id: ObjectId | None = None

    @model_validator(mode="after")
    def template_for_dedicated(self) -> Self:
        if (self.mode == "dedicated") != (self.template_id is not None):
            raise ValueError("Exactly dedicated child environments name a template")
        return self


class SubagentSelection(_Frozen):
    agent_id: ObjectId
    revision_id: ObjectId | None = None
    # Shown to the delegating model; the child agent's description, else its name, when omitted.
    description: Description | None = None
    context: DelegationContextPolicy = Field(default_factory=DelegationContextPolicy)
    usage_limits: UsageLimits | None = None
    environment: ChildEnvironmentPolicy = Field(default_factory=ChildEnvironmentPolicy)


class OutputVariant(_Schemas):
    name: BoundedKey
    description: Description | None = None
    schema_: JsonObject = Field(alias="schema")
    resources: dict[BoundedKey, JsonObject] = Field(default_factory=dict, max_length=128)


class OutputSpec(_Schemas):
    """Plain text without a schema, one structured output, or at least two named variants.

    `resources` are the schemas `$ref`s may name besides the schema's own definitions.
    """

    name: BoundedKey | None = None
    description: Description | None = None
    schema_: JsonObject | None = Field(default=None, alias="schema")
    resources: dict[BoundedKey, JsonObject] = Field(default_factory=dict, max_length=128)
    variants: tuple[OutputVariant, ...] | None = Field(default=None, min_length=2, max_length=32)

    @model_validator(mode="after")
    def one_shape(self) -> Self:
        if self.variants is None:
            if self.schema_ is None and self.resources:
                raise ValueError("Resources require a schema")
        elif self.schema_ is not None or self.resources:
            raise ValueError("Variants cannot be combined with a top-level schema or resources")
        elif len({variant.name for variant in self.variants}) != len(self.variants):
            raise ValueError("Variant names must be unique")
        return self


class RetryConfig(_Frozen):
    tools: int = Field(default=0, ge=0, le=100)
    output: int = Field(default=0, ge=0, le=100)


class AgentReviewer(ToolReviewConfig):
    """The model reviewing calls whose permission is `review`, selected by model ID."""

    model: ObjectId
    model_settings: ModelSettings | None = None


class PluginSelection(_Frozen):
    """One instance of a Harness plugin factory the deployment installed."""

    instance_name: BoundedKey
    plugin_key: PluginKey
    config: JsonObject = Field(default_factory=dict)


class AgentConfig(_Frozen):
    model: AgentModel
    instructions: Instructions = ""
    toolsets: Toolsets = Field(default_factory=default_toolsets)
    skills: tuple[SkillSelection, ...] = Field(default=(), max_length=512)
    connection_tools: tuple[ConnectionSelection, ...] = Field(default=(), max_length=128)
    client_tools: tuple[ClientToolDefinition, ...] = Field(default=(), max_length=128)
    # Offers `ask_user_question`; a question makes the run wait for the next message.
    user_questions: bool = False
    # Inline children run inside the parent's run; async children run as child runs of their own.
    subagent_mode: Literal["inline", "async"] = "inline"
    subagents: dict[BoundedKey, SubagentSelection] = Field(default_factory=dict, max_length=128)
    reviewer: AgentReviewer | None = None
    media_understanding: MediaUnderstandingSelection = Field(default_factory=MediaUnderstandingSelection)
    plugins: tuple[PluginSelection, ...] = Field(default=(), max_length=128)
    output_spec: OutputSpec | None = None
    retries: RetryConfig | None = None
    secret_requirements: tuple[SecretRequirement, ...] = Field(default=(), max_length=128)
    # Referenced, not pinned: read when an environment is created from it, never during execution.
    default_environment_template_id: ObjectId | None = None
    # Added to a thread's memory mounts at its first acceptance, for names and memories it does not use yet.
    memory_mounts: MemoryMounts = ()

    @model_validator(mode="after")
    def unique_selections(self) -> Self:
        for label, values in (
            ("Skills", [item.skill_id for item in self.skills]),
            ("Connections", [item.connection_id for item in self.connection_tools]),
            ("Client tool names", [item.name for item in self.client_tools]),
            ("Plugin instance names", [item.instance_name for item in self.plugins]),
            ("Secret requirement keys", [item.key for item in self.secret_requirements]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        return self


class ModelOverride(_Frozen):
    model_id: ObjectId | None = None
    settings: ModelSettings | None = None
    characteristics: AgentModelCharacteristics | None = None


class SubagentOverride(_Frozen):
    """Replaces the fields it sets of an edge; an edge the revision lacks sets at least `agent_id`."""

    agent_id: ObjectId | None = None
    revision_id: ObjectId | None = None
    description: Description | None = None
    context: DelegationContextPolicy | None = None
    usage_limits: UsageLimits | None = None
    environment: ChildEnvironmentPolicy | None = None


class RetryOverride(_Frozen):
    tools: int | None = Field(default=None, ge=0, le=100)
    output: int | None = Field(default=None, ge=0, le=100)


class AgentOverride(_Frozen):
    """What one run changes of its revision's configuration; an omitted or null field keeps the revision's.

    `toolsets` replaces whole toolsets; `model`, `retries` and each subagent edge replace the fields they set,
    and a null edge removes it; every other field replaces the revision's value.
    """

    toolsets: ToolsetOverrides | None = None
    reviewer: AgentReviewer | None = None
    media_understanding: MediaUnderstandingSelection | None = None
    model: ModelOverride | None = None
    instructions: Instructions | None = None
    plugins: tuple[PluginSelection, ...] | None = Field(default=None, max_length=128)
    skills: tuple[SkillSelection, ...] | None = Field(default=None, max_length=512)
    connection_tools: tuple[ConnectionSelection, ...] | None = Field(default=None, max_length=128)
    subagents: dict[BoundedKey, SubagentOverride | None] | None = Field(default=None, max_length=128)
    client_tools: tuple[ClientToolDefinition, ...] | None = Field(default=None, max_length=128)
    output_spec: OutputSpec | None = None
    retries: RetryOverride | None = None


_REPLACED = (
    "reviewer",
    "media_understanding",
    "instructions",
    "plugins",
    "skills",
    "connection_tools",
    "client_tools",
    "output_spec",
)


def apply_override(config: AgentConfig, override: AgentOverride) -> AgentConfig:
    """The configuration a run executes: its revision's with the override applied, validated as a whole."""
    fields: dict[str, object] = dict(config)
    fields.update((name, value) for name in _REPLACED if (value := getattr(override, name)) is not None)
    if override.toolsets is not None:
        fields["toolsets"] = {**config.toolsets, **override.toolsets}
    if override.model is not None:
        fields["model"] = {**dict(config.model), **_set(override.model)}
    if override.retries is not None:
        fields["retries"] = {**dict(config.retries or RetryConfig()), **_set(override.retries)}
    if override.subagents is not None:
        edges: dict[str, object] = dict(config.subagents)
        for name, edge in override.subagents.items():
            if edge is None:
                edges.pop(name, None)
                continue
            changes = _set(edge)
            # Another agent without a revision runs its own default, never the replaced agent's pin.
            if "agent_id" in changes and "revision_id" not in changes:
                changes["revision_id"] = None
            current = config.subagents.get(name)
            edges[name] = {**(dict(current) if current is not None else {}), **changes}
        fields["subagents"] = edges
    return AgentConfig.model_validate(fields)


def freeze_override(override: AgentOverride, validated: AgentConfig) -> AgentOverride:
    """The override as a run freezes it: with the pins and normalized plugin configs of `validated`, the
    configuration it produced once validated."""
    changes: dict[str, object] = {}
    if override.plugins is not None:
        changes["plugins"] = validated.plugins
    if override.skills is not None:
        changes["skills"] = validated.skills
    if override.subagents is not None:
        changes["subagents"] = {
            name: None
            if edge is None
            else edge.model_copy(update={"revision_id": validated.subagents[name].revision_id})
            for name, edge in override.subagents.items()
        }
    return override.model_copy(update=changes)


def _set(value: BaseModel) -> dict[str, object]:
    return {name: item for name, item in value if item is not None}


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: AgentKey
    name: AgentName
    description: AgentDescription = ""
    labels: Labels = Field(default_factory=dict)
    config: AgentConfig


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: AgentName | None = None
    # Links naming the old key stop resolving; everything else refers to the agent by ID.
    key: AgentKey | None = None
    description: AgentDescription | None = None
    labels: Labels | None = None


class AgentDuplicate(BaseModel):
    """A new head whose first revision copies `revision_id`, by default the source's default revision."""

    model_config = ConfigDict(extra="forbid")
    key: AgentKey
    name: AgentName
    description: AgentDescription = ""
    labels: Labels = Field(default_factory=dict)
    revision_id: ObjectId | None = None


class AgentRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: AgentConfig
    note: str | None = Field(default=None, max_length=2048)
    make_default: bool = True


class AgentValidate(BaseModel):
    """A configuration to check as creating a revision would, storing nothing.

    `agent_id` names the agent it would become a revision of, whose inline subagents may not lead back to it;
    omit it for a new agent.
    """

    model_config = ConfigDict(extra="forbid")
    config: AgentConfig
    agent_id: ObjectId | None = None


class Agent(BaseModel):
    id: str
    organization_id: str
    workspace_id: str
    key: str
    name: str
    description: str
    labels: dict[str, str]
    default_revision_id: str | None
    source: str
    image_url: str | None
    archived_at: datetime | None
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class AgentRevision(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_id: str
    number: int
    config: AgentConfig
    digest: str
    note: str | None
    created_by_id: str
    created_at: datetime


class AgentPage(BaseModel):
    items: list[Agent]
    next_cursor: str | None


class AgentRevisionPage(BaseModel):
    items: list[AgentRevision]
    next_cursor: str | None
