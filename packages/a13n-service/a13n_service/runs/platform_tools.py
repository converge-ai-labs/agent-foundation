"""Attempt-owned native MCP tools, with Host credentials and Harness write review."""

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

import httpx2
from a13n_harness import AgentContext
from a13n_harness.mcp import MCPServer, ToolsetFactory
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

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
    _checked_model: Model | None = field(default=None, init=False, repr=False)
    _checked_tools: dict[str, ToolDefinition] = field(default_factory=dict, init=False, repr=False)

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        result = {}
        # Keep only the current model and offered definitions for this Run-owned wrapper.
        # A replacement model or changed schema must pass its adapter again.
        if self._checked_model is not ctx.model:
            self._checked_model = ctx.model if isinstance(ctx.model, Model) else None
            self._checked_tools.clear()
        checked = {}
        for name, tool in (await self.wrapped.get_tools(ctx)).items():
            if name in self.tools:
                # Exercise the selected model's real adapter before offering a tool. Do not
                # repair a schema, remove a tool, or silently select another model on failure.
                candidate = replace(tool.tool_def, metadata=None)
                try:
                    if isinstance(ctx.model, Model):
                        previous = self._checked_tools.get(name)
                        if previous != candidate:
                            ctx.model.prepare_request(None, ModelRequestParameters(function_tools=[tool.tool_def]))
                            previous = deepcopy(candidate)
                        checked[name] = previous
                except (ValueError, TypeError, RecursionError, UserError) as error:
                    raise UserError(
                        f"Platform tool {name} schema is incompatible with the selected model at parameters: {error}"
                    ) from error
                metadata = {**(tool.tool_def.metadata or {}), TOOL_IDENTITY_KEY: self.tools[name]}
                result[name] = replace(tool, tool_def=replace(tool.tool_def, metadata=metadata))
        self._checked_tools = checked
        return result


@asynccontextmanager
async def platform_capability(
    runtime: Runtime, lease: Lease, tools: Mapping[str, ToolIdentity]
) -> AsyncGenerator[MCPServer]:
    """Refresh before sending, never refresh-and-replay a request whose outcome may be unknown.

    The credential admits the groups of the enabled tools; the agent is offered only the enabled tools.
    """
    groups: frozenset[PlatformGroup] = frozenset(tool.group for tool in PLATFORM_TOOLS if tool.key in tools)

    async def credentials(request: httpx2.Request) -> None:
        request.headers["authorization"] = "Bearer " + await issue(runtime, lease, groups)

    server = runtime.settings.server
    url = (server.control_url or server.public_url).rstrip("/") + "/api/v1/platform-tools/"
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
        init_timeout=60,
        max_retries=0,
        tool_error_behavior="failed",
        prefer_tasks=False,
        include_instructions=False,
    )
    try:
        # Native MCP enters HTTP once. Each Harness Run borrows a fresh projection of this entered client.
        async with native.client as client:

            async def prepare(_ctx: RunContext[AgentContext]) -> ToolsetFactory:
                async def create_toolset() -> MCPToolset[AgentContext]:
                    return AttemptToolset(
                        client,
                        id=PLATFORM_SOURCE,
                        max_retries=0,
                        tool_error_behavior="failed",
                        prefer_tasks=False,
                        include_instructions=False,
                        cache_tools=True,
                        cache_resources=False,
                        cache_prompts=False,
                    )

                return create_toolset

            def project(toolset: MCPToolset[AgentContext]) -> MCP[AgentContext]:
                return MCP(id=PLATFORM_SOURCE, local=PlatformToolset(toolset, tools))

            yield MCPServer(id=PLATFORM_SOURCE, prepare=prepare, project=project)
    finally:
        await http.aclose()
