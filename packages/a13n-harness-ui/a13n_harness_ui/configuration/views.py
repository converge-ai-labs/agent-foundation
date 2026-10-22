"""Detached views of accepted configuration sources, never raw credential stores."""

from pathlib import Path
from typing import Literal

from a13n_harness.tools.exposure import ToolsetExposure
from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.errors import ConfigurationError

from .exposure import CAPABILITY_TOOLSETS, source_exposure
from .models import AgentResource, LoadedHarnessUiConfiguration
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


class ToolExposureSourceView(ToolsetExposure):
    """Source declarations only; counts and tools are discovered during a Run."""

    resource_id: str
    name: str
    kind: Literal["capability", "mcp_server", "harness_plugin"]
    enabled: bool
    toolset_ids: tuple[str, ...] = ()


class AgentToolExposureView(BaseModel):
    """Static presentation declarations; never connects to MCP or enumerates tools."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    agent_id: str
    progressive_search: Literal["harness", "native"]
    progressive_search_strategy: Literal["bm25", "regex"] | None
    sources: tuple[ToolExposureSourceView, ...]


class ConfigurationSourceView(ConfigurationSourceInfo):
    generation_digest: str
    content: str | None


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
            )
    raise ConfigurationError("The accepted configuration source does not exist.", code="configuration_source_not_found")


def agent_tool_exposure_view(
    source: LoadedHarnessUiConfiguration,
    agent: AgentResource,
    *,
    mcp_server_ids: tuple[str, ...] | None = None,
    harness_plugin_ids: tuple[str, ...] | None = None,
) -> AgentToolExposureView:
    selected = {
        *(source.selected_mcp_servers(agent) if mcp_server_ids is None else mcp_server_ids),
        *(source.selected_plugins(agent) if harness_plugin_ids is None else harness_plugin_ids),
    }
    sources = [
        ToolExposureSourceView(
            resource_id=item.capability,
            name=item.capability,
            kind="capability",
            enabled=True,
            toolset_ids=CAPABILITY_TOOLSETS.get(item.capability, ()),
            **source_exposure(item).model_dump(),
        )
        for item in agent.capabilities
    ]
    for resource in (*source.mcp_servers.values(), *source.harness_plugins.values()):
        sources.append(
            ToolExposureSourceView(
                resource_id=resource.id,
                name=resource.name,
                kind=resource.kind,
                enabled=resource.id in selected,
                toolset_ids=(resource.id,) if resource.kind == "mcp_server" else (),
                **source_exposure(resource).model_dump(),
            )
        )
    return AgentToolExposureView(
        agent_id=agent.id,
        progressive_search=agent.progressive_search,
        progressive_search_strategy=agent.progressive_search_strategy,
        sources=tuple(sources),
    )
