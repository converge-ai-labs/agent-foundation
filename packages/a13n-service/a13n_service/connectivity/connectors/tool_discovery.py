"""Shared bounded validation for provider tool previews and connection discovery."""

from a13n_harness.providers.connector.contracts import ConnectorTool, ToolCatalog
from anyio import fail_after, to_thread
from mcp.types import Tool, ToolAnnotations

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.bounds import DISCOVERY_MAX_BYTES, DISCOVERY_MAX_PAGES, DISCOVERY_MAX_TOOLS
from a13n_service.connectivity.tool_validation import validate_tools

from .errors import ConnectorError


def mcp_tool(tool: ConnectorTool) -> Tool:
    return Tool(
        name=tool.key,
        description=tool.description,
        input_schema=tool.input_schema,
        output_schema=tool.output_schema,
        annotations=ToolAnnotations.model_validate(tool.annotations),
    )


async def discover_tools(runtime: ToolCatalog) -> tuple[tuple[ConnectorTool, ...], str]:
    cursor: str | None = None
    tools: list[ConnectorTool] = []
    provider_version: str | None = None
    seen_cursors: set[str] = set()
    names: set[str] = set()
    size = 0
    with fail_after(30):
        for _ in range(DISCOVERY_MAX_PAGES):
            page = await runtime.discover_tools(cursor=cursor)
            if provider_version is None:
                provider_version = page.provider_version
            elif page.provider_version != provider_version:
                raise ConnectorError(
                    "discovery_incompatible", "Tools changed during discovery.", category=ErrorCategory.conflict
                )
            tools.extend(page.items)
            if len(tools) > DISCOVERY_MAX_TOOLS:
                raise ConnectorError("discovery_too_large", "Too many tools.", category=ErrorCategory.conflict)
            definitions = tuple(mcp_tool(tool) for tool in page.items)
            size += await to_thread.run_sync(validate_tools, definitions)
            if size > DISCOVERY_MAX_BYTES or names.intersection(tool.name for tool in definitions):
                raise ConnectorError(
                    "discovery_incompatible", "Invalid tool discovery.", category=ErrorCategory.conflict
                )
            names.update(tool.name for tool in definitions)
            cursor = page.next_cursor
            if cursor is None:
                return tuple(tools), provider_version
            if cursor in seen_cursors:
                raise ConnectorError(
                    "discovery_incompatible", "Invalid discovery cursor.", category=ErrorCategory.conflict
                )
            seen_cursors.add(cursor)
    raise ConnectorError("discovery_too_large", "Too many discovery pages.", category=ErrorCategory.conflict)
