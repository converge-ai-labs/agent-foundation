"""Run-fresh MCP reconstruction from credential-reference-only recipes."""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import httpx2
from a13n_harness.configuration import RunConfiguration
from a13n_harness.context import AgentContext
from a13n_harness.errors import RunError
from a13n_harness.http import outbound_tls_verify
from a13n_harness.mcp import InitializationRetryClient, MCPServer, ToolsetFactory, raise_initialization_status
from anyio import to_thread
from fastmcp.client.transports import ClientTransport, StdioTransport, StreamableHttpTransport
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.loader import read_mcp_file_values
from a13n_harness_ui.configuration.models import (
    EnvironmentVariableSource,
    McpCommandTransport,
    McpFileValueSource,
    McpPrivateValueSource,
    McpRemoteTransport,
    McpValueSource,
)
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.mcp_apps.connections import AppMCP, AppToolset, Connections
from a13n_harness_ui.mcp_oauth.models import OAuthError
from a13n_harness_ui.mcp_oauth.service import McpOAuth
from a13n_harness_ui.mcp_runtime.connections import Connection, HostClient
from a13n_harness_ui.mcp_runtime.connections import Connections as HostConnections
from a13n_harness_ui.mcp_runtime.projection import HostToolset


class HarnessUiMCP(MCPServer):
    """Inert definition capability that creates one MCP client per logical Run."""

    def __init__(
        self,
        recipe: ResolvedMcpRecipe,
        *,
        configuration_root: Path | None = None,
        apps: Connections | None = None,
        connections: HostConnections | None = None,
        private_values: Callable[[McpPrivateValueSource], Awaitable[str]] | None = None,
        operation_guard: Callable[[], Awaitable[None]] | None = None,
        oauth: McpOAuth | None = None,
    ) -> None:
        if not isinstance(recipe, ResolvedMcpRecipe):
            raise TypeError("recipe must be a ResolvedMcpRecipe")
        self._recipe = recipe.model_copy(deep=True)
        self._oauth = oauth
        self._configuration_root = configuration_root
        self._private_values = private_values
        self._operation_guard = operation_guard
        self._apps = apps
        self._connections = apps or connections
        super().__init__(
            id=recipe.server_id,
            prepare=self._prepare_toolset,
            project=self._project_toolset,
            on_error=recipe.on_error,
            tools=recipe.tools,
        )

    async def _prepare_toolset(self, ctx: RunContext[AgentContext]) -> ToolsetFactory:
        client_transport, effective_recipe, init_timeout = await prepare_mcp_transport(
            self._recipe,
            self._configuration_root,
            configuration=ctx.deps.configuration,
            private_values=self._private_values,
            oauth=self._oauth,
        )
        return partial(
            self._start_toolset,
            ctx,
            f"a13n.ui.mcp.{self._recipe.server_id}",
            client_transport,
            effective_recipe,
            init_timeout,
        )

    def _project_toolset(self, toolset: MCPToolset[AgentContext]) -> MCP[AgentContext]:
        local = _GuardedToolset(toolset, self._operation_guard) if self._operation_guard is not None else toolset
        capability = AppMCP if self._apps is not None else MCP[AgentContext]
        return capability(
            id=self._recipe.server_id,
            native=False,
            local=local,
            allowed_tools=list(self._recipe.tools) if self._recipe.tools is not None else None,
        )

    async def _start_toolset(
        self,
        ctx: RunContext[AgentContext],
        cache_id: str,
        client_transport: ClientTransport,
        effective_recipe: str,
        init_timeout: float,
    ) -> MCPToolset[AgentContext]:
        if self._connections is not None and (self._recipe.host_owned or self._apps is not None):
            connection = await self._connections.acquire(
                ctx.deps.thread_id,
                self._recipe.server_id,
                # Effective credentials are compared only in process memory, never persisted.
                effective_recipe,
                client_transport,
                binding=self._recipe.binding_key(apps=self._apps is not None),
                activate=True,
                protocol=self._recipe.protocol,
                init_timeout=init_timeout,
                client_factory=None if self._apps is not None else HostClient,
                extensions=None if self._apps is not None else (),
            )
            return AppToolset(connection, self._apps.captures) if self._apps is not None else HostToolset(connection)
        elif self._connections is not None and self._connections.input_handler is not None:
            connection = Connection(
                ctx.deps.thread_id,
                self._recipe.server_id,
                effective_recipe,
                client_transport,
                protocol=self._recipe.protocol,
                input_handler=self._connections.input_handler,
                init_timeout=init_timeout,
                cleanup_timeout_seconds=self._connections.cleanup_timeout_seconds,
            )
            # Logical-Run cleanup spans internal Agent attempts, but never retains
            # a default Run-local client beyond the enclosing Harness Run.
            ctx.deps._register_run_cleanup(cache_id, connection.close)
            await connection.start()
            return HostToolset(connection)
        client = InitializationRetryClient(client_transport, mode=self._recipe.protocol, init_timeout=init_timeout)
        return MCPToolset[AgentContext](client, id=self._recipe.server_id)


@dataclass
class _GuardedToolset(WrapperToolset[AgentContext]):
    guard: Callable[[], Awaitable[None]]

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        await self.guard()
        return await self.wrapped.get_tools(ctx)

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        await self.guard()
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)


