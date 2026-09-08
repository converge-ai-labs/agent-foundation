"""Strict serialized contracts for the Harness UI configuration tree."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self, get_args, get_origin
from urllib.parse import unquote_plus, urlsplit

from a13n_harness.spec import HarnessModelCharacteristics
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from a13n_harness_ui.content_plugins import InstalledContentPlugin
from a13n_harness_ui.environment_profiles import built_in_environment_profile
from a13n_harness_ui.subagents import BuiltinSubagentName

_RESOURCE_ID = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$"
_NAME = r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$"
_CATALOG_KEY = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_FIELD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "bearer_token",
        "client_secret",
        "credential",
        "password",
        "private_key",
        "refresh_token",
        "secret",
    }
)

type ResourceId = Annotated[str, Field(min_length=3, max_length=128, pattern=_RESOURCE_ID)]
type CatalogKey = Annotated[str, Field(min_length=1, max_length=200, pattern=_CATALOG_KEY)]
type ToolName = Annotated[str, Field(min_length=1, max_length=128, pattern=_CATALOG_KEY)]
type RosterName = Annotated[str, Field(min_length=1, max_length=63, pattern=_NAME)]
type SourceDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    """Immutable strict behavior shared by serialized contracts."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_serialized_collections(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name, field in cls.model_fields.items():
            raw = normalized.get(name)
            if isinstance(raw, list) and _contains_tuple(field.annotation):
                normalized[name] = tuple(raw)
        return normalized


class ProcessConfiguration(StrictModel):
    pricing_auto_update: bool = True
    terminal_update_check: bool = True
    log_level: str = Field(default="INFO", min_length=1, max_length=32)
    log_format: Literal["pretty", "json"] = "pretty"

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("log_level must be a standard logging level")
        return normalized


class GlobalDefaults(StrictModel):
    project: ResourceId | None = None
    agent: ResourceId | None = None
    environment_profile: ResourceId | None = None
    harness_plugins: tuple[ResourceId, ...] = ()
    environment_run_extensions: tuple[ResourceId, ...] = ()
    mcp_servers: tuple[ResourceId, ...] = ()

    @field_validator("harness_plugins", "environment_run_extensions", "mcp_servers")
    @classmethod
    def _unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("default resource IDs must be unique and ordered")
        return value


class TerminalDisplayConfiguration(StrictModel):
    theme: Literal["auto", "dark", "light"] = "auto"
    mode: Literal["concise", "detailed"] = "concise"
    show_status: bool = True
    max_tool_result_lines: int = Field(default=5, ge=1, le=200)
    max_tool_argument_chars: int = Field(default=8192, ge=128, le=65536)


class ToolsConfiguration(StrictModel):
    """Application-owned built-in tool switches and terminal question waiting policy."""

    enable_user_input: bool = True
    user_input_timeout_seconds: float = Field(default=120.0, gt=0, allow_inf_nan=False)
    enable_codeact: bool = True


class SubagentsConfiguration(StrictModel):
    """Named release-owned children automatically included in the root roster."""

    include: tuple[BuiltinSubagentName, ...] = ()

    @field_validator("include")
    @classmethod
    def _unique_names(cls, value: tuple[BuiltinSubagentName, ...]) -> tuple[BuiltinSubagentName, ...]:
        if len(value) != len(set(value)):
            raise ValueError("included built-in subagent names must be unique and ordered")
        return value


class HarnessUiDocument(StrictModel):
    """Root ``a13n-harness-ui.yaml`` document."""

    schema_version: Literal["1"] = "1"
    process: ProcessConfiguration = Field(default_factory=ProcessConfiguration)
    defaults: GlobalDefaults = Field(default_factory=GlobalDefaults)
    display: TerminalDisplayConfiguration = Field(default_factory=TerminalDisplayConfiguration)
    tools: ToolsConfiguration = Field(default_factory=ToolsConfiguration)
    subagents: SubagentsConfiguration = Field(default_factory=SubagentsConfiguration)


