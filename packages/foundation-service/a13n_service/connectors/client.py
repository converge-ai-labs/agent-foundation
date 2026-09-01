"""Worker-side Connector MCP Client reconstruction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import urlparse

from a13n_harness import AgentContext, ContextualMCP
from a13n_harness.errors import DefinitionError
from a13n_harness.tools import HARNESS_TOOL_METADATA_KEY
from a13n_harness.tools.metadata import normalize_harness_tool_metadata
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP, AbstractCapability
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

from .domain import ConnectorTurnSelection
from .errors import ConnectorError


def build_connector_mcp_client(
    connector_service_base_url: str,
    *,
    connector_id: str,
    selection: ConnectorTurnSelection,
    capability_token: str,
) -> ContextualMCP:
    """Build one fresh local MCP capability for a fenced TurnAttempt."""

    parsed = urlparse(connector_service_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ConnectorError("Connector Service URL is invalid.", code="connector_service_unavailable")
    if not connector_id or "/" in connector_id:
        raise ConnectorError("Connector ID is invalid.", code="invalid_request")
    if not capability_token or len(capability_token) > 16_384:
        raise ConnectorError("Connector capability is invalid.", code="connector_capability_invalid")

    url = f"{connector_service_base_url.rstrip('/')}/internal/mcp/connectors/{connector_id}"

    def headers_factory(context: AgentContext) -> dict[str, str]:
        del context
        return {"Authorization": f"Bearer {capability_token}"}

    return _ConnectorContextualMCP(
        url,
        id=f"connector-{selection.declaration_index}-{connector_id}",
        headers_factory=headers_factory,
        native=False,
        local=True,
        allowed_tools=list(selection.effective_tools),
    )


class _ConnectorContextualMCP(ContextualMCP):
    """Promote metadata only from the trusted internal Connector Gateway."""

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        replacement = await super().for_run(ctx)
        if not isinstance(replacement, MCP):
            raise DefinitionError("Connector MCP replacement is invalid.", code="mcp_definition_invalid")
        if isinstance(replacement.local, _ConnectorManagedMetadataToolset):
            return replacement
        toolset = replacement.get_toolset()
        if toolset is None:
            raise DefinitionError("Connector MCP requires a local toolset.", code="mcp_definition_invalid")
        replacement.local = _ConnectorManagedMetadataToolset(toolset)
        replacement.allowed_tools = None
        return replacement


class _ConnectorManagedMetadataToolset(WrapperToolset[AgentContext]):
    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        projected: dict[str, ToolsetTool[AgentContext]] = {}
        for name, tool in tools.items():
            metadata = dict(tool.tool_def.metadata or {})
            mcp_meta = metadata.get("meta")
            raw_managed = mcp_meta.get(HARNESS_TOOL_METADATA_KEY) if isinstance(mcp_meta, Mapping) else None
            if raw_managed is None:
                raise DefinitionError(
                    "Connector MCP tool is missing managed metadata.",
                    code="tool_metadata_invalid",
                    details={"tool_name": name},
                )
            metadata[HARNESS_TOOL_METADATA_KEY] = normalize_harness_tool_metadata(raw_managed)
            projected[name] = replace(tool, tool_def=replace(tool.tool_def, metadata=metadata))
        return projected


__all__ = ["build_connector_mcp_client"]
