"""Strict file-backed configuration and resource catalog contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from enum import Enum, StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self, get_args, get_origin
from urllib.parse import unquote_plus, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

_SCHEMA_VERSION = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
_ID = Annotated[str, Field(min_length=3, max_length=128, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")]
_NAME = Annotated[str, Field(min_length=1, max_length=256)]
_DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_KEY = Annotated[str, Field(min_length=3, max_length=128, pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")]
_SECRET_NAMES = re.compile(
    r"(?:^|[_-])(api[_-]?key|authorization|bearer|credential|password|private[_-]?key|secret|token)(?:$|[_-])", re.I
)
_SECRET_VALUES = re.compile(
    r"^(?:Bearer|Basic)\s+\S+|-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    re.I,
)


class StrictModel(BaseModel):
    """Common immutable strict document behavior."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _freeze_serialized_collections(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name, field in cls.model_fields.items():
            raw = normalized.get(name)
            origin = _serialized_collection_origin(field.annotation)
            if isinstance(raw, list) and origin is tuple:
                normalized[name] = tuple(raw)
            elif isinstance(raw, list) and origin is frozenset:
                normalized[name] = frozenset(raw)
        return normalized


def _serialized_collection_origin(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin in {tuple, frozenset}:
        return origin
    for argument in get_args(annotation):
        nested = _serialized_collection_origin(argument)
        if nested in {tuple, frozenset}:
            return nested
    return origin


class ResourceKind(StrEnum):
    model = "model"
    prompt = "prompt"
    plugin = "plugin"
    skill_source = "skill_source"
    skill = "skill"
    agent = "agent"
    environment = "environment"


class DefinitionRootSettings(StrictModel):
    """One ordered source layer selected for the process lifetime."""

    root_id: _ID
    path: Path
    writable: bool = False

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, value: Path) -> Path:
        return _normalize_absolute_path(value, "definition root")


class LocalDirectorySettings(StrictModel):
    """A process-only alias authorizing a native directory for management reads."""

    directory_id: _ID
    path: Path

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, value: Path) -> Path:
        return _normalize_absolute_path(value, "local directory")


class ProjectRootPolicy(StrEnum):
    disabled = "disabled"
    explicit = "explicit"


