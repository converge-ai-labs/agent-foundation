"""The built-in toolset catalogue, normalized selections of it, and the Harness configuration they select.

A stored selection is always complete: normalization materializes every toolset and tool of the catalogue
with its defaults and validates each typed config, so readers never merge defaults again.
"""

from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from a13n_harness.capabilities import FILE_TOOL_KEYS, RECORD_TOOL_KEYS, FileToolKey, RecordToolKey
from a13n_harness.capabilities.web import (
    WebConfiguration,
    WebDownloadConfiguration,
    WebFetchConfiguration,
    WebScrapeConfiguration,
    WebSearchConfiguration,
)
from a13n_harness.environment import DynamicEnvironmentConfiguration
from a13n_harness.providers.web.domains import DomainRestrictions
from a13n_harness.providers.web.options import MAX_SCRAPE_CONTENT_BYTES
from a13n_harness.tools import ToolPermissionMode, ToolPermissionSetting
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from a13n_service.infra.ids import ObjectId
from a13n_service.providers.registry import WebOperation

PUBLISH_ASSET_TOOL_ID = "service.publish_asset"
CONFIGURATION_TOOL_IDS = {
    "find": "service.configuration.find",
    "read": "service.configuration.read",
    "describe": "service.configuration.describe",
    "create_agent": "service.configuration.create_agent",
    "create_revision": "service.configuration.create_revision",
}

type ToolsetKey = Literal["files", "shell", "web", "memory", "assets", "configuration"]
ToolKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
JsonObject = dict[str, JsonValue]
SupportedPermission = Literal["inherit", "allow", "ask", "deny", "review"]
_PERMISSIONS: tuple[SupportedPermission, ...] = ("inherit", "allow", "ask", "deny", "review")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmptyConfiguration(_Frozen):
    pass


class SearchConfiguration(DomainRestrictions):
    """An enabled search requires `provider_id`; revision validation enforces it."""

    provider_id: ObjectId | None = None
    max_results: int = Field(default=5, ge=1, le=10)


class ScrapeConfiguration(DomainRestrictions):
    """An enabled scrape requires `provider_id`; revision validation enforces it."""

    provider_id: ObjectId | None = None
    max_content_bytes: int = Field(default=512 * 1024, ge=1, le=MAX_SCRAPE_CONTENT_BYTES)


class FetchConfiguration(DomainRestrictions):
    max_content_bytes: int = Field(default=256 * 1024, ge=1, le=256 * 1024)


class DownloadConfiguration(DomainRestrictions):
    pass


class ToolSelection(_Frozen):
    enabled: bool = True
    permission: ToolPermissionSetting = "inherit"
    config: JsonObject = Field(default_factory=dict)


class ToolsetSelection(_Frozen):
    enabled: bool = True
    config: JsonObject = Field(default_factory=dict)
    tools: dict[ToolKey, ToolSelection] = Field(default_factory=dict, max_length=64)


class ToolResourceSelector(_Frozen):
    """The resource a tool needs before it can be enabled."""

    kind: Literal["web_provider"]
    operation: WebOperation


class ToolDefinition(_Frozen):
    key: str
    display_name: str
    # The tool's permission identity: the key of its rule in the compiled tool permissions.
    execution_id: str
    model_name: str
    default_enabled: bool
    default_permission: ToolPermissionMode
    config_schema: dict[str, JsonValue]
    supported_permissions: tuple[SupportedPermission, ...]
    resource_selector: ToolResourceSelector | None
    # False when no registered web provider type serves the tool's operation.
    deployment_supported: bool


class ToolsetDefinition(_Frozen):
    key: ToolsetKey
    display_name: str
    default_enabled: bool
    config_schema: dict[str, JsonValue]
    tools: tuple[ToolDefinition, ...]


class ToolsetCatalog(_Frozen):
    items: tuple[ToolsetDefinition, ...]


