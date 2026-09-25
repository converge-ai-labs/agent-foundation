"""The Remote MCP servers a deployment suggests for new connections: a packaged list plus its own entries.

An entry only prefills a connection; creating one still validates the endpoint against the egress policy.
"""

from collections.abc import Sequence
from importlib.resources import files
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from a13n_service.infra import cursors
from a13n_service.providers.tools import unique
from a13n_service.providers.tools.mcp import MAX_HEADERS, HeaderName, McpAuth

# People open these links, so only web URLs are accepted.
WebUrl = Annotated[str, StringConstraints(max_length=2048, pattern=r"^https?://\S+$")]


class McpServer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    url: WebUrl
    auth: McpAuth
    # With `auth = 'headers'`, the credential headers the server expects.
    header_names: Annotated[tuple[HeaderName, ...], AfterValidator(unique)] = Field(default=(), max_length=MAX_HEADERS)
    documentation_url: WebUrl | None = None
    logo_url: WebUrl | None = None
    # What the person connecting needs, such as an account or a token with certain access.
    requirements: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def headers_for_headers_auth(self) -> Self:
        if (self.auth == "headers") != bool(self.header_names):
            raise ValueError("header_names lists headers exactly for headers authentication")
        return self


def _unique_keys(servers: tuple[McpServer, ...]) -> tuple[McpServer, ...]:
    if len({server.key for server in servers}) != len(servers):
        raise ValueError("MCP server keys must be unique")
    return servers


# A list of entries, such as the deployment's `providers.mcp_servers`; keys are unique within it.
type McpServers = Annotated[tuple[McpServer, ...], AfterValidator(_unique_keys)]

PACKAGED = TypeAdapter(McpServers).validate_json(
    files("a13n_service.providers.tools").joinpath("mcp_servers.json").read_bytes()
)


class McpServerPage(BaseModel):
    items: list[McpServer]
    next_cursor: str | None


def list_mcp_servers(
    deployment: Sequence[McpServer], *, query: str | None, limit: int, cursor: str | None
) -> McpServerPage:
    """Servers in key order whose key, name or description contains `query`, which a cursor stays bound to.

    A deployment entry replaces the packaged one with its key.
    """
    needle = (query or "").casefold()
    servers = {server.key: server for server in (*PACKAGED, *deployment)}
    matching = [
        server
        for key, server in sorted(servers.items())
        if needle in key or needle in server.name.casefold() or needle in server.description.casefold()
    ]
    items, next_cursor = cursors.key_page(
        matching,
        lambda server: server.key,
        kind="mcp_servers",
        owner=cursors.query_owner(needle),
        cursor=cursor,
        limit=limit,
    )
    return McpServerPage(items=items, next_cursor=next_cursor)
