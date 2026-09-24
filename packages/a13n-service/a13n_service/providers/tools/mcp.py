"""Remote MCP servers: configuration, tool discovery and the per-run Harness capability.

Every request goes through a host-owned HTTP client that enforces the endpoint policy and presents the
connection's authentication; this module only speaks MCP over it.
"""

import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Literal, Self

import httpx2
from a13n_harness import AgentContext
from a13n_harness.providers.connector.bounds import DISCOVERY_MAX_TOOLS
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_service.providers.tools import MAX_TOOLS, CheckedToolset, DispatchCheck, ToolInfo, unique

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
    headers: Annotated[tuple[HeaderName, ...], AfterValidator(unique)] = Field(default=(), max_length=32)
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


def mcp_capability(
    url: str,
    connection_id: str,
    open_client: Callable[[], Awaitable[httpx2.AsyncClient]],
    *,
    tools: tuple[str, ...] | None,
    caller_headers: Mapping[str, str],
    defer_loading: bool,
    check: DispatchCheck,
    timeout: float,
) -> MCP[AgentContext]:
    """Compose native MCP with fresh authenticated clients and Toolsets per native run.

    Native MCP owns client entry/exit; the Service also closes clients when setup fails.
    Filtering precedes the dispatch check's catalog bound.
    """

    async def local(_ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        client = await open_client()
        client.headers.update(caller_headers)
        listed: AbstractToolset[AgentContext] = _toolset(url, connection_id, client, timeout=timeout)
        if tools is not None:
            selected = frozenset(tools)
            listed = listed.filtered(lambda _ctx, tool: tool.name in selected)
        return CheckedToolset(listed, connection_id, None, check)

    return MCP(
        id=connection_id,
        local=DynamicToolset(local, per_run_step=False),
        defer_loading=defer_loading,
    )