class EnvironmentVariableSource(StrictModel):
    env: str = Field(min_length=1, max_length=256)

    @field_validator("env")
    @classmethod
    def _valid_environment_name(cls, value: str) -> str:
        if not _ENV_NAME.fullmatch(value):
            raise ValueError("env must be a valid environment variable name")
        return value


class ApiKeyAuthentication(StrictModel):
    kind: Literal["api_key"]
    env: str | None = Field(default=None, min_length=1, max_length=256)
    credential_ref: ResourceId | None = None

    @field_validator("env")
    @classmethod
    def _valid_environment_name(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_NAME.fullmatch(value):
            raise ValueError("env must be a valid environment variable name")
        return value

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.env is None) == (self.credential_ref is None):
            raise ValueError("API key authentication requires exactly one of env or credential_ref")
        return self


class CodexSubscriptionAuthentication(StrictModel):
    kind: Literal["codex_subscription"]


class GrokSubscriptionAuthentication(StrictModel):
    kind: Literal["grok_subscription"]


type ModelAuthentication = Annotated[
    ApiKeyAuthentication | CodexSubscriptionAuthentication | GrokSubscriptionAuthentication,
    Field(discriminator="kind"),
]


def _native_model_characteristics(value: object) -> object:
    # Parent document normalizers turn JSON arrays into Python values before
    # nested validation. Preserve native JSON semantics for its frozenset field.
    if isinstance(value, dict):
        return HarnessModelCharacteristics.model_validate_json(json.dumps(value), strict=True)
    return value


type ModelCharacteristics = Annotated[HarnessModelCharacteristics, BeforeValidator(_native_model_characteristics)]


class ModelResource(StrictModel):
    schema_version: Literal["1"]
    kind: Literal["model"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    route: str = Field(min_length=3, max_length=512)
    authentication: ModelAuthentication
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    model_configuration: dict[str, JsonValue] = Field(default_factory=dict)
    model_characteristics: ModelCharacteristics | None = None

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "model-")
        _validate_json_mapping(self.settings)
        _validate_json_mapping(self.model_configuration)
        return self


class HarnessPluginResource(StrictModel):
    schema_version: Literal["1"]
    kind: Literal["harness_plugin"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    plugin_key: CatalogKey
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "plugin-")
        _validate_json_mapping(self.configuration)
        return self


class EnvironmentProfileResource(StrictModel):
    schema_version: Literal["1"]
    kind: Literal["environment_profile"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    provider_key: CatalogKey
    provider_schema_version: str = Field(min_length=1, max_length=64)
    provider_configuration: dict[str, JsonValue] = Field(default_factory=dict)
    adapter_key: CatalogKey
    adapter_configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "environment-")
        if built_in_environment_profile(self.id) is not None:
            raise ValueError("release-owned Environment profile IDs cannot be redefined")
        _validate_json_mapping(self.provider_configuration)
        _validate_json_mapping(self.adapter_configuration)
        return self


class EnvironmentRunExtensionResource(StrictModel):
    schema_version: Literal["1"]
    kind: Literal["environment_run_extension"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    extension_key: CatalogKey
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "extension-")
        _validate_json_mapping(self.configuration)
        return self


type ExtensionResource = Annotated[
    HarnessPluginResource | EnvironmentProfileResource | EnvironmentRunExtensionResource,
    Field(discriminator="kind"),
]


class McpFileValueSource(StrictModel):
    """Internal locator for a string in an exact user-owned MCP source file."""

    model_config = ConfigDict(str_strip_whitespace=False)

    file: str = Field(pattern=r"^mcp/[^/\\\\]+\.(yaml|json)$")
    source_digest: SourceDigest
    path: tuple[str, ...] = Field(min_length=1, max_length=8)


type McpValueSource = EnvironmentVariableSource | McpFileValueSource


