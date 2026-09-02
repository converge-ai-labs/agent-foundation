"""Run-fresh MCP reconstruction from credential-reference-only snapshot recipes."""

from __future__ import annotations

import os

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError, RunError
from fastmcp.client.transports import StdioTransport
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP, AbstractCapability
from pydantic_ai.mcp import MCPToolset

from a13n_ui.composition.models import ResolvedMcpRecipe
from a13n_ui.configuration.models import McpCommandTransport, McpRemoteTransport


class AgentUiMCP(MCP[AgentContext]):
    """Inert definition capability that resolves environment-backed values for each logical Run."""

    def __init__(self, recipe: ResolvedMcpRecipe) -> None:
        if not isinstance(recipe, ResolvedMcpRecipe):
            raise TypeError("recipe must be a ResolvedMcpRecipe")
        self._recipe = recipe.model_copy(deep=True)

        transport = recipe.transport
        self.url = transport.url if isinstance(transport, McpRemoteTransport) else None
        self.id = recipe.server_name
        self.native = False
        self.local = None
        self.authorization_token = None
        self.headers = None
        self.allowed_tools = None
        self.description = None
        self.defer_loading = False

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        cache_id = f"a13n.ui.mcp.{self._recipe.server_name}"
        existing = ctx.deps._run_capability(cache_id)
        if existing is not None:
            if not isinstance(existing, MCP):
                raise DefinitionError(
                    "Agent UI MCP has an incompatible logical-Run replacement.",
                    code="capability_type_mismatch",
                )
            return existing

        transport = self._recipe.transport
        if isinstance(transport, McpRemoteTransport):
            headers = {
                name: _required_environment(source.env, purpose="MCP header")
                for name, source in transport.headers.items()
            }
            replacement = MCP[AgentContext](
                transport.url,
                id=self._recipe.server_name,
                native=False,
                local=True,
                headers=headers or None,
            )
        elif isinstance(transport, McpCommandTransport):
            environment = {
                name: _required_environment(source.env, purpose="MCP command environment")
                for name, source in transport.environment.items()
            }
            toolset = MCPToolset[AgentContext](
                StdioTransport(
                    command=transport.command,
                    args=list(transport.arguments),
                    env=environment or None,
                    keep_alive=False,
                ),
                id=self._recipe.server_name,
            )
            replacement = MCP[AgentContext](
                id=self._recipe.server_name,
                native=False,
                local=toolset,
            )
        else:  # pragma: no cover - the strict serialized union is closed
            raise TypeError("unsupported MCP transport")
        ctx.deps._record_run_capability(cache_id, replacement)
        return replacement


def _required_environment(name: str, *, purpose: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RunError(
            f"A required {purpose} source is unavailable.",
            code="runtime_credential_missing",
            details={"environment_variable": name},
        )
    return value


__all__ = ["AgentUiMCP"]
