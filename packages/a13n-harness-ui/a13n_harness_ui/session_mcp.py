"""Session-scoped stdio MCP entries; no executable recipe is persisted."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from a13n_harness.context import AgentContext
from a13n_harness.mcp import InitializationRetryClient, MCPServer, ToolsetFactory
from a13n_harness.tools.surface import UNATTENDED_TOOL_KEY
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.transports import StdioTransport
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset


@dataclass(frozen=True, slots=True)
class SessionMcpServer:
    name: str
    command: str = field(repr=False)
    args: tuple[str, ...] = field(repr=False)
    cwd: Path
    env: tuple[tuple[str, str], ...] = field(repr=False)
    unattended_safe: bool = False

    def capability(self) -> MCPServer:
        capability_id = f"session-mcp.{self.name}"

        async def prepare(ctx: RunContext[AgentContext]) -> ToolsetFactory:
            async def start() -> MCPToolset[AgentContext]:
                transport = StdioTransport(
                    self.command, list(self.args), env=dict(self.env) or None, cwd=str(self.cwd), keep_alive=False
                )
                client = InitializationRetryClient(
                    transport,
                    mode="auto",
                    init_timeout=30,
                    elicitation_handler=_decline_elicitation if self.unattended_safe and ctx.deps.unattended else None,
                )
                return MCPToolset(client, id=capability_id)

            return start

        def project(toolset: MCPToolset[AgentContext]) -> MCP[AgentContext]:
            local = _DeclaredSafe(toolset) if self.unattended_safe else toolset
            return MCP(id=capability_id, native=False, local=local)

        return MCPServer(id=capability_id, prepare=prepare, project=project, on_error="raise")


async def _decline_elicitation(*_: object) -> ElicitResult:
    return ElicitResult(action="decline")


@dataclass
class _DeclaredSafe(WrapperToolset[AgentContext]):
    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        return {
            name: replace(
                tool,
                tool_def=replace(
                    tool.tool_def,
                    metadata={
                        **(tool.tool_def.metadata or {}),
                        UNATTENDED_TOOL_KEY: True,
                    },
                ),
            )
            for name, tool in tools.items()
        }