class McpCommandTransport(StrictModel):
    command: str = Field(min_length=1, max_length=1024)
    arguments: tuple[str, ...] = Field(default=(), max_length=256)
    environment: dict[str, McpValueSource] = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def _bounded_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any("\x00" in item or len(item) > 16 * 1024 for item in value):
            raise ValueError("MCP command arguments must be bounded and contain no NUL")
        return value

    @field_validator("environment")
    @classmethod
    def _valid_environment_keys(cls, value: dict[str, McpValueSource]) -> dict[str, McpValueSource]:
        if any(not _ENV_NAME.fullmatch(name) for name in value):
            raise ValueError("MCP environment keys must be valid environment variable names")
        return value


class McpRemoteTransport(StrictModel):
    url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, McpValueSource] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _safe_remote(self) -> Self:
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("MCP URL must be a credential-free HTTP(S) URL")
        query_names = {unquote_plus(part.split("=", 1)[0]).lower() for part in parsed.query.split("&") if part}
        if query_names & _SECRET_FIELD_NAMES:
            raise ValueError("MCP URL query parameters must not contain credentials")
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme == "http" and (not loopback or self.headers):
            raise ValueError("plain HTTP MCP is limited to header-free loopback URLs")
        return self


type McpTransport = McpCommandTransport | McpRemoteTransport


class McpServerResource(StrictModel):
    schema_version: Literal["1"]
    kind: Literal["mcp_server"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    transport: McpTransport

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "mcp-")
        return self


class CapabilitySelection(StrictModel):
    capability: CatalogKey
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_configuration(self) -> Self:
        _validate_json_mapping(self.configuration)
        return self


class MarkdownSubagentSelection(StrictModel):
    markdown: ResourceId

    @field_validator("markdown")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        _require_id_prefix(value, "subagent-")
        return value


class AgentSubagentSelection(StrictModel):
    agent: ResourceId

    @field_validator("agent")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        _require_id_prefix(value, "agent-")
        return value


type SubagentSelection = MarkdownSubagentSelection | AgentSubagentSelection


