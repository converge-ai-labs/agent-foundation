"""Remote MCP servers: configuration, tool discovery and the per-run Harness capability.

Every request goes through a host-owned HTTP client that enforces the endpoint policy and presents the
connection's authentication; this module only speaks MCP over it.
"""

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Annotated, Any, Literal, Self, Unpack

import httpx2
from a13n_harness import AgentContext
from a13n_harness.providers.connector.bounds import DISCOVERY_MAX_TOOLS
from fastmcp import Client
from fastmcp.client.transports import ClientTransport
from fastmcp.client.transports.base import SessionKwargs, TransportOptions
from mcp import ClientSession
from mcp.types import CallToolRequest
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset, ToolsetTool

from a13n_service.providers.tools import MAX_TOOLS, CheckedToolset, DispatchCheck, ToolDispatch, ToolInfo, unique

MAX_HEADERS = 32

_HEADER_NAME = r"^[!#$%&'*+.^_`|~0-9a-z-]{1,128}$"
# Headers the HTTP client and the MCP transport own; only an entered bearer token sets `authorization`.
_RESERVED_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "content-type",
        "accept",
        "accept-encoding",
        "cookie",
        "set-cookie",
        "host",
        "keep-alive",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def check_header_name(name: str, *, authentication: bool = False) -> str:
    """A lowercase header name a connection may send: its credential headers or a thread's caller headers."""
    if not re.fullmatch(_HEADER_NAME, name):
        raise ValueError("Header names must be lowercase HTTP tokens of at most 128 characters")
    if name.startswith(("proxy-", "mcp-", "sec-")) or (
        name in _RESERVED_HEADERS and not (authentication and name == "authorization")
    ):
        raise ValueError("Reserved HTTP header")
    return name


ToolName = Annotated[str, StringConstraints(min_length=1, max_length=128)]
HeaderName = Annotated[str, StringConstraints(pattern=_HEADER_NAME), AfterValidator(check_header_name)]
# How a connection authenticates to its MCP server; `account` belongs to connector connections.
type McpAuth = Literal["none", "bearer", "headers", "oauth"]
type ClientAuthentication = Literal["none", "client_secret_post", "client_secret_basic"]
type OAuthGrant = Literal["authorization_code", "client_credentials"]


class OAuthSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # A client registered in advance; without one, each authorization registers a public client dynamically.
    client_id: str | None = Field(default=None, min_length=1, max_length=512)
    # How a client registered in advance authenticates to the token endpoint; the secret methods use the
    # connection's write-only `client_secret`.
    token_endpoint_auth_method: ClientAuthentication = "none"
    # `client_credentials` obtains a token for the client itself without a browser: a machine account that
    # serves every run of the workspace, as a bearer token does.
    grant_type: OAuthGrant = "authorization_code"
    # Empty requests the scopes the server advertises.
    scopes: tuple[Annotated[str, StringConstraints(pattern=r"^[!#-\[\]-~]{1,256}$")], ...] = Field(
        default=(), max_length=64
    )

    @model_validator(mode="after")
    def confidential_client(self) -> Self:
        if self.token_endpoint_auth_method != "none" and self.client_id is None:
            raise ValueError("A client secret authenticates only a client registered in advance")
        if self.grant_type == "client_credentials" and self.token_endpoint_auth_method == "none":
            raise ValueError("The client credentials grant needs a client that authenticates with a secret")
        return self


class McpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1, max_length=2048)
    # None exposes every tool the server lists, so a run refuses a server listing more than `MAX_TOOLS`.
    tools: Annotated[tuple[ToolName, ...], Field(min_length=1, max_length=MAX_TOOLS), AfterValidator(unique)] | None = (
        None
    )
    # With `auth = 'headers'`, the names of the credential headers; their values stay write-only.
    headers: Annotated[tuple[HeaderName, ...], AfterValidator(unique)] = Field(default=(), max_length=MAX_HEADERS)
    # With `auth = 'oauth'`, how the authorization flow identifies the client.
    oauth: OAuthSettings | None = None


def _toolset(url: str, connection_id: str, client: httpx2.AsyncClient, *, timeout: float) -> MCPToolset[AgentContext]:
    return MCPToolset(
        url,
        id=connection_id,
        http_client=client,
        init_timeout=timeout,
        read_timeout=timeout,
        max_retries=0,
        tool_error_behavior="failed",
        prefer_tasks=False,
        cache_tools=True,
        cache_resources=False,
        cache_prompts=False,
        include_instructions=False,
    )