@dataclass(frozen=True, slots=True)
class _Tool:
    key: str
    display_name: str
    execution_id: str
    model_name: str
    config: type[BaseModel] = EmptyConfiguration
    default_enabled: bool = True
    operation: WebOperation | None = None
    # What a selection starts with; `inherit` takes the agent's default, which allows.
    permission: ToolPermissionSetting = "inherit"

    def definition(self, supported_operations: frozenset[str]) -> ToolDefinition:
        return ToolDefinition(
            key=self.key,
            display_name=self.display_name,
            execution_id=self.execution_id,
            model_name=self.model_name,
            default_enabled=self.default_enabled,
            default_permission="allow" if self.permission == "inherit" else self.permission,
            config_schema=self.config.model_json_schema(),
            supported_permissions=_PERMISSIONS,
            resource_selector=None
            if self.operation is None
            else ToolResourceSelector(kind="web_provider", operation=self.operation),
            deployment_supported=self.operation is None or self.operation in supported_operations,
        )


@dataclass(frozen=True, slots=True)
class _Toolset:
    key: ToolsetKey
    display_name: str
    default_enabled: bool
    tools: tuple[_Tool, ...]

    def definition(self, supported_operations: frozenset[str]) -> ToolsetDefinition:
        return ToolsetDefinition(
            key=self.key,
            display_name=self.display_name,
            default_enabled=self.default_enabled,
            config_schema=EmptyConfiguration.model_json_schema(),
            tools=tuple(tool.definition(supported_operations) for tool in self.tools),
        )


_TOOLSETS: tuple[_Toolset, ...] = (
    _Toolset(
        "files",
        "Files",
        True,
        (
            _Tool("view", "View", "filesystem.view", "view"),
            _Tool("write", "Write", "filesystem.write", "write"),
            _Tool("edit", "Edit", "filesystem.edit", "edit"),
            _Tool("multi_edit", "Multi edit", "filesystem.multi_edit", "multi_edit"),
            _Tool("mkdir", "Create directories", "filesystem.mkdir", "mkdir"),
            _Tool("move", "Move", "filesystem.move", "move"),
            _Tool("copy", "Copy", "filesystem.copy", "copy"),
            _Tool("delete", "Delete", "filesystem.remove", "delete"),
            _Tool("ls", "List", "filesystem.ls", "ls"),
            _Tool("glob", "Find paths", "filesystem.glob", "glob"),
            _Tool("grep", "Search files", "filesystem.grep", "grep"),
        ),
    ),
    _Toolset(
        "shell",
        "Terminal",
        True,
        (
            _Tool("exec", "Execute", "environment.shell_exec", "shell_exec"),
            _Tool("info", "Inspect processes", "environment.process_info", "shell_info"),
            _Tool("wait", "Wait for process", "environment.process_wait", "shell_wait"),
            _Tool("input", "Send process input", "environment.process_input", "shell_input"),
            _Tool("signal", "Signal process", "environment.process_signal", "shell_signal"),
        ),
    ),
    _Toolset(
        "web",
        "Web",
        False,
        (
            _Tool("search", "Search", "web.search", "search", SearchConfiguration, False, "search"),
            _Tool("scrape", "Scrape", "web.scrape", "scrape", ScrapeConfiguration, False, "scrape"),
            _Tool("fetch", "Fetch", "web.fetch", "fetch", FetchConfiguration, False),
            _Tool("download", "Download", "web.download", "download", DownloadConfiguration, False),
        ),
    ),
    # The tools of the memories a run mounts; a read mount offers only viewing and searching.
    _Toolset(
        "memory",
        "Memory",
        True,
        (
            _Tool("file_view", "View memory files", "memory.file.view", "memory_file_view"),
            _Tool("file_grep", "Search memory files", "memory.file.grep", "memory_file_grep"),
            _Tool("file_create", "Create memory file", "memory.file.create", "memory_file_create"),
            _Tool("file_edit", "Edit memory file", "memory.file.edit", "memory_file_edit"),
            _Tool("file_append", "Append to memory file", "memory.file.append", "memory_file_append"),
            _Tool("file_move", "Move memory file", "memory.file.move", "memory_file_move"),
            _Tool("file_delete", "Delete memory file", "memory.file.delete", "memory_file_delete"),
            _Tool("record_search", "Search memory records", "memory.record.search", "memory_record_search"),
            _Tool("record_list", "List memory records", "memory.record.list", "memory_record_list"),
            _Tool("record_add", "Add memory record", "memory.record.add", "memory_record_add"),
            _Tool("record_update", "Update memory record", "memory.record.update", "memory_record_update"),
            _Tool("record_delete", "Delete memory record", "memory.record.delete", "memory_record_delete"),
        ),
    ),
    _Toolset(
        "assets",
        "Asset publication",
        False,
        (_Tool("publish", "Publish asset", PUBLISH_ASSET_TOOL_ID, "publish_asset"),),
    ),
    _Toolset(
        "configuration",
        "Agent configuration",
        False,
        (
            _Tool("find", "Find resources", CONFIGURATION_TOOL_IDS["find"], "find_resources"),
            _Tool("read", "Read resource", CONFIGURATION_TOOL_IDS["read"], "read_resource"),
            _Tool(
                "describe", "Describe agent configuration", CONFIGURATION_TOOL_IDS["describe"], "describe_agent_config"
            ),
            # Writes act with the run principal's authority, so each one waits for the user's approval.
            _Tool(
                "create_agent", "Create agent", CONFIGURATION_TOOL_IDS["create_agent"], "create_agent", permission="ask"
            ),
            _Tool(
                "create_revision",
                "Create agent revision",
                CONFIGURATION_TOOL_IDS["create_revision"],
                "create_agent_revision",
                permission="ask",
            ),
        ),
    ),
)
_BY_KEY = {toolset.key: toolset for toolset in _TOOLSETS}