class ConfigurationSettings(StrictModel):
    """Reloadable catalog policy and restart-bound source selection."""

    schema_version: Literal["1"] = "1"
    definition_roots: tuple[DefinitionRootSettings, ...] = ()
    project_root_policy: ProjectRootPolicy = ProjectRootPolicy.disabled
    project_root: DefinitionRootSettings | None = None
    local_directories: tuple[LocalDirectorySettings, ...] = ()
    skill_discovery: LocalSkillDiscoverySettings = Field(  # type: ignore[name-defined]
        default_factory=lambda: LocalSkillDiscoverySettings()
    )
    credential_backends: tuple[_KEY, ...] = ("a13n.environment",)
    model_adapter_keys: tuple[_KEY, ...] = ()
    plugin_keys: tuple[_KEY, ...] = ()
    builtin_provider_keys: tuple[_KEY, ...] = ("a13n.direct-local",)
    extension_provider_keys: tuple[_KEY, ...] = ()
    max_source_files: int = Field(default=4096, gt=0, le=100_000)
    max_source_bytes: int = Field(default=4 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    max_source_transaction_bytes: int = Field(
        default=128 * 1024 * 1024,
        ge=1024,
        le=1024 * 1024 * 1024,
    )
    max_prepared_skill_bytes: int = Field(
        default=128 * 1024 * 1024,
        ge=1024,
        le=1024 * 1024 * 1024,
    )
    max_yaml_nodes: int = Field(default=100_000, gt=0, le=1_000_000)
    max_document_depth: int = Field(default=64, gt=0, le=256)
    stable_read_attempts: int = Field(default=3, gt=0, le=10)
    orphan_retention_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0, le=365 * 24 * 60 * 60)
    max_skill_package_files: int = Field(default=4096, gt=0, le=100_000)
    max_skill_package_depth: int = Field(default=32, gt=0, le=256)
    max_skill_package_bytes: int = Field(default=64 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    max_skill_scan_leases: int = Field(default=16, gt=0, le=1024)
    skill_scan_lease_seconds: float = Field(default=300.0, gt=0, le=3600)
    reconciliation_interval_seconds: float = Field(default=5.0, gt=0, le=3600)
    envd_executable_override: Path | None = None

    @field_validator(
        "credential_backends",
        "model_adapter_keys",
        "plugin_keys",
        "builtin_provider_keys",
        "extension_provider_keys",
    )
    @classmethod
    def _unique_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("selected catalog keys must be unique")
        return value

    @field_validator("envd_executable_override")
    @classmethod
    def _absolute_envd_override(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if "\x00" in str(value) or not value.expanduser().is_absolute():
            raise ValueError("envd executable override must be one explicit absolute path")
        expanded = value.expanduser()
        if expanded.name not in {"agent-envd", "agent-envd.exe"}:
            raise ValueError("envd executable override must name agent-envd or agent-envd.exe")
        if expanded.exists() and expanded.is_dir():
            raise ValueError("envd executable override must not be a directory")
        return expanded.resolve(strict=False)

    @model_validator(mode="after")
    def _consistent_sources(self) -> Self:
        root_ids = [root.root_id for root in self.definition_roots]
        paths = [root.path for root in self.definition_roots]
        if self.project_root_policy is ProjectRootPolicy.explicit:
            if self.project_root is None:
                raise ValueError("explicit project-root policy requires project_root")
            root_ids.append(self.project_root.root_id)
            paths.append(self.project_root.path)
        elif self.project_root is not None:
            raise ValueError("project_root requires explicit project-root policy")
        if len(root_ids) != len(set(root_ids)) or len(paths) != len(set(paths)):
            raise ValueError("definition roots must have unique IDs and paths")
        directory_ids = [item.directory_id for item in self.local_directories]
        if len(directory_ids) != len(set(directory_ids)):
            raise ValueError("local directory IDs must be unique")
        if set(self.builtin_provider_keys) & set(self.extension_provider_keys):
            raise ValueError("built-in and extension provider selections must not overlap")
        return self

    @property
    def ordered_roots(self) -> tuple[DefinitionRootSettings, ...]:
        if self.project_root is None:
            return self.definition_roots
        return (*self.definition_roots, self.project_root)


class SafeSourceRef(StrictModel):
    """Path-free durable provenance relative to one configured source alias."""

    source_id: _ID
    relative_path: str = Field(min_length=1, max_length=1024)

    @field_validator("relative_path")
    @classmethod
    def _safe_relative_path(cls, value: str) -> str:
        return _normalize_relative_path(value)


class ResourceRef(StrictModel):
    kind: ResourceKind
    resource_id: _ID

    @field_validator("kind", mode="before")
    @classmethod
    def _serialized_kind(cls, value: object) -> object:
        if isinstance(value, str):
            return ResourceKind(value)
        return value

    @model_validator(mode="after")
    def _kind_prefix(self) -> Self:
        prefix = self.kind.value.replace("_", "-")
        if not self.resource_id.startswith(f"{prefix}-"):
            raise ValueError("resource_id must use its kind prefix")
        return self


class ResourceRevisionRef(ResourceRef):
    content_digest: _DIGEST


class DependencyLock(StrictModel):
    dependency_kind: Literal["model_adapter", "harness_plugin", "environment_provider", "skill_codec"]
    key: _KEY
    distribution_name: str | None = Field(default=None, min_length=1, max_length=200)
    distribution_version: str | None = Field(default=None, min_length=1, max_length=100)


class ResourceRevision(StrictModel):
    ref: ResourceRevisionRef
    schema_version: _SCHEMA_VERSION
    normalized_content: JsonValue
    source: SafeSourceRef
    dependency_provenance: tuple[DependencyLock, ...] = ()

    @field_validator("normalized_content")
    @classmethod
    def _finite_content(cls, value: JsonValue) -> JsonValue:
        _require_finite_json(value)
        return value


class ConfigurationGeneration(StrictModel):
    generation_id: _ID
    accepted_at: datetime
    process_settings_digest: _DIGEST
    resources: tuple[ResourceRevisionRef, ...]
    catalog_digest: _DIGEST
    restart_required: bool

    @field_validator("accepted_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("accepted_at must be timezone-aware")
        return value.astimezone(UTC)


class SourceTransactionEntry(StrictModel):
    relative_path: str = Field(min_length=1, max_length=1024)
    operation: Literal["replace", "delete"]
    content_digest: _DIGEST | None = None

    @field_validator("relative_path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _normalize_relative_path(value)

    @model_validator(mode="after")
    def _operation_shape(self) -> Self:
        if (self.operation == "replace") != (self.content_digest is not None):
            raise ValueError("replace requires content_digest and delete forbids it")
        return self


class SourceTransactionManifest(StrictModel):
    schema_version: Literal["1"]
    transaction_id: _ID
    root_id: _ID
    base_catalog_digest: _DIGEST
    entries: tuple[SourceTransactionEntry, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def _unique_ordered_entries(self) -> Self:
        paths = tuple(entry.relative_path for entry in self.entries)
        if len(paths) != len(set(paths)):
            raise ValueError("source transaction paths must be unique")
        if paths != tuple(sorted(paths)):
            raise ValueError("source transaction entries must be ordered by relative_path")
        return self


class ModelDefinition(StrictModel):
    schema_version: Literal["1"]
    model_id: _ID
    display_name: _NAME
    provider_key: _KEY
    model_name: str = Field(min_length=1, max_length=512)
    endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    credential_ref: _ID | None = None

    @field_validator("endpoint")
    @classmethod
    def _credential_free_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("endpoint must be a credential-free HTTP(S) URL")
        query_names = {unquote_plus(part.split("=", 1)[0]) for part in parsed.query.split("&") if part}
        if any(_is_secret_name(name) for name in query_names):
            raise ValueError("endpoint query parameters must not contain credentials")
        return value

    @model_validator(mode="after")
    def _no_literal_credentials(self) -> Self:
        _reject_secret_fields(self.settings)
        return self


class PromptBlock(StrictModel):
    content: str | None = Field(default=None, min_length=1, max_length=1024 * 1024)
    source: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.content is None) == (self.source is None):
            raise ValueError("prompt block requires exactly one of content or source")
        if self.source is not None:
            _normalize_relative_path(self.source)
        return self


class PromptDefinition(StrictModel):
    schema_version: Literal["1"]
    prompt_id: _ID
    display_name: _NAME
    description: str | None = Field(default=None, max_length=16 * 1024)
    system_prompt_blocks: tuple[PromptBlock, ...] = Field(min_length=1, max_length=1024)


class PluginInstanceDefinition(StrictModel):
    schema_version: Literal["1"]
    plugin_resource_id: _ID
    display_name: _NAME
    plugin_key: _KEY
    plugin_id: _ID
    enabled: bool
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_literal_credentials(self) -> Self:
        _reject_secret_fields(self.configuration)
        return self


class LocalSkillSourceDefinition(StrictModel):
    schema_version: Literal["1"]
    skill_source_id: _ID
    display_name: _NAME
    directory_id: _ID
    roots: tuple[str, ...] = Field(min_length=1, max_length=1024)
    required: bool = True
    max_entries_per_root: int = Field(default=256, gt=0, le=100_000)

    @field_validator("roots")
    @classmethod
    def _logical_roots(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("skill roots must be unique")
        for root in value:
            if not root.startswith("/") or "\x00" in root or any(part in {".", ".."} for part in root.split("/")):
                raise ValueError("skill roots must be canonical absolute logical paths")
        return value


class LocalSkillDiscoverySettings(StrictModel):
    ordered_sources: tuple[ResourceRef, ...] = ()
    conflict: Literal["error", "prefer_earlier", "prefer_later"] = "error"
    max_skills: int = Field(default=512, gt=0, le=10_000)

    @model_validator(mode="after")
    def _skill_source_refs(self) -> Self:
        if any(item.kind is not ResourceKind.skill_source for item in self.ordered_sources):
            raise ValueError("ordered_sources entries must reference skill_source resources")
        ids = [item.resource_id for item in self.ordered_sources]
        if len(ids) != len(set(ids)):
            raise ValueError("ordered_sources entries must be unique")
        return self


class SkillPackageSource(StrictModel):
    root_id: _ID
    relative_path: str = Field(min_length=1, max_length=1024)

    @field_validator("relative_path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _normalize_relative_path(value)


class SkillImportProvenance(StrictModel):
    source_id: _ID
    skill_name: str = Field(min_length=1, max_length=256)
    source_catalog_digest: _DIGEST


class SkillCompatibility(StrictModel):
    harness_skill_contract: Literal["1"] = "1"


class SkillDefinition(StrictModel):
    schema_version: Literal["1"]
    skill_id: _ID
    display_name: _NAME
    skill_name: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=16 * 1024)
    package: SkillPackageSource
    imported_from: SkillImportProvenance | None = None
    compatibility: SkillCompatibility = SkillCompatibility()


class SkillNameSelection(StrictModel):
    mode: Literal["all", "exact"] = "all"
    names: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent_selection(self) -> Self:
        if len(self.names) != len(set(self.names)):
            raise ValueError("Skill selection names must be unique")
        if self.mode == "all" and self.names:
            raise ValueError("all-mode Skill selection does not accept names")
        return self


class AgentSkillConfiguration(StrictModel):
    available: tuple[ResourceRef, ...] = ()
    materialization_mount: _ID | None = None
    default_selection: SkillNameSelection = SkillNameSelection()

    @model_validator(mode="after")
    def _skill_references(self) -> Self:
        if any(item.kind is not ResourceKind.skill for item in self.available):
            raise ValueError("available Skills must reference skill resources")
        ids = [item.resource_id for item in self.available]
        if len(ids) != len(set(ids)):
            raise ValueError("available Skill references must be unique")
        return self


class EnvironmentMountRequirement(StrictModel):
    mount_name: _ID
    required_operations: frozenset[str] = frozenset()


class AgentEnvironmentRequirements(StrictModel):
    mounts: tuple[EnvironmentMountRequirement, ...] = ()

    @model_validator(mode="after")
    def _unique_mounts(self) -> Self:
        names = [item.mount_name for item in self.mounts]
        if len(names) != len(set(names)):
            raise ValueError("Agent Environment mount requirements must be unique")
        return self


class DelegationContextSelection(StrictModel):
    """Portable child-context ceilings mapped to the Harness public contract."""

    include_task: bool = True
    history: Literal["none", "summary", "selected"] = "none"
    task_state: Literal["shared", "isolated"] = "shared"


class SubagentIdentitySelection(StrictModel):
    """Portable child Identity inheritance mapped to the Harness public contract."""

    inherit_agent_id: bool = False


class UsageLimitsSelection(StrictModel):
    """Serializable subset of Pydantic AI usage ceilings supported by Agent UI."""

    request_limit: int | None = Field(default=None, ge=1)
    tool_calls_limit: int | None = Field(default=None, ge=1)
    input_tokens_limit: int | None = Field(default=None, ge=1)
    output_tokens_limit: int | None = Field(default=None, ge=1)
    total_tokens_limit: int | None = Field(default=None, ge=1)
    per_request_input_tokens_limit: int | None = Field(default=None, ge=1)
    count_tokens_before_request: bool = False


class ChildEnvironmentPolicy(StrictModel):
    mode: Literal["none", "dedicated", "shared_root", "serialized_root"] = "none"
    mounts: tuple[_ID, ...] | None = None

    @field_validator("mounts")
    @classmethod
    def _unique_mounts(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("child Environment mount selections must be unique")
        return value


class SubagentEdge(StrictModel):
    name: _ID
    description: str = Field(min_length=1, max_length=16 * 1024)
    agent: ResourceRef
    context: DelegationContextSelection = DelegationContextSelection()
    identity: SubagentIdentitySelection = SubagentIdentitySelection()
    usage_limits: UsageLimitsSelection | None = None
    environment: ChildEnvironmentPolicy = ChildEnvironmentPolicy()
    lifetime: Literal["parent_scope", "session"] = "parent_scope"
    steering: Literal["enabled", "disabled"] = "disabled"
    continuation: Literal["enabled", "disabled"] = "disabled"

    @model_validator(mode="after")
    def _agent_reference(self) -> Self:
        if self.agent.kind is not ResourceKind.agent:
            raise ValueError("subagent edges must reference agent resources")
        return self


class DynamicEnvironmentCapabilityConfiguration(StrictModel):
    file_tools: bool = True
    shell_tools: bool = True
    max_reference_entries: int = Field(default=1024, gt=0, le=100_000)


class WorkingStateCapabilityConfiguration(StrictModel):
    task_mode: Literal["embedded", "provider"] = "embedded"
    tasks_enabled: bool = True
    notes_enabled: bool = True
    max_context_tasks: int = Field(default=128, gt=0, le=10_000)
    max_context_note_keys: int = Field(default=256, gt=0, le=10_000)
    max_context_bytes: int = Field(default=64 * 1024, ge=1024, le=256 * 1024)


class DynamicEnvironmentCapabilitySelection(StrictModel):
    key: Literal["a13n.dynamic-environment"]
    schema_version: Literal["1"] = "1"
    configuration: DynamicEnvironmentCapabilityConfiguration = DynamicEnvironmentCapabilityConfiguration()


class WorkingStateCapabilitySelection(StrictModel):
    key: Literal["a13n.working-state"]
    schema_version: Literal["1"] = "1"
    configuration: WorkingStateCapabilityConfiguration = WorkingStateCapabilityConfiguration()


class UserInteractionCapabilitySelection(StrictModel):
    key: Literal["a13n.user-interaction"]
    schema_version: Literal["1"] = "1"


class DocumentsCapabilitySelection(StrictModel):
    key: Literal["a13n.documents"]
    schema_version: Literal["1"] = "1"


class MediaCapabilitySelection(StrictModel):
    key: Literal["a13n.media"]
    schema_version: Literal["1"] = "1"


class WebCapabilitySelection(StrictModel):
    key: Literal["a13n.web"]
    schema_version: Literal["1"] = "1"


class MCPContextHeaderSelection(StrictModel):
    source: str = Field(min_length=1, max_length=1024)
    required: bool = True

    @field_validator("source")
    @classmethod
    def _supported_source(cls, value: str) -> str:
        if value in {
            "identity.issuer",
            "identity.subject",
            "instance.agent_instance_id",
            "instance.parent_agent_instance_id",
            "instance.delegation_id",
            "instance.actor",
            "context.run_id",
            "context.thread_id",
        }:
            return value
        if value.startswith("identity.") and value.removeprefix("identity."):
            return value
        if value.startswith("context.metadata.") and value.removeprefix("context.metadata."):
            return value
        raise ValueError("unsupported MCP context header source")


class MCPSelection(StrictModel):
    key: Literal["a13n.mcp"]
    schema_version: Literal["1"] = "1"
    id: _ID
    url: str = Field(min_length=1, max_length=2048)
    execution: Literal["auto", "local", "native"] = "auto"
    allowed_tools: tuple[str, ...] | None = None
    description: str | None = Field(default=None, max_length=16 * 1024)
    defer_loading: bool = False
    context_headers: dict[str, MCPContextHeaderSelection]

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("MCP url must be an HTTP(S) URL")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def _valid_allowed_tools(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        if any(not item.strip() for item in value) or len(value) != len(set(value)):
            raise ValueError("MCP allowed_tools must contain unique non-blank names")
        return value

    @field_validator("context_headers")
    @classmethod
    def _valid_context_headers(
        cls,
        value: dict[str, MCPContextHeaderSelection],
    ) -> dict[str, MCPContextHeaderSelection]:
        if any(not name.strip() for name in value):
            raise ValueError("MCP context header names must be non-blank")
        folded = [name.casefold() for name in value]
        if len(folded) != len(set(folded)):
            raise ValueError("MCP context header names must be unique case-insensitively")
        return value


type FirstPartyCapabilitySelection = Annotated[
    DynamicEnvironmentCapabilitySelection
    | WorkingStateCapabilitySelection
    | UserInteractionCapabilitySelection
    | DocumentsCapabilitySelection
    | MediaCapabilitySelection
    | WebCapabilitySelection
    | MCPSelection,
    Field(discriminator="key"),
]


class AsyncSubagentConfiguration(StrictModel):
    tools: Literal["standard", "disabled"] = "disabled"
    max_active_jobs: int = Field(default=8, gt=0, le=1024)
    max_jobs_per_run: int = Field(default=32, gt=0, le=10_000)
    max_depth: int = Field(default=8, gt=0, le=64)
    completion_delivery: Literal["active_or_next_run", "manual"] = "active_or_next_run"


class AgentOutputSelection(StrictModel):
    key: Literal["text"] = "text"
    schema_version: Literal["1"] = "1"


class ModelRecoverySelection(StrictModel):
    enabled: bool = False
    max_attempts: int = Field(default=5, ge=1, le=100)
    continuation_prompt: str = Field(
        default="The previous model stream ended before the task finished. Continue from the available history.",
        min_length=1,
        max_length=16 * 1024,
    )
    backoff_initial_seconds: float = Field(default=1.0, ge=0, le=3600)
    backoff_max_seconds: float = Field(default=30.0, ge=0, le=3600)

    @model_validator(mode="after")
    def _consistent_backoff(self) -> Self:
        if self.backoff_initial_seconds > self.backoff_max_seconds:
            raise ValueError("initial recovery backoff must not exceed maximum backoff")
        return self


class ResolvedSkillRevisionContent(SkillDefinition):
    resolved_package_digest: _DIGEST


class AgentDefinitionDocument(StrictModel):
    schema_version: Literal["1"]
    agent_id: _ID
    display_name: _NAME
    description: str | None = Field(default=None, max_length=16 * 1024)
    model: ResourceRef
    prompt: ResourceRef
    plugins: tuple[ResourceRef, ...] = ()
    skills: AgentSkillConfiguration = AgentSkillConfiguration()
    capabilities: tuple[FirstPartyCapabilitySelection, ...] = ()
    environment: AgentEnvironmentRequirements = AgentEnvironmentRequirements()
    subagents: tuple[SubagentEdge, ...] = ()
    async_subagents: AsyncSubagentConfiguration = AsyncSubagentConfiguration()
    output: AgentOutputSelection = AgentOutputSelection()
    model_recovery: ModelRecoverySelection = ModelRecoverySelection()

    @model_validator(mode="after")
    def _direct_reference_kinds(self) -> Self:
        if self.model.kind is not ResourceKind.model or self.prompt.kind is not ResourceKind.prompt:
            raise ValueError("agent model and prompt references have incompatible kinds")
        if any(item.kind is not ResourceKind.plugin for item in self.plugins):
            raise ValueError("agent plugins must reference plugin resources")
        child_names = [item.name for item in self.subagents]
        if len(child_names) != len(set(child_names)):
            raise ValueError("subagent edge names must be unique")
        singleton_keys = [item.key for item in self.capabilities if not isinstance(item, MCPSelection)]
        if len(singleton_keys) != len(set(singleton_keys)):
            raise ValueError("Singleton Agent Capability selections must be unique")
        mcp_ids = [item.id for item in self.capabilities if isinstance(item, MCPSelection)]
        if len(mcp_ids) != len(set(mcp_ids)):
            raise ValueError("MCP Capability IDs must be unique within one Agent")
        return self


class EnvironmentMountDefinition(StrictModel):
    mount_name: _ID
    model_alias: Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9-]{0,62}$")]
    provider_key: _KEY
    provider_schema_version: _SCHEMA_VERSION
    provider_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    permission_ceiling: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _credential_free(self) -> Self:
        _reject_secret_fields(self.provider_parameters)
        _reject_native_paths(self.provider_parameters)
        return self


class SessionEnvironmentLifecyclePolicy(StrictModel):
    provision: Literal["eager", "on_first_run"]
    idle: Literal["keep_running", "pause_full", "pause_filesystem"]
    release: Literal["retain", "destroy_when_unreferenced"]


class EnvironmentDefinitionDocument(StrictModel):
    schema_version: Literal["1"]
    environment_id: _ID
    display_name: _NAME
    description: str | None = Field(default=None, max_length=16 * 1024)
    mounts: tuple[EnvironmentMountDefinition, ...] = Field(max_length=1024)
    default_mount: _ID | None = None
    lifecycle: SessionEnvironmentLifecyclePolicy

    @model_validator(mode="after")
    def _mount_consistency(self) -> Self:
        names = [item.mount_name for item in self.mounts]
        aliases = [item.model_alias for item in self.mounts]
        if len(names) != len(set(names)) or len(aliases) != len(set(aliases)):
            raise ValueError("Environment mount names and model aliases must be unique")
        if (not self.mounts) != (self.default_mount is None):
            raise ValueError("default_mount is absent exactly when mounts is empty")
        if self.default_mount is not None and self.default_mount not in names:
            raise ValueError("default_mount must name one desired mount")
        return self


type ResourceDocument = (
    ModelDefinition
    | PromptDefinition
    | PluginInstanceDefinition
    | LocalSkillSourceDefinition
    | SkillDefinition
    | AgentDefinitionDocument
    | EnvironmentDefinitionDocument
)


class EnvdRuntimeAsset(StrictModel):
    target: Literal[
        "linux-x86_64",
        "linux-aarch64",
        "darwin-x86_64",
        "darwin-aarch64",
        "windows-x86_64",
        "windows-aarch64",
    ]
    archive_name: str = Field(min_length=1, max_length=256)
    archive_url: str = Field(min_length=1, max_length=2048)
    archive_sha256: _DIGEST
    executable_name: Literal["agent-envd", "agent-envd.exe"]
    executable_sha256: _DIGEST

    @model_validator(mode="after")
    def _immutable_asset(self) -> Self:
        parsed = urlsplit(self.archive_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("runtime archive URL must be an immutable credential-free HTTPS selector")
        if PurePosixPath(parsed.path).name != self.archive_name:
            raise ValueError("runtime archive URL filename must match archive_name")
        windows = self.target.startswith("windows-")
        if windows != (self.executable_name == "agent-envd.exe"):
            raise ValueError("runtime executable name does not match target operating system")
        return self


class EnvdRuntimeManifest(StrictModel):
    schema_version: Literal["1"]
    envd_release: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?$")]
    assets: tuple[EnvdRuntimeAsset, ...] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def _six_exact_targets(self) -> Self:
        expected = {
            "linux-x86_64",
            "linux-aarch64",
            "darwin-x86_64",
            "darwin-aarch64",
            "windows-x86_64",
            "windows-aarch64",
        }
        targets = {asset.target for asset in self.assets}
        if targets != expected or len(targets) != len(self.assets):
            raise ValueError("runtime manifest must contain each supported target exactly once")
        if tuple(asset.target for asset in self.assets) != tuple(sorted(targets)):
            raise ValueError("runtime manifest assets must be ordered by target")
        return self


ConfigurationSettings.model_rebuild()


def restart_settings_digest(settings: ConfigurationSettings) -> str:
    """Digest settings whose implementation is fixed for one process lifetime."""

    return canonical_digest({"credential_backends": settings.credential_backends})


def canonical_json_value(value: object) -> JsonValue:
    """Return deterministic JSON, including stable serialization of unordered sets."""

    if isinstance(value, BaseModel):
        return canonical_json_value(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return canonical_json_value(value.value)
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON numbers must be finite")
        return value
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: canonical_json_value(value[key]) for key in sorted(value)}
    if isinstance(value, set | frozenset):
        items = [canonical_json_value(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if isinstance(value, list | tuple):
        return [canonical_json_value(item) for item in value]
    raise TypeError(f"{type(value).__name__} is not canonical JSON")


def canonical_digest(value: object) -> str:
    """Return the behavior digest for one finite JSON-compatible value."""

    encoded = json.dumps(
        canonical_json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resource_identity(document: ResourceDocument) -> ResourceRef:
    if isinstance(document, ModelDefinition):
        return ResourceRef(kind=ResourceKind.model, resource_id=document.model_id)
    if isinstance(document, PromptDefinition):
        return ResourceRef(kind=ResourceKind.prompt, resource_id=document.prompt_id)
    if isinstance(document, PluginInstanceDefinition):
        return ResourceRef(kind=ResourceKind.plugin, resource_id=document.plugin_resource_id)
    if isinstance(document, LocalSkillSourceDefinition):
        return ResourceRef(kind=ResourceKind.skill_source, resource_id=document.skill_source_id)
    if isinstance(document, SkillDefinition):
        return ResourceRef(kind=ResourceKind.skill, resource_id=document.skill_id)
    if isinstance(document, AgentDefinitionDocument):
        return ResourceRef(kind=ResourceKind.agent, resource_id=document.agent_id)
    if isinstance(document, EnvironmentDefinitionDocument):
        return ResourceRef(kind=ResourceKind.environment, resource_id=document.environment_id)
    raise TypeError("unsupported resource document")


def _normalize_absolute_path(value: Path, subject: str) -> Path:
    if "\x00" in str(value) or not value.expanduser().is_absolute():
        raise ValueError(f"{subject} path must be absolute")
    return value.expanduser().resolve(strict=False)


def _normalize_relative_path(value: str) -> str:
    if "\x00" in value or "\\" in value:
        raise ValueError("source paths must use canonical POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("source path must be canonical and relative")
    normalized = path.as_posix()
    if normalized != value:
        raise ValueError("source path must already be normalized")
    return normalized


def _reject_secret_fields(value: JsonValue, *, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("configuration exceeds the maximum JSON depth")
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_secret_name(key):
                raise ValueError("literal credential-like fields are forbidden")
            _reject_secret_fields(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_fields(item, depth=depth + 1)
    elif isinstance(value, str) and _SECRET_VALUES.search(value.strip()):
        raise ValueError("literal credential values are forbidden")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("configuration values must be finite")


def _is_secret_name(value: str) -> bool:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return _SECRET_NAMES.search(separated) is not None


def _reject_native_paths(value: JsonValue, *, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("configuration exceeds the maximum JSON depth")
    if isinstance(value, dict):
        for item in value.values():
            _reject_native_paths(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _reject_native_paths(item, depth=depth + 1)
    elif isinstance(value, str):
        windows_drive = len(value) >= 3 and value[0].isalpha() and value[1:3] in {":/", ":\\"}
        if value.startswith(("/", "\\\\")) or windows_drive:
            raise ValueError("native paths must use process-owned directory aliases")


def _require_finite_json(value: JsonValue, *, depth: int = 0) -> None:
    if depth > 256:
        raise ValueError("JSON value exceeds the maximum supported depth")
    if isinstance(value, dict):
        for item in value.values():
            _require_finite_json(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _require_finite_json(item, depth=depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON values must be finite")


__all__ = [
    "AgentDefinitionDocument",
    "AgentOutputSelection",
    "AsyncSubagentConfiguration",
    "ConfigurationGeneration",
    "ConfigurationSettings",
    "DefinitionRootSettings",
    "DependencyLock",
    "EnvdRuntimeAsset",
    "EnvdRuntimeManifest",
    "EnvironmentDefinitionDocument",
    "EnvironmentMountDefinition",
    "FirstPartyCapabilitySelection",
    "LocalDirectorySettings",
    "LocalSkillDiscoverySettings",
    "LocalSkillSourceDefinition",
    "ModelDefinition",
    "ModelRecoverySelection",
    "PluginInstanceDefinition",
    "ProjectRootPolicy",
    "PromptBlock",
    "PromptDefinition",
    "ResolvedSkillRevisionContent",
    "ResourceDocument",
    "ResourceKind",
    "ResourceRef",
    "ResourceRevision",
    "ResourceRevisionRef",
    "SafeSourceRef",
    "SessionEnvironmentLifecyclePolicy",
    "SkillCompatibility",
    "SkillDefinition",
    "SkillImportProvenance",
    "SkillPackageSource",
    "SourceTransactionEntry",
    "SourceTransactionManifest",
    "UsageLimitsSelection",
    "canonical_digest",
    "canonical_json_value",
    "resource_identity",
    "restart_settings_digest",
]
