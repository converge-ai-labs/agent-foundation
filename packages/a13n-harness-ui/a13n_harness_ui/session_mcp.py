"""Session-scoped stdio MCP entries; no executable recipe is persisted."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from a13n_harness.context import AgentContext
from a13n_harness.mcp import InitializationRetryClient, MCPServer, ProgressMCPToolset, ToolsetFactory
from a13n_logging import get_logger
from acp import RequestError
from acp.schema import McpServerStdio
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.transports import StdioTransport
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset

PROFILE = "dev.a13n.harness-ui"


@dataclass(frozen=True, slots=True)
class SessionMcpServer:
    name: str
    command: str = field(repr=False)
    args: tuple[str, ...] = field(repr=False)
    cwd: Path
    env: tuple[tuple[str, str], ...] = field(repr=False)

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
                    elicitation_handler=_decline_elicitation if ctx.deps.unattended else None,
                )
                return ProgressMCPToolset(client, id=capability_id)

            return start

        def project(toolset: MCPToolset[AgentContext]) -> MCP[AgentContext]:
            return MCP(id=capability_id, native=False, local=toolset)

        return MCPServer(id=capability_id, prepare=prepare, project=project, on_error="raise")


async def _decline_elicitation(*_: object) -> ElicitResult:
    return ElicitResult(action="decline")


def select_session_mcp(
    directory: Path,
    entries: Sequence[object] | None,
    profile: dict[str, Any] | None,
    *,
    unattended: bool,
) -> tuple[tuple[SessionMcpServer, ...], dict[str, Any] | None]:
    opted = [] if profile is None else profile.get("unattendedSafeMcpServers", [])
    if not isinstance(opted, list) or any(not isinstance(name, str) for name in opted) or len(set(opted)) != len(opted):
        raise RequestError.invalid_params({"message": "List unique names in unattendedSafeMcpServers."})
    if profile is not None and opted and (type(profile.get("version")) is not int or profile["version"] != 1):
        raise RequestError.invalid_params({"message": "MCP opt-in requires a13n profile version 1."})
    servers = []
    omitted = []
    names = set()
    for entry in entries or ():
        if not isinstance(entry, McpServerStdio):
            raise RequestError.invalid_params({"message": "Only stdio MCP servers are supported."})
        name = entry.name
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name) or name in names:
            raise RequestError.invalid_params({"message": "MCP server names must be unique identifiers."})
        names.add(name)
        if not entry.command.strip() or "\x00" in entry.command or any("\x00" in arg for arg in entry.args):
            raise RequestError.invalid_params({"message": "Use a nonblank MCP command and valid arguments."})
        env = tuple((pair.name, pair.value) for pair in entry.env)
        if len({key for key, _ in env}) != len(env) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in value for key, value in env
        ):
            raise RequestError.invalid_params({"message": "Use unique, valid MCP environment entries."})
        if unattended and name not in opted:
            omitted.append({"name": name, "reason": "unattended_safety_not_declared"})
            continue
        servers.append(SessionMcpServer(name, entry.command, tuple(entry.args), directory, env))
    if set(opted) - names:
        raise RequestError.invalid_params({"message": "Every opted-in MCP name must appear in this request."})
    for item in omitted:
        get_logger(__name__).warning("Omitted ACP MCP server %s: %s", item["name"], item["reason"])
    return tuple(servers), {PROFILE: {"omittedMcpServers": omitted}} if omitted else None