def catalog(supported_web_operations: frozenset[str]) -> ToolsetCatalog:
    return ToolsetCatalog(items=tuple(toolset.definition(supported_web_operations) for toolset in _TOOLSETS))


def normalize_toolsets(value: dict[ToolsetKey, ToolsetSelection]) -> dict[ToolsetKey, ToolsetSelection]:
    """Every toolset of the catalogue with its defaults materialized and its typed config validated."""
    return {toolset.key: _normalize(toolset, value.get(toolset.key)) for toolset in _TOOLSETS}


def normalize_toolset_overrides(value: dict[ToolsetKey, ToolsetSelection]) -> dict[ToolsetKey, ToolsetSelection]:
    """Only the submitted toolsets, each a whole replacement; omitted toolsets keep the revision's."""
    return {key: _normalize(_BY_KEY[key], selected) for key, selected in value.items()}


def _normalize(toolset: _Toolset, selected: ToolsetSelection | None) -> ToolsetSelection:
    selected = selected or ToolsetSelection(enabled=toolset.default_enabled)
    EmptyConfiguration.model_validate(selected.config)
    unknown = selected.tools.keys() - {tool.key for tool in toolset.tools}
    if unknown:
        raise ValueError(f"unknown {toolset.key} tool {sorted(unknown)[0]!r}")
    tools: dict[str, ToolSelection] = {}
    for tool in toolset.tools:
        submitted = selected.tools.get(tool.key) or ToolSelection(
            enabled=tool.default_enabled, permission=tool.permission
        )
        config = tool.config.model_validate(submitted.config).model_dump(mode="json", exclude_none=True)
        tools[tool.key] = submitted.model_copy(update={"config": config})
    return ToolsetSelection(enabled=selected.enabled, tools=tools)


Toolsets = Annotated[dict[ToolsetKey, ToolsetSelection], AfterValidator(normalize_toolsets)]
ToolsetOverrides = Annotated[dict[ToolsetKey, ToolsetSelection], AfterValidator(normalize_toolset_overrides)]


def default_toolsets() -> dict[ToolsetKey, ToolsetSelection]:
    return normalize_toolsets({})


def enabled_tool(toolsets: dict[ToolsetKey, ToolsetSelection], toolset: ToolsetKey, tool: str) -> ToolSelection | None:
    """The tool's selection when both it and its toolset are enabled."""
    selected = toolsets[toolset]
    return selected.tools[tool] if selected.enabled and selected.tools[tool].enabled else None


def enabled_tools(toolsets: dict[ToolsetKey, ToolsetSelection], toolset: ToolsetKey) -> frozenset[str]:
    """The keys of the toolset's enabled tools; none when the toolset is disabled."""
    selected = toolsets[toolset]
    return frozenset(key for key, tool in selected.tools.items() if tool.enabled) if selected.enabled else frozenset()


def _enabled(toolsets: dict[ToolsetKey, ToolsetSelection]) -> list[tuple[_Tool, ToolSelection]]:
    return [
        (tool, selected)
        for toolset in _TOOLSETS
        for tool in toolset.tools
        if (selected := enabled_tool(toolsets, toolset.key, tool.key)) is not None
    ]


