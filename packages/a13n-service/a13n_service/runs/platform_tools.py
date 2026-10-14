"""Attempt-owned native MCP tools, with Host credentials and Harness write review."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

import httpx2
from a13n_harness import AgentContext
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity, source_tool_id
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset, ToolsetTool, WrapperToolset

from a13n_service.infra.platform import PLATFORM_METADATA, PlatformOperation
from a13n_service.providers.tools.mcp import AttemptToolset
from a13n_service.resources.agents.schemas import PlatformSelection
from a13n_service.runs.attempts import Lease
from a13n_service.runs.platform_tokens import issue
from a13n_service.runs.runtime import Runtime


@dataclass
class PlatformToolset(WrapperToolset[AgentContext]):
    selection: PlatformSelection

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        result = {}
        for name, tool in (await self.wrapped.get_tools(ctx)).items():
            metadata = dict(tool.tool_def.metadata or {})
            operation = PlatformOperation.model_validate((metadata.get("meta") or {})[PLATFORM_METADATA])
            if operation.group not in self.selection.groups:
                continue
            mode = self.selection.read_permission if operation.effect == "read" else self.selection.write_permission
            metadata[TOOL_IDENTITY_KEY] = ToolIdentity(source_tool_id("platform", name, kind="mcp"), mode)
            result[name] = replace(tool, tool_def=replace(tool.tool_def, metadata=metadata))
        return result


@asynccontextmanager
async def platform_capability(
    runtime: Runtime, lease: Lease, selection: PlatformSelection
) -> AsyncIterator[MCP[AgentContext]]:
    """Refresh before sending, never refresh-and-replay a request whose outcome may be unknown."""

    async def credentials(request: httpx2.Request) -> None:
        request.headers["authorization"] = "Bearer " + await issue(runtime, lease, selection.groups)

    server = runtime.settings.server
    url = (server.control_url or server.public_url).rstrip("/") + "/api/v1/mcp/"
    http = httpx2.AsyncClient(
        event_hooks={"request": [credentials]},
        headers={"host": urlsplit(server.public_url).netloc},
        follow_redirects=False,
        timeout=60,
        trust_env=False,
    )
    native = MCPToolset(
        url,
        id="platform",
        http_client=http,
        max_retries=0,
        tool_error_behavior="failed",
        prefer_tasks=False,
        include_instructions=False,
    )
    try:
        # Native MCP enters HTTP once. Each Harness Run borrows a fresh projection of this entered client.
        async with native.client as client:

            async def local(_ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
                tools = AttemptToolset(
                    client,
                    id="platform",
                    max_retries=0,
                    tool_error_behavior="failed",
                    prefer_tasks=False,
                    include_instructions=False,
                    cache_tools=False,
                    cache_resources=False,
                    cache_prompts=False,
                )
                return PlatformToolset(tools, selection)

            yield MCP(id="platform", local=DynamicToolset(local, per_run_step=False))
    finally:
        await http.aclose()
