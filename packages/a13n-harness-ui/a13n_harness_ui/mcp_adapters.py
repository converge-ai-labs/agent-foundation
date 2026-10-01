"""Run-fresh MCP reconstruction from credential-reference-only recipes."""

from __future__ import annotations

import os
import re
from functools import partial
from pathlib import Path

import httpx2
from a13n_harness.configuration import RunConfiguration
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError, RunError
from a13n_harness.http import outbound_tls_verify
from anyio import to_thread
from fastmcp.client.transports import ClientTransport, StdioTransport, StreamableHttpTransport
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP, AbstractCapability
from pydantic_ai.mcp import MCPToolset

from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.loader import read_mcp_file_values
from a13n_harness_ui.configuration.models import (
    EnvironmentVariableSource,
    McpCommandTransport,
    McpFileValueSource,
    McpRemoteTransport,
    McpValueSource,
)
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.mcp_apps.connections import AppMCP, AppToolset, Connections


class HarnessUiMCP(MCP[AgentContext]):
    """Inert definition capability that creates one MCP client per logical Run."""

    def __init__(
        self,
        recipe: ResolvedMcpRecipe,
        *,
        configuration_root: Path | None = None,
        apps: Connections | None = None,
    ) -> None:
        if not isinstance(recipe, ResolvedMcpRecipe):
            raise TypeError("recipe must be a ResolvedMcpRecipe")
        self._recipe = recipe.model_copy(deep=True)
        self._configuration_root = configuration_root
        self._apps = apps
        transport = recipe.transport
        self.url = transport.url if isinstance(transport, McpRemoteTransport) else None
        self.id = recipe.server_id
        self.native = False
        self.local = None
        self.authorization_token = None
        self.headers = None
        self.allowed_tools = None
        self.description = None
        self.defer_loading = False

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        cache_id = f"a13n.ui.mcp.{self._recipe.server_id}"
        existing = ctx.deps._run_capability(cache_id)
        if existing is not None:
            if not isinstance(existing, MCP):
                raise DefinitionError(
                    "Harness UI MCP has an incompatible logical-Run replacement.",
                    code="capability_type_mismatch",
                )
            return existing

        client_transport, effective_recipe, init_timeout = await prepare_mcp_transport(
            self._recipe,
            self._configuration_root,
            configuration=ctx.deps.configuration,
        )
        if self._apps is not None:
            connection = await self._apps.acquire(
                ctx.deps.thread_id,
                self._recipe.server_id,
                # Effective credentials are compared only in process memory, never persisted.
                effective_recipe,
                client_transport,
                binding=self._recipe.transport.model_dump_json(),
            )
            toolset = AppToolset(connection, self._apps.captures)
        else:
            toolset = MCPToolset[AgentContext](client_transport, id=self._recipe.server_id, init_timeout=init_timeout)
        capability = AppMCP if self._apps is not None else MCP[AgentContext]
        replacement = capability(
            id=self._recipe.server_id,
            native=False,
            local=toolset,
        )
        ctx.deps._record_run_capability(cache_id, replacement)
        return replacement


async def prepare_mcp_transport(
    recipe: ResolvedMcpRecipe,
    configuration_root: Path | None,
    *,
    configuration: RunConfiguration | None = None,
) -> tuple[ClientTransport, str, int]:
    """Resolve current credentials for a fresh Run or App operation; never persist the comparison value."""
    transport = recipe.transport
    configuration = configuration or RunConfiguration()
    if isinstance(transport, McpRemoteTransport):
        configuration.authorize_url(transport.url)
        values = await resolve_mcp_values(transport.headers, configuration_root)
        client_transport = StreamableHttpTransport(
            transport.url,
            headers=values or None,
            httpx_client_factory=partial(_no_redirect_client, configuration=configuration),
        )
        init_timeout = 5
    elif isinstance(transport, McpCommandTransport):
        values = await resolve_mcp_values(transport.environment, configuration_root)
        client_transport = StdioTransport(
            command=transport.command, args=list(transport.arguments), env=values or None, keep_alive=False
        )
        init_timeout = 30
    else:
        raise TypeError("unsupported MCP transport")
    # Selection provenance does not change the live server binding.
    return client_transport, repr((transport.model_dump(), values, configuration.model_dump(mode="json"))), init_timeout


async def resolve_mcp_values(
    values: dict[str, McpValueSource],
    configuration_root: Path | None,
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
        event_hooks={"request": [authorize]},
        # Configuration allows plaintext only to literal loopback. Keep that
        # request local even when the process has an HTTP/ALL proxy configured.
        mounts={"http://": httpx2.AsyncHTTPTransport(trust_env=False)},
    )


def _required_environment(name: str, *, purpose: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RunError(
            f"A required {purpose} source is unavailable.",
            code="runtime_credential_missing",
            details={"environment_variable": name},
        )
    return value


__all__ = ["HarnessUiMCP"]
