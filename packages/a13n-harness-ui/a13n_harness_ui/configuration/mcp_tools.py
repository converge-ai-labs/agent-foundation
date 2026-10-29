"""Secret-free MCP tool settings and explicit discovery for configuration editors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from anyio import fail_after
from fastmcp import Client
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.mcp_adapters import prepare_mcp_transport

from .loader import _parse_mcp_resources, _read_bounded_stable
from .models import LoadedHarnessUiConfiguration, McpServerResource
from .mutation import ResourceMutationRequest, _select_target


class McpToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tools: list[str] | None = Field(max_length=1024)


class McpToolSettings(McpToolSelection):
    server_id: str
    name: str


class McpToolDescriptor(BaseModel):
    name: str
    description: str
    input_schema: dict[str, JsonValue]


def server_resource(source: LoadedHarnessUiConfiguration | None, server_id: str) -> McpServerResource:
    if source is None or server_id not in source.mcp_servers:
        raise ConfigurationError("The MCP server is unavailable.", code="configuration_resource_missing")
    return source.mcp_servers[server_id]


def tool_settings(source: LoadedHarnessUiConfiguration | None, server_id: str) -> McpToolSettings:
    resource = server_resource(source, server_id)
    assert source is not None
    document = next(item for item in source.sources if server_id in item.indexed_resource_ids)
    return McpToolSettings(
        server_id=server_id,
        name=resource.name,
        source_digest=document.source_digest,
        tools=list(resource.tools) if resource.tools is not None else None,
    )


def selection_mutation(
    configuration_path: Path, source: LoadedHarnessUiConfiguration, server_id: str, request: McpToolSelection
) -> tuple[str, ResourceMutationRequest]:
    """Patch only the selection, preserving hidden credentials and sibling servers."""
    settings = tool_settings(source, server_id)
    document = next(item for item in source.sources if server_id in item.indexed_resource_ids)
    _, _, target = _select_target(configuration_path, document.relative_path, False)
    content, _ = _read_bounded_stable(target, 1024 * 1024)
    if settings.source_digest != request.source_digest or hashlib.sha256(content).hexdigest() != request.source_digest:
        raise ConfigurationError(
            "The MCP configuration changed. Reload its settings before saving.", code="configuration_source_changed"
        )
    # Validate the source before parsing it; parser exceptions must not expose credentials.
    resources = _parse_mcp_resources(target, content)
    raw = json.loads(content) if target.suffix == ".json" else yaml.safe_load(content)
    if "mcpServers" in raw:
        key = list(raw["mcpServers"])[next(i for i, resource in enumerate(resources) if resource.id == server_id)]
        selected = raw["mcpServers"][key]
    else:
        selected = raw
    raw["schema_version"] = "2"
    selected["tools"] = request.tools
    updated = json.dumps(raw, indent=2) + "\n" if target.suffix == ".json" else yaml.safe_dump(raw, sort_keys=False)
    return document.relative_path, ResourceMutationRequest(content=updated)


async def discover_tools(
    source: LoadedHarnessUiConfiguration, server_id: str, configuration_root: Path
) -> tuple[McpToolDescriptor, ...]:
    resource = server_resource(source, server_id)
    recipe = ResolvedMcpRecipe(
        server_id=server_id,
        transport=resource.transport,
        protocol=source.document.mcp.protocol_overrides.get(server_id, "auto"),
    )
    try:
        with fail_after(30):
            transport, _, timeout = await prepare_mcp_transport(
                recipe, configuration_root, configuration=source.document.run_configuration
            )
            async with Client(transport, mode=recipe.protocol, init_timeout=timeout) as client:
                discovered = await client.list_tools()
        return tuple(
            McpToolDescriptor(name=tool.name, description=tool.description or "", input_schema=tool.input_schema)
            for tool in discovered
        )
    except Exception:
        # MCP/transport errors may contain headers, URLs or server-controlled text.
        raise ConfigurationError(
            "Could not load tools. Check the saved server connection and try again.", code="mcp_discovery_failed"
        ) from None