async def prepare_mcp_transport(
    recipe: ResolvedMcpRecipe,
    configuration_root: Path | None,
    *,
    configuration: RunConfiguration | None = None,
    private_values: Callable[[McpPrivateValueSource], Awaitable[str]] | None = None,
    oauth: McpOAuth | None = None,
) -> tuple[ClientTransport, str, float]:
    """Resolve current credentials for a fresh Run or App operation; never persist the comparison value."""
    transport = recipe.transport
    configuration = configuration or RunConfiguration()
    if isinstance(transport, McpRemoteTransport):
        configuration.authorize_url(transport.url)
        values = await resolve_mcp_values(transport.headers, configuration_root, private_values=private_values)
        auth = None
        if transport.oauth is not None:
            if oauth is None or recipe.oauth_binding is None:
                raise OAuthError(
                    "The captured MCP OAuth account is unavailable; start a new Run.", code="mcp_oauth_login_required"
                )
            # Resolve the initial account/token before entering startup isolation.
            # Later requests keep the ordinary live refresh and origin checks.
            await oauth.access_token(recipe.oauth_binding)
            auth = oauth.auth(recipe.oauth_binding, transport.url)
        client_transport = StreamableHttpTransport(
            transport.url,
            auth=auth,
            headers=values or None,
            httpx_client_factory=partial(_no_redirect_client, configuration=configuration),
        )
        init_timeout = transport.init_timeout_seconds or (30 if recipe.host_owned or recipe.apps_enabled else 5)
    elif isinstance(transport, McpCommandTransport):
        values = await resolve_mcp_values(transport.environment, configuration_root, private_values=private_values)
        client_transport = StdioTransport(
            command=transport.command, args=list(transport.arguments), env=values or None, keep_alive=False
        )
        init_timeout = 30
    else:
        raise TypeError("unsupported MCP transport")
    # Selection provenance does not change the live server binding.
    return (
        client_transport,
        repr(
            (
                transport.model_dump(),
                recipe.oauth_binding.model_dump() if recipe.oauth_binding else None,
                values,
                configuration.model_dump(mode="json"),
                recipe.protocol,
                recipe.apps_enabled,
            )
        ),
        init_timeout,
    )


async def resolve_mcp_values(
    values: dict[str, McpValueSource],
    configuration_root: Path | None,
    *,
    private_values: Callable[[McpPrivateValueSource], Awaitable[str]] | None = None,
) -> dict[str, str]:
    """Resolve values once per Run, without adding secret bytes to its recipe."""
    files: dict[tuple[str, str], list[McpFileValueSource]] = {}
    for source in values.values():
        if isinstance(source, McpFileValueSource):
            files.setdefault((source.file, source.source_digest), []).append(source)
    literals: dict[tuple[str, str, tuple[str, ...]], str] = {}
    for (file, digest), sources in files.items():
        if configuration_root is None:
            raise RunError(
                "MCP literal values require the selected configuration directory.", code="runtime_credential_missing"
            )
        try:
            resolved = await to_thread.run_sync(read_mcp_file_values, configuration_root, tuple(sources))
        except (ConfigurationError, KeyError, TypeError) as exc:
            code = exc.code if isinstance(exc, ConfigurationError) else "mcp_source_invalid"
            error = RunError(
                "The captured MCP value source is unavailable or changed; reload configuration for a new Run.",
                code=code,
            )
        else:
            literals.update({(file, digest, path): value for path, value in resolved.items()})
            continue
        raise error
    result = {}
    for name, source in values.items():
        if isinstance(source, EnvironmentVariableSource):
            result[name] = _required_environment(source.env, purpose="MCP value")
        elif isinstance(source, McpPrivateValueSource):
            if private_values is None:
                raise RunError(
                    "Private MCP credentials are unavailable in this execution.", code="runtime_credential_missing"
                )
            result[name] = await private_values(source)
        else:
            template = literals[(source.file, source.source_digest, source.path)]
            result[name] = re.sub(
                r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                lambda match: _required_environment(match[1], purpose="MCP value"),
                template,
            )
    return result


def _no_redirect_client(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    *,
    follow_redirects: bool = False,
    configuration: RunConfiguration | None = None,
) -> httpx2.AsyncClient:
    """Accept the transport factory contract while retaining Host redirect policy."""
    del follow_redirects
    configuration = configuration or RunConfiguration()

    async def authorize(request: httpx2.Request) -> None:
        configuration.authorize_url(str(request.url))

    return httpx2.AsyncClient(
        verify=outbound_tls_verify(),
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=False,
        event_hooks={"request": [authorize], "response": [raise_initialization_status]},
        # Configuration allows plaintext only to literal loopback. Keep that
        # request local even when the process has an HTTP/ALL proxy configured.
        mounts={"http://": httpx2.AsyncHTTPTransport(trust_env=False)},
    )


def _required_environment(name: str, *, purpose: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RunError(
            f"A required {purpose} source is unavailable: environment variable {name!r} is missing or empty. "
            "Set it in the Harness UI process environment or update the MCP configuration.",
            code="runtime_credential_missing",
            details={"environment_variable": name},
        )
    return value


__all__ = ["HarnessUiMCP"]
