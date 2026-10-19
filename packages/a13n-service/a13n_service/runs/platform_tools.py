"""Attempt-owned native MCP tools, with Host credentials and Harness write review."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

import httpx2
from a13n_harness import AgentContext
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset, ToolsetTool, WrapperToolset

from a13n_service.infra.platform import PLATFORM_TOOLS, PlatformGroup
from a13n_service.providers.tools.mcp import AttemptToolset
from a13n_service.resources.agents.toolsets import PLATFORM_SOURCE
from a13n_service.runs.attempts import Lease
from a13n_service.runs.platform_tokens import issue
from a13n_service.runs.runtime import Runtime


@dataclass
class PlatformToolset(WrapperToolset[AgentContext]):
    """Only the enabled tools, each with the identity its permission rule and default apply to."""

    tools: Mapping[str, ToolIdentity]

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        result = {}
        for name, tool in (await self.wrapped.get_tools(ctx)).items():
            if name in self.tools:
                metadata = {**(tool.tool_def.metadata or {}), TOOL_IDENTITY_KEY: self.tools[name]}
                result[name] = replace(tool, tool_def=replace(tool.tool_def, metadata=metadata))
        return result


@asynccontextmanager
async def platform_capability(
    runtime: Runtime, lease: Lease, tools: Mapping[str, ToolIdentity]
) -> AsyncIterator[MCP[AgentContext]]:
    """Refresh before sending, never refresh-and-replay a request whose outcome may be unknown.

    The credential admits the groups of the enabled tools; the agent is offered only the enabled tools.
    """
    groups: frozenset[PlatformGroup] = frozenset(tool.group for tool in PLATFORM_TOOLS if tool.key in tools)

    async def credentials(request: httpx2.Request) -> None:
        request.headers["authorization"] = "Bearer " + await issue(runtime, lease, groups)

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
        id=PLATFORM_SOURCE,
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
                attempt = AttemptToolset(
                    client,
                    id=PLATFORM_SOURCE,
                    max_retries=0,
                    tool_error_behavior="failed",
                    prefer_tasks=False,
                    include_instructions=False,
                    cache_tools=False,
                    cache_resources=False,
                    cache_prompts=False,
                )
                return PlatformToolset(attempt, tools)

            yield MCP(id=PLATFORM_SOURCE, local=DynamicToolset(local, per_run_step=False))
    finally:
        await http.aclose()