def model_names(toolsets: dict[ToolsetKey, ToolsetSelection]) -> frozenset[str]:
    """The names the model sees for the enabled built-in tools."""
    return frozenset(tool.model_name for tool, _ in _enabled(toolsets))


def permission_rules(toolsets: dict[ToolsetKey, ToolsetSelection]) -> dict[str, ToolPermissionSetting]:
    return {tool.execution_id: selected.permission for tool, selected in _enabled(toolsets)}


# The memory toolset's tool keys name each memory kind's tools by their Harness keys, such as `file_view`.
_FILE_TOOLS: dict[str, FileToolKey] = {f"file_{key}": key for key in FILE_TOOL_KEYS}
_RECORD_TOOLS: dict[str, RecordToolKey] = {f"record_{key}": key for key in RECORD_TOOL_KEYS}


def memory_file_tools(toolsets: dict[ToolsetKey, ToolsetSelection]) -> frozenset[FileToolKey]:
    """The enabled memory file tools as the file memory capability names them, such as `view`."""
    return frozenset(_FILE_TOOLS[key] for key in enabled_tools(toolsets, "memory") if key in _FILE_TOOLS)


def memory_record_tools(toolsets: dict[ToolsetKey, ToolsetSelection]) -> frozenset[RecordToolKey]:
    """The enabled memory record tools as the record memory capability names them, such as `search`."""
    return frozenset(_RECORD_TOOLS[key] for key in enabled_tools(toolsets, "memory") if key in _RECORD_TOOLS)


def environment_configuration(toolsets: dict[ToolsetKey, ToolsetSelection]) -> DynamicEnvironmentConfiguration:
    files, shell = toolsets["files"], toolsets["shell"]
    return DynamicEnvironmentConfiguration(
        files_enabled=files.enabled,
        shell_enabled=shell.enabled,
        file_tools=frozenset(tool.model_name for tool in _BY_KEY["files"].tools if files.tools[tool.key].enabled),
        shell_tools=frozenset(tool.model_name for tool in _BY_KEY["shell"].tools if shell.tools[tool.key].enabled),
    )


@dataclass(frozen=True, slots=True)
class WebTools:
    """The enabled web tools; search and scrape name the web provider serving them."""

    search: SearchConfiguration | None
    scrape: ScrapeConfiguration | None
    fetch: FetchConfiguration | None
    download: DownloadConfiguration | None


def web_tools(toolsets: dict[ToolsetKey, ToolsetSelection]) -> WebTools | None:
    """None when no web tool is enabled."""

    def configured[C: BaseModel](key: str, model: type[C]) -> C | None:
        selected = enabled_tool(toolsets, "web", key)
        return None if selected is None else model.model_validate(selected.config)

    tools = WebTools(
        search=configured("search", SearchConfiguration),
        scrape=configured("scrape", ScrapeConfiguration),
        fetch=configured("fetch", FetchConfiguration),
        download=configured("download", DownloadConfiguration),
    )
    return None if tools == WebTools(None, None, None, None) else tools


def web_configuration(tools: WebTools) -> WebConfiguration:
    """Search and scrape run on the host, through the backend whose ID is the selected provider's ID."""
    search, scrape, fetch, download = tools.search, tools.scrape, tools.fetch, tools.download
    return WebConfiguration(
        search=WebSearchConfiguration(mode="off")
        if search is None
        else WebSearchConfiguration(mode="host", backend=search.provider_id, **_domains(search)),
        scrape=WebScrapeConfiguration(mode="off")
        if scrape is None
        else WebScrapeConfiguration(mode="host", backend=scrape.provider_id, **_domains(scrape)),
        fetch=WebFetchConfiguration(enabled=fetch is not None, **_domains(fetch)),
        download=WebDownloadConfiguration(enabled=download is not None, **_domains(download)),
        max_search_results=10 if search is None else search.max_results,
        max_scrape_bytes=512 * 1024 if scrape is None else scrape.max_content_bytes,
        max_text_bytes=256 * 1024 if fetch is None else fetch.max_content_bytes,
        deadline_seconds=30,
    )


class _Domains(TypedDict, total=False):
    allow_domains: tuple[str, ...]
    deny_domains: tuple[str, ...]


def _domains(restrictions: DomainRestrictions | None) -> _Domains:
    if restrictions is None:
        return {}
    return {"allow_domains": restrictions.allow_domains, "deny_domains": restrictions.deny_domains}
