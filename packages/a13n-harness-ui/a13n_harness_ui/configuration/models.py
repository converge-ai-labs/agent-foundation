"""Configuration documents with additive fields and strict known contracts."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self, get_args, get_origin
from urllib.parse import unquote_plus, urlsplit

from a13n_harness.capabilities import ToolProxyConfig
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    RemoteEnvdStateData,
    WebSocketEnvdConnectionConfiguration,
)
from a13n_harness.spec import HarnessModelCharacteristics
from a13n_harness.tools.tool_proxy import validate_group
from a13n_harness.toolsets.file_media import NativeInputMediaKind
from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from a13n_harness_ui.content_plugins import InstalledContentPlugin
from a13n_harness_ui.environment_bindings import (
    EnvironmentBindingSelection,
    validate_binding_aliases,
    validate_environment_selection,
)
from a13n_harness_ui.environment_profiles import built_in_environment_profile
from a13n_harness_ui.mcp_apps.origins import origin
from a13n_harness_ui.settings import DEFAULT_MAX_OBJECT_BYTES, ObjectSizeLimit
from a13n_harness_ui.subagents import BuiltinSubagentName

MEDIA_KINDS: tuple[NativeInputMediaKind, ...] = ("image", "video", "audio")

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

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)

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


class ConfigurationModel(StrictModel):
    """Retain additive JSON fields without interpreting them as execution policy."""

    model_config = ConfigDict(extra="allow")
    # Pydantic uses this narrower annotation to validate retained JSON extras.
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)  # pyright: ignore[reportIncompatibleVariableOverride]


class ProcessConfiguration(ConfigurationModel):
    max_object_bytes: ObjectSizeLimit = DEFAULT_MAX_OBJECT_BYTES
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


class GlobalDefaults(ConfigurationModel):
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


class TerminalDisplayConfiguration(ConfigurationModel):
    theme: Literal["auto", "dark", "light"] = "auto"
    mode: Literal["concise", "detailed"] = "concise"
    show_status: bool = True
    max_tool_result_lines: int = Field(default=5, ge=1, le=200)
    max_tool_argument_chars: int = Field(default=8192, ge=128, le=65536)


class ToolsConfiguration(ConfigurationModel):
    """Application-owned tool switches and uniform interactive waiting policy."""

    enable_ask_user_question: bool = True
    interaction_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices("interaction_timeout_seconds", "ask_user_question_timeout_seconds"),
    )
    enable_codeact: bool = True

    @model_validator(mode="before")
    @classmethod
    def _no_obsolete_question_policy(cls, value: object) -> object:
        if isinstance(value, dict) and {"enable_user_input", "user_input_timeout_seconds"} & value.keys():
            raise ValueError("Obsolete question settings must be replaced with ask_user_question settings")
        return value


class SubagentsConfiguration(ConfigurationModel):
    """Named release-owned children automatically included in the root roster."""

    include: tuple[BuiltinSubagentName, ...] = ()

    @field_validator("include")
    @classmethod
    def _unique_names(cls, value: tuple[BuiltinSubagentName, ...]) -> tuple[BuiltinSubagentName, ...]:
        if len(value) != len(set(value)):
            raise ValueError("included built-in subagent names must be unique and ordered")
        return value


class InputConfiguration(ConfigurationModel):
    """User-text delivery policy captured for each root Run."""

    long_text_threshold_chars: int | None = Field(default=8000, ge=1)


class ShellReviewConfiguration(ConfigurationModel):
    """Optional Host shortcut; disabled leaves Agent capability policy untouched."""

    enable: bool = False
    risk_threshold: Literal["low", "medium", "high", "extra_high"] | None = None
    model: ResourceId | None = None
    on_flagged: Literal["deny", "approval_required"] | None = None
    on_error: Literal["deny", "approval_required", "allow"] | None = None

    @field_validator("model")
    @classmethod
    def _model_id(cls, value: str | None) -> str | None:
        if value is not None:
            _require_id_prefix(value, "model-")
        return value


class SecurityConfiguration(ConfigurationModel):
    shell_review: ShellReviewConfiguration = Field(default_factory=ShellReviewConfiguration)


class SidekickConfiguration(ConfigurationModel):
    """Instruction-guided collaboration; omitted Agent inherits the calling Agent."""

    agent: ResourceId | None = None
    model: ResourceId | None = None


class McpAppsSandboxConfiguration(ConfigurationModel):
    bind: str = "127.0.0.1"
    port: int = Field(default=0, ge=0, le=65535)
    public_url: str | None = None

    @field_validator("bind")
    @classmethod
    def _bind_address(cls, value: str) -> str:
        return str(ipaddress.ip_address(value))

    @field_validator("public_url")
    @classmethod
    def _public_origin(cls, value: str | None) -> str | None:
        return None if value is None else origin(value)


class McpAppsConfiguration(ConfigurationModel):
    """Opt-in UI behavior for already selected MCP servers; never a server selection."""

    enabled: bool = False
    servers: tuple[ResourceId, ...] = Field(default=(), max_length=128)
    sandbox: McpAppsSandboxConfiguration = Field(default_factory=McpAppsSandboxConfiguration)


class WebUiConfiguration(ConfigurationModel):
    sidekick: SidekickConfiguration | None = Field(default_factory=SidekickConfiguration)
    mcp_apps: McpAppsConfiguration = Field(default_factory=McpAppsConfiguration)


class MediaUnderstandingConfiguration(ConfigurationModel):
    """Application defaults for native-first file-media understanding."""

    image: ResourceId | None = None
    video: ResourceId | None = None
    audio: ResourceId | None = None

    @field_validator("image", "video", "audio")
    @classmethod
    def _model_id(cls, value: str | None) -> str | None:
        if value is not None:
            _require_id_prefix(value, "model-")
        return value

    def selections(self) -> dict[NativeInputMediaKind, str]:
        values: dict[NativeInputMediaKind, str | None] = {
            "image": self.image,
            "video": self.video,
            "audio": self.audio,
        }
        return {kind: value for kind, value in values.items() if value is not None}


class MemoryOrganizationConfiguration(ConfigurationModel):
    """WebUI-only maintenance with an optional Model override."""

    enabled: bool = True
    model: ResourceId | None = None
    instructions: str = Field(default="", max_length=64 * 1024)

    @field_validator("model")
    @classmethod
    def _model_id(cls, value: str | None) -> str | None:
        if value is not None:
            _require_id_prefix(value, "model-")
        return value


class MemoryConfiguration(ConfigurationModel):
    """Shared file memory and independently configurable organization."""

    enabled: bool = True
    auto_organize: MemoryOrganizationConfiguration = Field(default_factory=MemoryOrganizationConfiguration)


class HarnessUiDocument(ConfigurationModel):
    """Root ``a13n-harness-ui.yaml`` document."""

    schema_version: Literal["1"] = "1"
    process: ProcessConfiguration = Field(default_factory=ProcessConfiguration)
    input: InputConfiguration = Field(default_factory=InputConfiguration)
    defaults: GlobalDefaults = Field(default_factory=GlobalDefaults)
    display: TerminalDisplayConfiguration = Field(default_factory=TerminalDisplayConfiguration)
    tools: ToolsConfiguration = Field(default_factory=ToolsConfiguration)
    security: SecurityConfiguration = Field(default_factory=SecurityConfiguration)
    subagents: SubagentsConfiguration = Field(default_factory=SubagentsConfiguration)
    webui: WebUiConfiguration = Field(default_factory=WebUiConfiguration)
    media_understanding: MediaUnderstandingConfiguration = Field(
        default_factory=MediaUnderstandingConfiguration, exclude_if=lambda value: not value.selections()
    )
    memory: MemoryConfiguration = Field(default_factory=MemoryConfiguration)
    max_goal_iterations: int = 10


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


class CopilotSubscriptionAuthentication(StrictModel):
    kind: Literal["copilot_subscription"]


type ModelAuthentication = Annotated[
    ApiKeyAuthentication
    | CodexSubscriptionAuthentication
    | GrokSubscriptionAuthentication
    | CopilotSubscriptionAuthentication,
    Field(discriminator="kind"),
]


def _native_model_characteristics(value: object) -> object:
    # Parent document normalizers turn JSON arrays into Python values before
    # nested validation. Preserve native JSON semantics for its frozenset field.
    if isinstance(value, dict):
        value = value.copy()
        if "context_window" in value:
            legacy = value.pop("context_window")
            if "context_window_tokens" in value and (
                type(value["context_window_tokens"]) is not type(legacy) or value["context_window_tokens"] != legacy
            ):
                raise ValueError("context_window and context_window_tokens must agree")
            value["context_window_tokens"] = legacy
        return HarnessModelCharacteristics.model_validate_json(json.dumps(value), strict=True)
    return value


type ModelCharacteristics = Annotated[HarnessModelCharacteristics, BeforeValidator(_native_model_characteristics)]


class ModelResource(ConfigurationModel):
    schema_version: Literal["1"]
    kind: Literal["model"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    route: str = Field(min_length=3, max_length=512)
    authentication: ModelAuthentication
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    model_configuration: dict[str, JsonValue] = Field(default_factory=dict)
    model_characteristics: ModelCharacteristics | None = None

    def media_capabilities(self) -> tuple[NativeInputMediaKind, ...]:
        capabilities = self.model_characteristics.capabilities if self.model_characteristics is not None else None
        return tuple(kind for kind in MEDIA_KINDS if f"{kind}_understanding" in (capabilities or ()))

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "model-")
        return self


class HttpDeviceTransport(StrictModel):
    kind: Literal["http"]
    configuration: HttpEnvdConnectionConfiguration


class WebSocketDeviceTransport(StrictModel):
    kind: Literal["websocket"]
    configuration: WebSocketEnvdConnectionConfiguration = Field(default_factory=WebSocketEnvdConnectionConfiguration)


type DeviceTransport = Annotated[HttpDeviceTransport | WebSocketDeviceTransport, Field(discriminator="kind")]


class PairedDeviceAuthentication(StrictModel):
    """Host-approved daemon credential; the original secret stays on the Device."""

    kind: Literal["paired"] = "paired"
    credential_digest: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    revoked: bool = False


type DeviceAuthentication = Annotated[ApiKeyAuthentication | PairedDeviceAuthentication, Field(discriminator="kind")]


class DeviceResource(StrictModel):
    """Credential references and connection recipe, never live Session state."""

    schema_version: Literal["1"]
    kind: Literal["device"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    device_id: str = Field(min_length=1, max_length=128)
    transport: DeviceTransport
    authentication: DeviceAuthentication

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "device-")
        RemoteEnvdStateData(device_id=self.device_id)
        if isinstance(self.authentication, PairedDeviceAuthentication) and not isinstance(
            self.transport, WebSocketDeviceTransport
        ):
            raise ValueError("Paired Device authentication requires WebSocket transport")
        return self


class HarnessPluginResource(ConfigurationModel):
    schema_version: Literal["1"]
    kind: Literal["harness_plugin"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    plugin_key: CatalogKey
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "plugin-")
        return self


class EnvironmentProfileResource(ConfigurationModel):
    schema_version: Literal["1"]
    kind: Literal["environment_profile"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    provider_key: CatalogKey
    provider_configuration: dict[str, JsonValue] = Field(default_factory=dict)
    adapter_key: CatalogKey
    adapter_configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "environment-")
        if built_in_environment_profile(self.id) is not None:
            raise ValueError("release-owned Environment profile IDs cannot be redefined")
        return self


class EnvironmentRunExtensionResource(ConfigurationModel):
    schema_version: Literal["1"]
    kind: Literal["environment_run_extension"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    extension_key: CatalogKey
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "extension-")
        return self


type ExtensionResource = Annotated[
    HarnessPluginResource | EnvironmentProfileResource | EnvironmentRunExtensionResource,
    Field(discriminator="kind"),
]


class McpFileValueSource(StrictModel):
    """Internal locator for a string in an exact user-owned MCP source file."""

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


class McpServerResource(ConfigurationModel):
    schema_version: Literal["1"]
    kind: Literal["mcp_server"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    transport: McpTransport

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "mcp-")
        return self


class CapabilitySelection(ConfigurationModel):
    capability: CatalogKey
    configuration: dict[str, JsonValue] = Field(default_factory=dict)


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


class _MissingModelReferenceError(ValueError):
    """A graph diagnostic containing only the authored reference and its location."""

    def __init__(self, *, path: str, field: str, model_id: str) -> None:
        self.path = path
        self.field = field
        self.model_id = model_id
        super().__init__(f"{path}: {field} references unavailable Model {model_id!r}.")


class _ToolProxyConfigurationError(ValueError):
    """Authored source/group diagnostic safe to expose without resource inputs."""


class AgentToolProxyGroup(ConfigurationModel):
    description: str = Field(min_length=1, max_length=512)
    mcp_servers: tuple[ResourceId, ...] = ()
    harness_plugins: tuple[ResourceId, ...] = ()


class AgentToolProxy(ConfigurationModel):
    groups: dict[str, AgentToolProxyGroup] = Field(default_factory=dict)
    config: ToolProxyConfig = Field(default_factory=ToolProxyConfig)

    @field_validator("config", mode="before")
    @classmethod
    def _parse_config(cls, value: object) -> object:
        if isinstance(value, dict):
            try:
                return ToolProxyConfig(**value)
            except (TypeError, ValueError) as exc:
                raise _ToolProxyConfigurationError(f"Invalid tool_proxy.config: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _valid_groups(self) -> Self:
        owners: dict[str, str] = {}
        for name, group in self.groups.items():
            try:
                validate_group(name, group.description)
            except ValueError as exc:
                raise _ToolProxyConfigurationError(f"Invalid tool_proxy group {name!r}: {exc}") from exc
            for source in (*group.mcp_servers, *group.harness_plugins):
                if source in owners:
                    raise _ToolProxyConfigurationError(
                        f"ToolProxy source {source!r} is selected more than once ({owners[source]!r}, {name!r})"
                    )
                owners[source] = name
        return self

    def selected(self, *, mcp_servers: tuple[str, ...], harness_plugins: tuple[str, ...]) -> AgentToolProxy:
        """Capture only enabled membership, without activating dormant references."""
        return AgentToolProxy(
            groups={
                name: AgentToolProxyGroup(
                    description=group.description,
                    mcp_servers=tuple(source for source in group.mcp_servers if source in mcp_servers),
                    harness_plugins=tuple(source for source in group.harness_plugins if source in harness_plugins),
                )
                for name, group in self.groups.items()
                if any(source in mcp_servers for source in group.mcp_servers)
                or any(source in harness_plugins for source in group.harness_plugins)
            },
            config=self.config,
        )


class AgentResource(ConfigurationModel):
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
    tool_proxy: AgentToolProxy | None = Field(default=None, exclude_if=lambda value: value is None)
    subagents: tuple[SubagentSelection, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "agent-")
        if self.model is not None:
            _require_id_prefix(self.model, "model-")
        for values in (self.harness_plugins, self.mcp_servers, self.tools):
            if values is not None and len(values) != len(set(values)):
                raise ValueError("Agent selections must be unique and ordered")
        # NativeTool registers one tool per entry; different native tools compose.
        capability_names = tuple(item.capability for item in self.capabilities if item.capability != "NativeTool")
        if len(capability_names) != len(set(capability_names)):
            raise ValueError("Agent Capability selections must be unique")
        return self


class ProjectRoot(ConfigurationModel):
    path: str = Field(min_length=1, max_length=4096)

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Project root contains NUL")
        if not Path(value).is_absolute():
            raise ValueError("Project roots must be absolute")
        # Decoding retained bytes must not consult the current Host filesystem.
        return value


class ProjectDefaults(ConfigurationModel):
    """One creation combination; omission falls back and empty lists select none."""

    agent: ResourceId | None = Field(default=None, exclude_if=lambda value: value is None)
    environment_profile: ResourceId | None = Field(default=None, exclude_if=lambda value: value is None)
    environment_bindings: tuple[EnvironmentBindingSelection, ...] | None = Field(
        default=None, max_length=64, exclude_if=lambda value: value is None
    )
    default_environment: str | None = Field(
        default=None, min_length=1, max_length=63, exclude_if=lambda value: value is None
    )
    harness_plugins: tuple[ResourceId, ...] | None = Field(default=None, exclude_if=lambda value: value is None)
    environment_run_extensions: tuple[ResourceId, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    mcp_servers: tuple[ResourceId, ...] | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="before")
    @classmethod
    def _no_recursive_project(cls, value: object) -> object:
        if isinstance(value, dict) and "project" in value:
            raise ValueError("Project defaults cannot select another Project")
        return value

    @model_validator(mode="after")
    def _valid_selections(self) -> Self:
        for name, values in (
            ("harness_plugins", self.harness_plugins),
            ("environment_run_extensions", self.environment_run_extensions),
            ("mcp_servers", self.mcp_servers),
        ):
            if values is not None and len(values) != len(set(values)):
                raise ValueError(f"Project defaults.{name} must be unique and ordered")
        validate_binding_aliases(self.environment_bindings or ())
        return self


class ProjectResource(ConfigurationModel):
    schema_version: Literal["1"]
    kind: Literal["project"]
    id: ResourceId
    name: str = Field(min_length=1, max_length=256)
    position: int = Field(default=0)
    roots: tuple[ProjectRoot, ...] = Field(default=(), max_length=64)
    defaults: ProjectDefaults = Field(default_factory=ProjectDefaults, exclude_if=lambda value: not value.model_dump())

    @model_validator(mode="after")
    def _valid_resource(self) -> Self:
        _require_id_prefix(self.id, "project-")
        paths = tuple(item.path for item in self.roots)
        if len(paths) != len(set(paths)):
            raise ValueError("Project roots must be unique and ordered")
        if not paths and not self.defaults.environment_bindings:
            raise ValueError("A Project requires local roots or Device Environment bindings")
        validate_environment_selection(
            self.defaults.environment_bindings or (), self.defaults.default_environment, local_root_count=len(paths)
        )
        return self


class CanonicalSubagent(ConfigurationModel):
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


class SourceDocument(ConfigurationModel):
    relative_path: str = Field(min_length=1, max_length=4096)
    source_digest: SourceDigest
    resource_kind: str
    resource_id: ResourceId | None = None
    resource_ids: tuple[ResourceId, ...] = Field(default=(), exclude_if=lambda value: not value)
    content: str = Field(max_length=1024 * 1024)

    @property
    def indexed_resource_ids(self) -> tuple[str, ...]:
        return (self.resource_id,) if self.resource_id is not None else self.resource_ids


class LoadedHarnessUiConfiguration(ConfigurationModel):
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
    devices: dict[ResourceId, DeviceResource] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_graph(self) -> Self:
        defaults = self.document.defaults
        _require_reference(self.document.memory.auto_organize.model, self.models, "memory.auto_organize.model")
        _require_reference(defaults.project, self.projects, "defaults.project")
        _require_reference(defaults.agent, self.agents, "defaults.agent")
        for kind, model_id in self.document.media_understanding.selections().items():
            field = f"media_understanding.{kind}"
            _require_reference(model_id, self.models, field)
            if kind not in self.models[model_id].media_capabilities():
                raise ValueError(f"{field} requires a Model declaring {kind}_understanding: {model_id}")
        sidekick = self.document.webui.sidekick
        if sidekick is not None:
            _require_reference(sidekick.agent, self.agents, "webui.sidekick.agent")
            _require_reference(sidekick.model, self.models, "webui.sidekick.model")
            if sidekick.agent is not None and sidekick.model is None and self.agents[sidekick.agent].model is None:
                raise ValueError("Sidekick requires an Agent Model or a webui.sidekick.model override")
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

        for project in self.projects.values():
            selected = project.defaults
            for binding in selected.environment_bindings or ():
                _require_reference(binding.device_id, self.devices, f"{project.id}.defaults.environment_bindings")
            _require_reference(selected.agent, self.agents, f"{project.id}.defaults.agent")
            if (
                selected.environment_profile is not None
                and built_in_environment_profile(selected.environment_profile) is None
            ):
                _require_reference(
                    selected.environment_profile,
                    self.environment_profiles,
                    f"{project.id}.defaults.environment_profile",
                )
            for values, resources, name in (
                (selected.harness_plugins, self.harness_plugins, "harness_plugins"),
                (selected.environment_run_extensions, self.environment_run_extensions, "environment_run_extensions"),
                (selected.mcp_servers, self.mcp_servers, "mcp_servers"),
            ):
                for item in values or ():
                    _require_reference(item, resources, f"{project.id}.defaults.{name}")

        for agent in self.agents.values():
            if agent.model is not None and agent.model not in self.models:
                path = next(
                    (item.relative_path for item in self.sources if agent.id in item.indexed_resource_ids),
                    agent.id,
                )
                raise _MissingModelReferenceError(path=path, field="model", model_id=agent.model)
            for item in agent.harness_plugins or ():
                _require_reference(item, self.harness_plugins, f"{agent.id}.harness_plugins")
            for item in agent.mcp_servers or ():
                _require_reference(item, self.mcp_servers, f"{agent.id}.mcp_servers")
            if agent.tool_proxy is not None:
                for name, group in agent.tool_proxy.groups.items():
                    for values, resources, kind in (
                        (group.mcp_servers, self.mcp_servers, "mcp_servers"),
                        (group.harness_plugins, self.harness_plugins, "harness_plugins"),
                    ):
                        for item in values:
                            if item not in resources:
                                raise _ToolProxyConfigurationError(
                                    f"{agent.id}.tool_proxy.groups.{name}.{kind} references unknown source {item!r}"
                                )
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

    def source(self, relative_path: str) -> SourceDocument:
        for item in self.sources:
            if item.relative_path == relative_path:
                return item
        raise KeyError(relative_path)

    def markdown(self, resource_id: str) -> CanonicalSubagent:
        return self.subagents[resource_id]

    @property
    def memory_organization_model_id(self) -> ResourceId | None:
        """Resolve organization against this generation's global Agent, never a Thread or Project."""
        override = self.document.memory.auto_organize.model
        if override is not None:
            return override
        agent_id = self.document.defaults.agent
        return None if agent_id is None else self.agents[agent_id].model

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
    "CopilotSubscriptionAuthentication",
    "EnvironmentProfileResource",
    "EnvironmentRunExtensionResource",
    "EnvironmentVariableSource",
    "ExtensionResource",
    "GlobalDefaults",
    "GrokSubscriptionAuthentication",
    "HarnessPluginResource",
    "HarnessUiDocument",
    "InputConfiguration",
    "LoadedHarnessUiConfiguration",
    "McpCommandTransport",
    "McpRemoteTransport",
    "McpServerResource",
    "McpTransport",
    "ModelAuthentication",
    "ModelResource",
    "ProcessConfiguration",
    "ProjectDefaults",
    "ProjectResource",
    "ProjectRoot",
    "SourceDocument",
    "SubagentSelection",
    "canonical_digest",
]