async def list_mcp_tools(url: str, connection_id: str, client: httpx2.AsyncClient, *, timeout: float) -> list[ToolInfo]:
    """Every tool the server lists, so a connection can select from a server larger than it may expose."""
    async with _toolset(url, connection_id, client, timeout=timeout) as toolset:
        tools = await toolset.list_tools()
    if len(tools) > DISCOVERY_MAX_TOOLS:
        raise ValueError("The server lists more tools than discovery reads")
    return [
        ToolInfo(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            output_schema=tool.output_schema,
            annotations={}
            if tool.annotations is None
            else tool.annotations.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        for tool in tools
    ]


# Native MRTR runs in the calling task. Carry its original model identity through
# every SDK round, without using server-supplied metadata as execution authority.
_dispatch: ContextVar[tuple[ToolDispatch, DispatchCheck] | None] = ContextVar("mcp_dispatch", default=None)


class DispatchTransport(ClientTransport):
    """Authorize every native business round through the public session seam."""

    def __init__(self, wrapped: ClientTransport, connection_id: str):
        self.wrapped, self.connection_id = wrapped, connection_id
        self.legacy_only = wrapped.legacy_only

    @asynccontextmanager
    async def connect_session(
        self, *, transport_options: TransportOptions | None = None, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        options = transport_options or TransportOptions()
        connection_id = self.connection_id

        class DispatchSession(options.session_class):
            async def send_request(self, request: Any, *args: Any, **kwargs: Any) -> Any:
                if isinstance(request, CallToolRequest):
                    current = _dispatch.get()
                    if current is None or current[0].connection_id != connection_id:
                        raise ToolFailed("The MCP request has no dispatch identity and was not sent.")
                    dispatch, check = current
                    await check(dispatch)
                return await super().send_request(request, *args, **kwargs)

        async with self.wrapped.connect_session(
            transport_options=replace(options, session_class=DispatchSession), **session_kwargs
        ) as session:
            yield session

    async def close(self) -> None:
        await self.wrapped.close()

    def get_session_id(self) -> str | None:
        return self.wrapped.get_session_id()


def mcp_client(url: str, connection_id: str, http: httpx2.AsyncClient, *, timeout: float) -> Client[ClientTransport]:
    """Use the upstream adapter; its transport enters the attempt's configured HTTP client."""
    client = _toolset(url, connection_id, http, timeout=timeout).client
    client.transport = DispatchTransport(client.transport, connection_id)
    return client


class DispatchToolset(CheckedToolset):
    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        if ctx.tool_call_id is None:
            raise ToolFailed("The tool call has no call identity and was not sent.")
        token = _dispatch.set((ToolDispatch(self.connection_id, None, name, ctx.tool_call_id), self.check))
        try:
            # DispatchSession performs the check immediately before each send;
            # a logical-call-only check would miss state-only SDK continuations.
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        finally:
            _dispatch.reset(token)


class AttemptToolset(MCPToolset[AgentContext]):
    async def __aenter__(self) -> Self:
        if not self.client.is_connected():
            raise RuntimeError("The attempt-owned MCP client disconnected; the business call will not be replayed.")
        return await super().__aenter__()


def mcp_capability(
    connection_id: str,
    open_client: Callable[[], Awaitable[Client[ClientTransport]]],
    *,
    tools: tuple[str, ...] | None,
    defer_loading: bool,
    check: DispatchCheck,
) -> MCP[AgentContext]:
    """Fresh native projections borrow one entered client from the worker attempt.

    Filtering precedes the dispatch check's catalog bound. No human input handler
    is advertised without a durable Service response channel.
    """

    async def local(_ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        client = await open_client()
        listed: AbstractToolset[AgentContext] = AttemptToolset(
            client,
            id=connection_id,
            max_retries=0,
            tool_error_behavior="failed",
            prefer_tasks=False,
            # A prebuilt client cannot replace its stable Host notification handler
            # with each projection's cache callback. Refresh the live catalog.
            cache_tools=False,
            cache_resources=False,
            cache_prompts=False,
            include_instructions=False,
        )
        if tools is not None:
            selected = frozenset(tools)
            listed = listed.filtered(lambda _ctx, tool: tool.name in selected)
        return DispatchToolset(listed, connection_id, None, check)

    return MCP(
        id=connection_id,
        local=DynamicToolset(local, per_run_step=False),
        defer_loading=defer_loading,
    )
