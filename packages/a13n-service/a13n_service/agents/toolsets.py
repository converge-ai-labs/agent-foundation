"""Code-defined built-in Toolset catalog and normalized authored selections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from a13n_harness.environment import DynamicEnvironmentConfiguration
from a13n_harness.tools import ToolIdentity, ToolPermissionMode, ToolPermissions, ToolPermissionSetting
from a13n_harness.toolsets.domains import DomainRestrictions
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from a13n_service.ids import ObjectId
from a13n_service.web.domain import (
    DownloadSelection,
    FetchSelection,
    ScrapeSelection,
    SearchSelection,
    WebSelection,
)

ToolsetKey = Literal["files", "shell", "web", "assets"]
ToolKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
JsonObject = dict[str, JsonValue]
SupportedPermission = Literal["allow", "ask", "deny", "review", "auto"]
_ALL_PERMISSIONS: tuple[SupportedPermission, ...] = ("allow", "ask", "deny", "review", "auto")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmptyToolConfiguration(_StrictModel):
    pass


class SearchToolConfiguration(DomainRestrictions):
    """Structurally valid draft; an enabled search requires ``provider_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ObjectId | None = None
    max_results: int = Field(default=5, ge=1, le=10)


class ScrapeToolConfiguration(DomainRestrictions):
    """Structurally valid draft; an enabled scrape requires ``provider_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ObjectId | None = None
    max_content_bytes: int = Field(default=512 * 1024, ge=1, le=4 * 1024 * 1024)


class ToolSelection(_StrictModel):
    enabled: bool = True
    permission: ToolPermissionSetting = "auto"
    config: JsonObject = Field(default_factory=dict)


class ToolsetSelection(_StrictModel):
    enabled: bool = True
    config: JsonObject = Field(default_factory=dict)
    tools: dict[ToolKey, ToolSelection] = Field(default_factory=dict, max_length=64)


class ToolResourceSelector(_StrictModel):
    kind: Literal["web_provider"]
    operation: Literal["search", "scrape"]


class ToolDefinition(_StrictModel):
    key: ToolKey
    display_name: str = Field(min_length=1, max_length=128)
    execution_id: str = Field(min_length=1, max_length=1024)
    model_name: str = Field(min_length=1, max_length=256)
    default_enabled: bool
    default_permission: ToolPermissionMode
    config_schema: dict[str, object]
    supported_permissions: tuple[SupportedPermission, ...]
    resource_selector: ToolResourceSelector | None = None
    deployment_supported: bool = True


class ToolsetDefinition(_StrictModel):
    key: ToolsetKey
    display_name: str = Field(min_length=1, max_length=128)
    default_enabled: bool
    config_schema: dict[str, object]
    tools: tuple[ToolDefinition, ...]


class ToolsetCatalog(_StrictModel):
    items: tuple[ToolsetDefinition, ...]


@dataclass(frozen=True, slots=True)
class _Tool:
    key: str
    display_name: str
    execution_id: str
    model_name: str
    config_model: type[BaseModel]
    default_enabled: bool = True
    default_permission: ToolPermissionMode = "review"
    resource_selector: ToolResourceSelector | None = None

    def public(self, *, deployment_supported: bool = True) -> ToolDefinition:
        return ToolDefinition(
            key=self.key,
            display_name=self.display_name,
            execution_id=self.execution_id,
            model_name=self.model_name,
            default_enabled=self.default_enabled,
            default_permission=self.default_permission,
            config_schema=self.config_model.model_json_schema(),
            supported_permissions=_ALL_PERMISSIONS,
            resource_selector=self.resource_selector,
            deployment_supported=deployment_supported,
        )


@dataclass(frozen=True, slots=True)
class _Toolset:
    key: ToolsetKey
    display_name: str
    default_enabled: bool
    tools: tuple[_Tool, ...]
    config_model: type[BaseModel] = EmptyToolConfiguration

    def public(self, *, supported_operations: frozenset[str]) -> ToolsetDefinition:
        return ToolsetDefinition(
            key=self.key,
            display_name=self.display_name,
            default_enabled=self.default_enabled,
            config_schema=self.config_model.model_json_schema(),
            tools=tuple(
                tool.public(
                    deployment_supported=(
                        tool.resource_selector is None or tool.resource_selector.operation in supported_operations
                    )
                )
                for tool in self.tools
            ),
        )


_TOOLSETS: tuple[_Toolset, ...] = (
    _Toolset(
        "files",
        "Files",
        True,
        (
            _Tool("view", "View", "filesystem.view", "view", EmptyToolConfiguration),
            _Tool("write", "Write", "filesystem.write", "write", EmptyToolConfiguration),
            _Tool("edit", "Edit", "filesystem.edit", "edit", EmptyToolConfiguration),
            _Tool("multi_edit", "Multi edit", "filesystem.multi_edit", "multi_edit", EmptyToolConfiguration),
            _Tool("mkdir", "Create directories", "filesystem.mkdir", "mkdir", EmptyToolConfiguration),
            _Tool("move", "Move", "filesystem.move", "move", EmptyToolConfiguration),
            _Tool("copy", "Copy", "filesystem.copy", "copy", EmptyToolConfiguration),
            _Tool("delete", "Delete", "filesystem.remove", "delete", EmptyToolConfiguration),
            _Tool("ls", "List", "filesystem.ls", "ls", EmptyToolConfiguration),
            _Tool("glob", "Find paths", "filesystem.glob", "glob", EmptyToolConfiguration),
            _Tool("grep", "Search files", "filesystem.grep", "grep", EmptyToolConfiguration),
        ),
    ),
    _Toolset(
        "shell",
        "Terminal",
        True,
        (
            _Tool("exec", "Execute", "environment.shell_exec", "shell_exec", EmptyToolConfiguration),
            _Tool("info", "Inspect processes", "environment.process_info", "shell_info", EmptyToolConfiguration),
            _Tool("wait", "Wait for process", "environment.process_wait", "shell_wait", EmptyToolConfiguration),
            _Tool("input", "Send process input", "environment.process_input", "shell_input", EmptyToolConfiguration),
            _Tool("signal", "Signal process", "environment.process_signal", "shell_signal", EmptyToolConfiguration),
        ),
    ),
    _Toolset(
        "web",
        "Web",
        False,
        (
            _Tool(
                "search",
                "Search",
                "web.search",
                "search",
                SearchToolConfiguration,
                default_enabled=False,
                resource_selector=ToolResourceSelector(kind="web_provider", operation="search"),
            ),
            _Tool(
                "scrape",
                "Scrape",
                "web.scrape",
                "scrape",
                ScrapeToolConfiguration,
                default_enabled=False,
                resource_selector=ToolResourceSelector(kind="web_provider", operation="scrape"),
            ),
            _Tool("fetch", "Fetch", "web.fetch", "fetch", FetchSelection, default_enabled=False),
            _Tool("download", "Download", "web.download", "download", DownloadSelection, default_enabled=False),
        ),
    ),
    _Toolset(
        "assets",
        "Asset publication",
        False,
        (_Tool("publish", "Publish asset", "service.publish_asset", "publish_asset", EmptyToolConfiguration),),
    ),
)
_BY_KEY = {toolset.key: toolset for toolset in _TOOLSETS}


def _validate_catalog() -> None:
    if len(_BY_KEY) != len(_TOOLSETS):
        raise RuntimeError("duplicate built-in Toolset key")
    tools = tuple(tool for toolset in _TOOLSETS for tool in toolset.tools)
    for label, values in (
        ("execution ID", tuple(tool.execution_id for tool in tools)),
        ("model-visible name", tuple(tool.model_name for tool in tools)),
    ):
        if len(values) != len(set(values)):
            raise RuntimeError(f"duplicate built-in Tool {label}")
    for toolset in _TOOLSETS:
        keys = tuple(tool.key for tool in toolset.tools)
        if len(keys) != len(set(keys)):
            raise RuntimeError(f"duplicate {toolset.key} Tool key")


_validate_catalog()


def default_toolsets() -> dict[ToolsetKey, ToolsetSelection]:
    return normalize_toolsets({})


def normalize_toolsets(value: dict[ToolsetKey, ToolsetSelection]) -> dict[ToolsetKey, ToolsetSelection]:
    """Materialize every definition-owned default and validate all typed config."""

    return {definition.key: _normalize_toolset(definition, value.get(definition.key)) for definition in _TOOLSETS}


def normalize_toolset_overrides(
    value: dict[ToolsetKey, ToolsetSelection],
) -> dict[ToolsetKey, ToolsetSelection]:
    """Normalize only submitted whole-entry replacements; omitted entries inherit."""

    return {key: _normalize_toolset(_BY_KEY[key], selected) for key, selected in value.items()}


def _normalize_toolset(definition: _Toolset, selected: ToolsetSelection | None) -> ToolsetSelection:
    enabled = definition.default_enabled if selected is None else selected.enabled
    group_config = definition.config_model.model_validate({} if selected is None else selected.config)
    submitted_tools = {} if selected is None else selected.tools
    known = {tool.key for tool in definition.tools}
    unknown = submitted_tools.keys() - known
    if unknown:
        raise ValueError(f"unknown {definition.key} Tool key {sorted(unknown)[0]!r}")
    tools: dict[ToolKey, ToolSelection] = {}
    for tool in definition.tools:
        submitted = submitted_tools.get(tool.key)
        tool_config = tool.config_model.model_validate({} if submitted is None else submitted.config)
        tools[tool.key] = ToolSelection(
            enabled=tool.default_enabled if submitted is None else submitted.enabled,
            permission="auto" if submitted is None else submitted.permission,
            config=tool_config.model_dump(mode="json", exclude_none=True),
        )
    return ToolsetSelection(
        enabled=enabled,
        config=group_config.model_dump(mode="json", exclude_none=True),
        tools=tools,
    )


Toolsets = Annotated[dict[ToolsetKey, ToolsetSelection], AfterValidator(normalize_toolsets)]
ToolsetOverrides = Annotated[dict[ToolsetKey, ToolsetSelection], AfterValidator(normalize_toolset_overrides)]


def catalog(*, supported_web_operations: frozenset[str] = frozenset({"search", "scrape"})) -> ToolsetCatalog:
    return ToolsetCatalog(items=tuple(item.public(supported_operations=supported_web_operations) for item in _TOOLSETS))


def enabled_tool(toolsets: Toolsets, toolset_key: ToolsetKey, tool_key: str) -> ToolSelection | None:
    toolset = toolsets[toolset_key]
    tool = toolset.tools[tool_key]
    return tool if toolset.enabled and tool.enabled else None


def requires_reviewer(toolsets: Toolsets) -> bool:
    return any(
        selected.enabled and tool.enabled and _effective_permission(definition_tool, tool.permission) == "review"
        for definition in _TOOLSETS
        for definition_tool in definition.tools
        for selected in (toolsets[definition.key],)
        for tool in (selected.tools[definition_tool.key],)
    )


def _effective_permission(tool: _Tool, setting: ToolPermissionSetting) -> ToolPermissionMode:
    return ToolPermissions(default=setting).resolve(ToolIdentity(tool.execution_id, tool.default_permission))


def builtin_permission_rules(toolsets: Toolsets) -> dict[str, ToolPermissionSetting]:
    rules: dict[str, ToolPermissionSetting] = {}
    for definition in _TOOLSETS:
        selected = toolsets[definition.key]
        if not selected.enabled:
            continue
        for tool in definition.tools:
            configured = selected.tools[tool.key]
            if configured.enabled:
                rules[tool.execution_id] = configured.permission
    return rules


def active_model_names(toolsets: Toolsets) -> frozenset[str]:
    return frozenset(
        tool.model_name
        for definition in _TOOLSETS
        if toolsets[definition.key].enabled
        for tool in definition.tools
        if toolsets[definition.key].tools[tool.key].enabled
    )


def environment_configuration(toolsets: Toolsets) -> DynamicEnvironmentConfiguration:
    files = toolsets["files"]
    shell = toolsets["shell"]
    return DynamicEnvironmentConfiguration(
        files_enabled=files.enabled,
        shell_enabled=shell.enabled,
        file_tools=frozenset(tool.model_name for tool in _BY_KEY["files"].tools if files.tools[tool.key].enabled),
        shell_tools=frozenset(tool.model_name for tool in _BY_KEY["shell"].tools if shell.tools[tool.key].enabled),
    )


def web_selection(toolsets: Toolsets) -> WebSelection | None:
    search = enabled_tool(toolsets, "web", "search")
    scrape = enabled_tool(toolsets, "web", "scrape")
    fetch = enabled_tool(toolsets, "web", "fetch")
    download = enabled_tool(toolsets, "web", "download")
    if not any((search, scrape, fetch, download)):
        return None
    return WebSelection(
        search=SearchSelection.model_validate(search.config) if search is not None else None,
        scrape=ScrapeSelection.model_validate(scrape.config) if scrape is not None else None,
        fetch=FetchSelection.model_validate(fetch.config) if fetch is not None else None,
        download=DownloadSelection.model_validate(download.config) if download is not None else None,
    )


__all__ = [
    "ToolDefinition",
    "ToolResourceSelector",
    "ToolSelection",
    "ToolsetCatalog",
    "ToolsetDefinition",
    "ToolsetKey",
    "ToolsetOverrides",
    "ToolsetSelection",
    "Toolsets",
    "active_model_names",
    "builtin_permission_rules",
    "catalog",
    "default_toolsets",
    "enabled_tool",
    "environment_configuration",
    "normalize_toolsets",
    "requires_reviewer",
    "web_selection",
]
