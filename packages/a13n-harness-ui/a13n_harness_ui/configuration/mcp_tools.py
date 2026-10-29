"""Secret-free MCP tool settings and explicit discovery for configuration editors."""

from __future__ import annotations

import hashlib
import json
from io import StringIO
from pathlib import Path

import yaml
from anyio import fail_after
from fastmcp import Client
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from ruamel.yaml import YAML

from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.mcp_adapters import prepare_mcp_transport

from .loader import _parse_mcp_resources, _read_bounded_stable
from .models import LoadedHarnessUiConfiguration, McpServerResource, McpToolName, SourceDocument
from .mutation import ResourceMutationRequest, _select_target


class McpToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resource_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tools: list[McpToolName] | None = Field(max_length=1024)


class McpToolSettings(McpToolSelection):
    server_id: str
    name: str


class McpToolDescriptor(BaseModel):
    name: str
    description: str
    input_schema: dict[str, JsonValue]


def _server_resource(
    source: LoadedHarnessUiConfiguration | None, server_id: str
) -> tuple[LoadedHarnessUiConfiguration, McpServerResource]:
    if source is None or server_id not in source.mcp_servers:
        raise ConfigurationError("The MCP server is unavailable.", code="configuration_resource_missing")
    return source, source.mcp_servers[server_id]


def _server_document(
    configuration_path: Path, source: LoadedHarnessUiConfiguration | None, server_id: str
) -> tuple[SourceDocument, McpServerResource, Path, bytes, dict, str | None]:
    source, resource = _server_resource(source, server_id)
    document = next(item for item in source.sources if server_id in item.indexed_resource_ids)
    _, _, target = _select_target(configuration_path, document.relative_path, False)
    content, _ = _read_bounded_stable(target, 1024 * 1024)
    # Validate before parsing; parser errors must not expose credentials.
    resources = _parse_mcp_resources(target, content)
    raw = json.loads(content) if target.suffix == ".json" else yaml.safe_load(content)
    key = None
    if "mcpServers" in raw:
        key = next(
            (name for name, item in zip(raw["mcpServers"], resources, strict=True) if item.id == server_id), None
        )
        if key is None:
            raise ConfigurationError(
                "The MCP server changed. Reload its settings.", code="configuration_source_changed"
            )
    elif raw.get("id") != server_id:
        raise ConfigurationError("The MCP server changed. Reload its settings.", code="configuration_source_changed")
    return document, resource, target, content, raw, key


def _resource_digest(raw: dict, key: str | None) -> str:
    # File formatting, sibling edits and the shared schema marker do not change
    # this server's policy or connection. Credentials participate only in the hash.
    selected = dict(raw["mcpServers"][key] if key is not None else raw)
    selected.pop("schema_version", None)
    return hashlib.sha256(json.dumps(selected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def tool_settings(
    configuration_path: Path, source: LoadedHarnessUiConfiguration | None, server_id: str
) -> McpToolSettings:
    document, resource, _, content, raw, key = _server_document(configuration_path, source, server_id)
    if hashlib.sha256(content).hexdigest() != document.source_digest:
        raise ConfigurationError(
            "The MCP configuration changed. Reload its settings.", code="configuration_source_changed"
        )
    return McpToolSettings(
        server_id=server_id,
        name=resource.name,
        resource_digest=_resource_digest(raw, key),
        tools=list(resource.tools) if resource.tools is not None else None,
    )


def selection_mutation(
    configuration_path: Path, source: LoadedHarnessUiConfiguration | None, server_id: str, request: McpToolSelection
) -> tuple[str, ResourceMutationRequest]:
    """Patch one server, preserving credentials, comments and sibling selections."""
    document, _, target, content, raw, key = _server_document(configuration_path, source, server_id)
    if _resource_digest(raw, key) != request.resource_digest:
        raise ConfigurationError(
            "The MCP server changed. Reload its settings before saving.", code="configuration_source_changed"
        )
    selected = raw["mcpServers"][key] if key is not None else raw
    if selected.get("tools") == request.tools:
        return document.relative_path, ResourceMutationRequest(content=content.decode())
    formatter = YAML()
    formatter.preserve_quotes = True
    if target.suffix != ".json":
        raw = formatter.load(content.decode())
        selected = raw["mcpServers"][key] if key is not None else raw

    if request.tools is None:
        selected.pop("tools", None)
    else:
        selected["tools"] = request.tools
        raw["schema_version"] = "2"
    if target.suffix == ".json":
        updated = json.dumps(raw, indent=2) + "\n"
    else:
        output = StringIO()
        formatter.dump(raw, output)
        updated = output.getvalue()
    return document.relative_path, ResourceMutationRequest(content=updated)


async def discover_tools(
    source: LoadedHarnessUiConfiguration | None, server_id: str, configuration_root: Path
) -> tuple[McpToolDescriptor, ...]:
    source, resource = _server_resource(source, server_id)
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