class AgentResource(StrictModel):
    schema_version: Literal["1"]
    kind: Literal["agent"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    model: ResourceId | None = None
    instructions: str = Field(default="", max_length=1024 * 1024)
    capabilities: tuple[CapabilitySelection, ...] = Field(default=(), max_length=128)
    harness_plugins: tuple[ResourceId, ...] | None = None
    mcp_servers: tuple[ResourceId, ...] | None = None
    tools: tuple[ToolName, ...] | None = None
    subagents: tuple[SubagentSelection, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "agent-")
        if self.model is not None:
            _require_id_prefix(self.model, "model-")
        for values in (self.harness_plugins, self.mcp_servers, self.tools):
            if values is not None and len(values) != len(set(values)):
                raise ValueError("Agent selections must be unique and ordered")
        capability_names = tuple(item.capability for item in self.capabilities)
        if len(capability_names) != len(set(capability_names)):
            raise ValueError("Agent Capability selections must be unique")
        return self


class ProjectRoot(StrictModel):
    path: str = Field(min_length=1, max_length=4096)

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Project root contains NUL")
        expanded = Path(value).expanduser()
        if not expanded.is_absolute():
            raise ValueError("Project roots must be absolute")
        return str(expanded.resolve(strict=False))


class ProjectResource(StrictModel):
    schema_version: Literal["1"]
    kind: Literal["project"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    position: int = Field(default=0)
    roots: tuple[ProjectRoot, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "project-")
        paths = tuple(item.path for item in self.roots)
        if len(paths) != len(set(paths)):
            raise ValueError("Project roots must be unique and ordered")
        for path in paths:
            candidate = Path(path)
            if not candidate.exists() or not candidate.is_dir():
                raise ValueError(f"Project root is not an existing directory: {path}")
        return self


class CanonicalSubagent(StrictModel):
    """Normalized canonical Markdown child resource."""

    id: ResourceId
    name: RosterName
    description: str = Field(min_length=1, max_length=4096)
    instruction: str | None = Field(default=None, min_length=1, max_length=16 * 1024)
    # Retained generations used to serialize this field. New Markdown rejects it;
    # keep decoding the historical value so admitted child Runs retain their model.
    model: ResourceId | None = Field(default=None, exclude_if=lambda value: value is None)
    tools: tuple[ToolName, ...] | None = None
    body: str = Field(default="", max_length=1024 * 1024)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        _require_id_prefix(value, "subagent-")
        return value

    @field_validator("tools", mode="before")
    @classmethod
    def _normalize_tool_list(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            value = tuple(item.strip() for item in value.split(",") if item.strip())
        elif isinstance(value, list):
            value = tuple(value)
        if isinstance(value, tuple):
            return tuple(dict.fromkeys(value))
        return value


class SourceDocument(StrictModel):
    relative_path: str = Field(min_length=1, max_length=4096)
    source_digest: SourceDigest
    resource_kind: str
    resource_id: ResourceId | None = None
    resource_ids: tuple[ResourceId, ...] = Field(default=(), exclude_if=lambda value: not value)
    content: str = Field(max_length=1024 * 1024)

    @property
    def indexed_resource_ids(self) -> tuple[str, ...]:
        return (self.resource_id,) if self.resource_id is not None else self.resource_ids


class LoadedHarnessUiConfiguration(StrictModel):
    """One complete graph-valid configuration-tree generation."""

    document: HarnessUiDocument
    root_digest: SourceDigest
    source_digest: SourceDigest
    sources: tuple[SourceDocument, ...]
    content_plugins: tuple[InstalledContentPlugin, ...] = Field(default=(), max_length=256)
    content_plugin_diagnostics: tuple[str, ...] = ()
    models: dict[ResourceId, ModelResource] = Field(default_factory=dict)
    harness_plugins: dict[ResourceId, HarnessPluginResource] = Field(default_factory=dict)
    environment_profiles: dict[ResourceId, EnvironmentProfileResource] = Field(default_factory=dict)
    environment_run_extensions: dict[ResourceId, EnvironmentRunExtensionResource] = Field(default_factory=dict)
    mcp_servers: dict[ResourceId, McpServerResource] = Field(default_factory=dict)
    agents: dict[ResourceId, AgentResource] = Field(default_factory=dict)
    subagents: dict[ResourceId, CanonicalSubagent] = Field(default_factory=dict)
    projects: dict[ResourceId, ProjectResource] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_graph(self) -> Self:
        defaults = self.document.defaults
        _require_reference(defaults.project, self.projects, "defaults.project")
        _require_reference(defaults.agent, self.agents, "defaults.agent")
        if (
            defaults.environment_profile is not None
            and built_in_environment_profile(defaults.environment_profile) is None
        ):
            _require_reference(defaults.environment_profile, self.environment_profiles, "defaults.environment_profile")
        for item in defaults.harness_plugins:
            _require_reference(item, self.harness_plugins, "defaults.harness_plugins")
        for item in defaults.environment_run_extensions:
            _require_reference(item, self.environment_run_extensions, "defaults.environment_run_extensions")
        for item in defaults.mcp_servers:
            _require_reference(item, self.mcp_servers, "defaults.mcp_servers")

        for agent in self.agents.values():
            if agent.model is not None:
                _require_reference(agent.model, self.models, f"{agent.id}.model")
            for item in agent.harness_plugins or ():
                _require_reference(item, self.harness_plugins, f"{agent.id}.harness_plugins")
            for item in agent.mcp_servers or ():
                _require_reference(item, self.mcp_servers, f"{agent.id}.mcp_servers")
            roster_names: list[str] = []
            for selection in agent.subagents:
                if isinstance(selection, AgentSubagentSelection):
                    child = self.agents.get(selection.agent)
                    if child is None:
                        raise ValueError(f"{agent.id} selects unknown Agent {selection.agent}")
                    roster_names.append(child.id)
            if len(roster_names) != len(set(roster_names)):
                raise ValueError(f"{agent.id} has duplicate immediate roster names")
        _reject_agent_cycles(self.agents)
        return self

    @property
    def global_guidance(self) -> tuple[str, ...]:
        """Global user-role AGENTS.md content from this accepted generation."""
        source = next(
            (
                item
                for item in self.sources
                if item.relative_path == "AGENTS.md" and item.resource_kind == "instructions"
            ),
            None,
        )
        if source is not None and source.content.strip():
            return (
                f"Global guidance from the Harness UI configuration directory (AGENTS.md):\n\n{source.content.strip()}",
            )
        return ()

    @property
    def yaml_digest(self) -> str:
        """Compatibility alias for the root source digest."""

        return self.root_digest

    def source(self, relative_path: str) -> SourceDocument:
        for item in self.sources:
            if item.relative_path == relative_path:
                return item
        raise KeyError(relative_path)

    def markdown(self, resource_id: str) -> CanonicalSubagent:
        return self.subagents[resource_id]

    def selected_plugins(self, agent: AgentResource) -> tuple[str, ...]:
        return self.document.defaults.harness_plugins if agent.harness_plugins is None else agent.harness_plugins

    def selected_mcp_servers(self, agent: AgentResource) -> tuple[str, ...]:
        return self.document.defaults.mcp_servers if agent.mcp_servers is None else agent.mcp_servers


def canonical_digest(value: BaseModel | JsonValue | object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _contains_tuple(annotation: object) -> bool:
    if get_origin(annotation) is tuple:
        return True
    return any(_contains_tuple(argument) for argument in get_args(annotation))


def _require_id_prefix(value: str, prefix: str) -> None:
    if not value.startswith(prefix):
        raise ValueError(f"resource ID must start with {prefix}")


def _require_reference(value: str | None, resources: Mapping[str, object], field: str) -> None:
    if value is not None and value not in resources:
        raise ValueError(f"{field} selects unknown resource {value}")


def _validate_json_mapping(value: dict[str, JsonValue]) -> None:
    _validate_json_value(value)


def _validate_json_value(value: JsonValue, *, field_name: str | None = None) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold() in _SECRET_FIELD_NAMES:
                raise ValueError(f"literal credential field {key!r} is forbidden")
            _validate_json_value(item, field_name=key)
    elif isinstance(value, list):
        for item in value:
            _validate_json_value(item, field_name=field_name)
    elif isinstance(value, float) and not (-float("inf") < value < float("inf")):
        raise ValueError("non-finite JSON values are forbidden")


def _reject_agent_cycles(agents: dict[str, AgentResource]) -> None:
    graph = {
        name: tuple(item.agent for item in agent.subagents if isinstance(item, AgentSubagentSelection))
        for name, agent in agents.items()
    }
    color: dict[str, int] = {}
    for start in graph:
        if color.get(start, 0):
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, index = stack[-1]
            if index == 0:
                color[node] = 1
                path.append(node)
            children = graph[node]
            if index < len(children):
                child = children[index]
                stack[-1] = (node, index + 1)
                state = color.get(child, 0)
                if state == 1:
                    raise ValueError(f"Agent graph contains a cycle through {child}")
                if state == 0:
                    stack.append((child, 0))
            else:
                stack.pop()
                path.pop()
                color[node] = 2


__all__ = [
    "AgentResource",
    "AgentSubagentSelection",
    "ApiKeyAuthentication",
    "CanonicalSubagent",
    "CapabilitySelection",
    "CodexSubscriptionAuthentication",
    "EnvironmentProfileResource",
    "EnvironmentRunExtensionResource",
    "EnvironmentVariableSource",
    "ExtensionResource",
    "GlobalDefaults",
    "GrokSubscriptionAuthentication",
    "HarnessPluginResource",
    "HarnessUiDocument",
    "LoadedHarnessUiConfiguration",
    "McpCommandTransport",
    "McpRemoteTransport",
    "McpServerResource",
    "McpTransport",
    "ModelAuthentication",
    "ModelResource",
    "ProcessConfiguration",
    "ProjectResource",
    "ProjectRoot",
    "SourceDocument",
    "SubagentSelection",
    "canonical_digest",
]
