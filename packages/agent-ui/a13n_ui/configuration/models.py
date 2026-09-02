"""Strict serialized configuration and canonical subagent contracts."""

from __future__ import annotations

import hashlib
import json
import re
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

from a13n_ui.settings import AgentUiSettings

type ConfigName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$"),
]
type CatalogKey = Annotated[
    str,
    Field(min_length=3, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
]
type ToolName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$"),
]
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
            origin = _collection_origin(field.annotation)
            if isinstance(raw, list) and origin is tuple:
                normalized[name] = tuple(raw)
        return normalized


def _collection_origin(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin is tuple:
        return origin
    for argument in get_args(annotation):
        nested = _collection_origin(argument)
        if nested is tuple:
            return nested
    return origin


class EnvironmentVariableSource(StrictModel):
    """A credential or runtime value resolved from the App process environment."""

    env: str = Field(min_length=1, max_length=256)

    @field_validator("env")
    @classmethod
    def _valid_environment_name(cls, value: str) -> str:
        if not _ENV_NAME.fullmatch(value):
            raise ValueError("env must be a valid environment variable name")
        return value


class ModelConfig(StrictModel):
    model: str = Field(min_length=1, max_length=512)
    api_key: EnvironmentVariableSource | None = None
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    model_cfg: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _credential_free_values(self) -> Self:
        _validate_json_mapping(self.settings)
        _validate_json_mapping(self.model_cfg)
        return self


class PluginConfig(StrictModel):
    plugin: CatalogKey
    enabled: bool = True
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _credential_free_configuration(self) -> Self:
        _validate_json_mapping(self.configuration)
        return self


class McpCommandTransport(StrictModel):
    command: str = Field(min_length=1, max_length=1024)
    arguments: tuple[str, ...] = Field(default=(), max_length=256)
    environment: dict[str, EnvironmentVariableSource] = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def _bounded_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any("\x00" in item or len(item) > 16 * 1024 for item in value):
            raise ValueError("MCP command arguments must be bounded and contain no NUL")
        return value

    @field_validator("environment")
    @classmethod
    def _valid_environment_keys(
        cls, value: dict[str, EnvironmentVariableSource]
    ) -> dict[str, EnvironmentVariableSource]:
        if any(not _ENV_NAME.fullmatch(name) for name in value):
            raise ValueError("MCP environment keys must be valid environment variable names")
        return value


class McpRemoteTransport(StrictModel):
    url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, EnvironmentVariableSource] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _credential_free_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("MCP URL must be a credential-free HTTP(S) URL")
        query_names = {unquote_plus(part.split("=", 1)[0]).lower() for part in parsed.query.split("&") if part}
        if query_names & _SECRET_FIELD_NAMES:
            raise ValueError("MCP URL query parameters must not contain credentials")
        return value


type McpTransport = McpCommandTransport | McpRemoteTransport


class McpServerConfig(StrictModel):
    enabled: bool = True
    transport: McpTransport


class MarkdownSubagentSelection(StrictModel):
    markdown: ConfigName


class AgentSubagentSelection(StrictModel):
    agent: ConfigName


type SubagentSelection = MarkdownSubagentSelection | AgentSubagentSelection


