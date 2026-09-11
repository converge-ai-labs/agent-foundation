"""Detached views of accepted configuration sources, never raw credential stores."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.errors import ConfigurationError

from .models import AgentResource, AgentToolProxy, LoadedHarnessUiConfiguration
from .mutation import ConfigurationMutationResult, _select_target


class ConfigurationSourceInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    relative_path: str
    source_digest: str
    resource_kind: str
    resource_ids: tuple[str, ...]
    writable: bool
    content_available: bool


class ConfigurationSourceCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    generation_digest: str | None
    sources: tuple[ConfigurationSourceInfo, ...]


class ToolProxySourceView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    resource_id: str
    name: str
    kind: Literal["mcp_server", "harness_plugin"]
    enabled: bool
    group: str | None
    presentation: Literal["active", "dormant", "direct", "disabled"]


class AgentToolProxyView(BaseModel):
    """Static source membership; never connects to MCP or discovers tools."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    agent_id: str
    tool_proxy: AgentToolProxy
    sources: tuple[ToolProxySourceView, ...]


class ConfigurationSourceView(ConfigurationSourceInfo):
    generation_digest: str
    content: str | None
    agent_tool_proxy: AgentToolProxyView | None = None


class ConfigurationValidation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    candidate_digest: str


class ConfigurationPublication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    action: Literal["created", "updated", "deleted", "unchanged"]
    relative_path: str
    source_digest: str | None
    generation_digest: str

    @classmethod
    def from_result(cls, result: ConfigurationMutationResult) -> "ConfigurationPublication":
        return cls(
            action=result.action,
            relative_path=result.relative_path,
            source_digest=result.source_digest,
            generation_digest=result.configuration.source_digest,
        )


def source_catalog(source: LoadedHarnessUiConfiguration | None, configuration_path: Path) -> ConfigurationSourceCatalog:
    if source is None:
        return ConfigurationSourceCatalog(generation_digest=None, sources=())
    entries: list[ConfigurationSourceInfo] = []
    for item in source.sources:
        try:
            _select_target(configuration_path, item.relative_path, False)
            writable = True
        except ConfigurationError:
            writable = False
        entries.append(
            ConfigurationSourceInfo(
                relative_path=item.relative_path,
                source_digest=item.source_digest,
                resource_kind=item.resource_kind,
                resource_ids=item.indexed_resource_ids,
                writable=writable,
                content_available=item.resource_kind != "mcp_server",
            )
        )
    return ConfigurationSourceCatalog(generation_digest=source.source_digest, sources=tuple(entries))


def source_view(
    source: LoadedHarnessUiConfiguration | None, configuration_path: Path, relative_path: str
) -> ConfigurationSourceView:
    catalog = source_catalog(source, configuration_path)
    for info in catalog.sources:
        if info.relative_path == relative_path:
            assert source is not None
            return ConfigurationSourceView(
                **info.model_dump(),
                generation_digest=source.source_digest,
                content=source.source(relative_path).content if info.content_available else None,
                agent_tool_proxy=(
                    agent_tool_proxy_view(source, source.agents[info.resource_ids[0]])
                    if info.resource_kind == "agent"
                    else None
                ),
            )
    raise ConfigurationError("The accepted configuration source does not exist.", code="configuration_source_not_found")


def agent_tool_proxy_view(source: LoadedHarnessUiConfiguration, agent: AgentResource) -> AgentToolProxyView:
    proxy = agent.tool_proxy or AgentToolProxy()
    owners = {
        resource_id: name
        for name, group in proxy.groups.items()
        for resource_id in (*group.mcp_servers, *group.harness_plugins)
    }
    selected = {*source.selected_mcp_servers(agent), *source.selected_plugins(agent)}
    sources = []
    for resource in (*source.mcp_servers.values(), *source.harness_plugins.values()):
        group = owners.get(resource.id)
        enabled = resource.id in selected
        sources.append(
            ToolProxySourceView(
                resource_id=resource.id,
                name=resource.name,
                kind=resource.kind,
                enabled=enabled,
                group=group,
                presentation=("active" if enabled else "dormant") if group else ("direct" if enabled else "disabled"),
            )
        )
    return AgentToolProxyView(agent_id=agent.id, tool_proxy=proxy, sources=tuple(sources))