class AgentConfig(StrictModel):
    model: ConfigName
    instructions: str = Field(default="", max_length=1024 * 1024)
    plugins: tuple[ConfigName, ...] | None = None
    mcp_servers: tuple[ConfigName, ...] | None = None
    subagents: tuple[SubagentSelection, ...] = Field(default=(), max_length=256)
    environment: ConfigName | None = None

    @field_validator("plugins", "mcp_servers")
    @classmethod
    def _unique_selections(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("selection names must be unique")
        return value


class EnvironmentProfile(StrictModel):
    kind: Literal["native", "local_eip", "provider"]
    provider: ConfigName | None = None
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent_provider(self) -> Self:
        if self.kind == "provider" and self.provider is None:
            raise ValueError("provider Environment profiles require provider")
        if self.kind != "provider" and self.provider is not None:
            raise ValueError("built-in Environment profiles cannot select provider")
        _validate_json_mapping(self.configuration)
        return self


class EnvironmentProviderConfig(StrictModel):
    provider: CatalogKey
    binder: CatalogKey
    enabled: bool = True
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _credential_free_configuration(self) -> Self:
        _validate_json_mapping(self.configuration)
        return self


class AgentUiDefaults(StrictModel):
    agent: ConfigName | None = None
    environment: ConfigName | None = None


class AgentUiDocument(StrictModel):
    """The complete desired configuration loaded from one agent-ui.yaml."""

    schema_version: Literal["1"] = "1"
    process: AgentUiSettings = Field(default_factory=AgentUiSettings)
    defaults: AgentUiDefaults = Field(default_factory=AgentUiDefaults)
    models: dict[ConfigName, ModelConfig] = Field(default_factory=dict)
    plugins: dict[ConfigName, PluginConfig] = Field(default_factory=dict)
    mcp_servers: dict[ConfigName, McpServerConfig] = Field(default_factory=dict)
    agents: dict[ConfigName, AgentConfig] = Field(default_factory=dict)
    environments: dict[ConfigName, EnvironmentProfile] = Field(default_factory=dict)
    environment_providers: dict[ConfigName, EnvironmentProviderConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_references(self) -> Self:
        if self.defaults.agent is not None and self.defaults.agent not in self.agents:
            raise ValueError("defaults.agent selects an unknown Agent")
        if self.defaults.environment is not None and self.defaults.environment not in self.environments:
            raise ValueError("defaults.environment selects an unknown Environment profile")

        enabled_plugins = {name for name, item in self.plugins.items() if item.enabled}
        enabled_mcp = {name for name, item in self.mcp_servers.items() if item.enabled}
        enabled_providers = {name for name, item in self.environment_providers.items() if item.enabled}
        for name, agent in self.agents.items():
            if agent.model not in self.models:
                raise ValueError(f"Agent {name!r} selects an unknown Model")
            if agent.environment is not None and agent.environment not in self.environments:
                raise ValueError(f"Agent {name!r} selects an unknown Environment profile")
            if agent.plugins is not None and not set(agent.plugins) <= enabled_plugins:
                raise ValueError(f"Agent {name!r} selects an unknown or disabled Plugin")
            if agent.mcp_servers is not None and not set(agent.mcp_servers) <= enabled_mcp:
                raise ValueError(f"Agent {name!r} selects an unknown or disabled MCP server")
            for selection in agent.subagents:
                if isinstance(selection, AgentSubagentSelection) and selection.agent not in self.agents:
                    raise ValueError(f"Agent {name!r} selects an unknown reusable Agent")

        for name, profile in self.environments.items():
            if profile.kind == "provider" and profile.provider not in enabled_providers:
                raise ValueError(f"Environment profile {name!r} selects an unknown or disabled Provider")
        _reject_agent_cycles(self.agents)
        return self

    def selected_plugins(self, agent: AgentConfig) -> tuple[str, ...]:
        if agent.plugins is None:
            return tuple(sorted(name for name, item in self.plugins.items() if item.enabled))
        return agent.plugins

    def selected_mcp_servers(self, agent: AgentConfig) -> tuple[str, ...]:
        if agent.mcp_servers is None:
            return tuple(sorted(name for name, item in self.mcp_servers.items() if item.enabled))
        return agent.mcp_servers


class CanonicalSubagent(StrictModel):
    """Normalized canonical sibling Markdown behavior."""

    name: ConfigName
    description: str = Field(min_length=1, max_length=4096)
    instruction: str | None = Field(default=None, min_length=1, max_length=16 * 1024)
    model: ConfigName | None = None
    model_settings: dict[str, JsonValue] | None = None
    model_cfg: dict[str, JsonValue] | None = None
    tools: tuple[ToolName, ...] | None = None
    optional_tools: tuple[ToolName, ...] | None = None
    body: str = Field(default="", max_length=1024 * 1024)

    @field_validator("model", mode="before")
    @classmethod
    def _normalize_inherited_model(cls, value: object) -> object:
        if value == "inherit":
            return None
        return value

    @field_validator("tools", "optional_tools", mode="before")
    @classmethod
    def _normalize_tool_list(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            value = tuple(item.strip() for item in value.split(",") if item.strip())
        elif isinstance(value, list):
            value = tuple(value)
        if isinstance(value, tuple) and len(value) != len(set(value)):
            raise ValueError("tool names must be unique and ordered")
        return value

    @model_validator(mode="after")
    def _valid_overrides(self) -> Self:
        if self.model_settings is not None:
            _validate_json_mapping(self.model_settings)
        if self.model_cfg is not None:
            _validate_json_mapping(self.model_cfg)
        if self.tools is not None and self.optional_tools is not None:
            overlap = set(self.tools) & set(self.optional_tools)
            if overlap:
                raise ValueError("tools and optional_tools must not overlap")
        return self


class CanonicalSubagentSource(StrictModel):
    """One normalized Markdown definition plus safe file provenance."""

    document: CanonicalSubagent
    relative_path: str = Field(min_length=1, max_length=4096)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class LoadedAgentUiConfiguration(StrictModel):
    """One graph-valid source candidate awaiting trusted catalog resolution."""

    document: AgentUiDocument
    yaml_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    subagents: tuple[CanonicalSubagentSource, ...] = ()
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_complete_graph(self) -> Self:
        markdown = {item.document.name: item.document for item in self.subagents}
        if len(markdown) != len(self.subagents):
            raise ValueError("canonical Markdown child names must be unique")
        for child in markdown.values():
            if child.model is not None and child.model not in self.document.models:
                raise ValueError(f"Markdown child {child.name!r} selects an unknown Model")
        for name, agent in self.document.agents.items():
            child_names: list[str] = []
            for selection in agent.subagents:
                if isinstance(selection, MarkdownSubagentSelection):
                    child = markdown.get(selection.markdown)
                    if child is None:
                        raise ValueError(f"Agent {name!r} selects an unknown Markdown child")
                    child_names.append(child.name)
                else:
                    child_names.append(selection.agent)
            if len(child_names) != len(set(child_names)):
                raise ValueError(f"Agent {name!r} has duplicate final child names")
        return self

    def markdown(self, name: str) -> CanonicalSubagent:
        for source in self.subagents:
            if source.document.name == name:
                return source.document
        raise KeyError(name)


def canonical_digest(value: BaseModel | JsonValue) -> str:
    """Hash one finite normalized JSON value deterministically."""

    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_json_mapping(value: dict[str, JsonValue]) -> None:
    _validate_json_value(value)


def _validate_json_value(value: JsonValue, *, field_name: str | None = None) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in _SECRET_FIELD_NAMES:
                raise ValueError(f"literal credential field {key!r} is forbidden")
            _validate_json_value(item, field_name=key)
    elif isinstance(value, list):
        for item in value:
            _validate_json_value(item, field_name=field_name)
    elif isinstance(value, float) and not (-float("inf") < value < float("inf")):
        raise ValueError("configuration numbers must be finite")


def _reject_agent_cycles(agents: dict[str, AgentConfig]) -> None:
    colors: dict[str, int] = {}
    for root in agents:
        if colors.get(root) == 2:
            continue
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            name, leaving = stack.pop()
            if leaving:
                colors[name] = 2
                continue
            color = colors.get(name, 0)
            if color == 1:
                raise ValueError("reusable Agent references must not contain cycles")
            if color == 2:
                continue
            colors[name] = 1
            stack.append((name, True))
            children = [
                selection.agent for selection in agents[name].subagents if isinstance(selection, AgentSubagentSelection)
            ]
            for child in reversed(children):
                if colors.get(child) == 1:
                    raise ValueError("reusable Agent references must not contain cycles")
                if colors.get(child) != 2:
                    stack.append((child, False))


__all__ = [
    "AgentConfig",
    "AgentSubagentSelection",
    "AgentUiDefaults",
    "AgentUiDocument",
    "CanonicalSubagent",
    "CanonicalSubagentSource",
    "EnvironmentProfile",
    "EnvironmentProviderConfig",
    "EnvironmentVariableSource",
    "LoadedAgentUiConfiguration",
    "MarkdownSubagentSelection",
    "McpCommandTransport",
    "McpRemoteTransport",
    "McpServerConfig",
    "ModelConfig",
    "PluginConfig",
    "canonical_digest",
]
