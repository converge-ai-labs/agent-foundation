"""Advisory remote discovery through the upstream MCP client."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.connections.domain import Connection
from a13n_service.iam import AuthenticatedActor
from a13n_service.storage import transaction

from .domain import MCPTool
from .errors import MCPConnectionError
from .management import authorize_connection, require_connection
from .refresh import OAuthCredentialRefresh
from .transport import RemoteTransport


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    connection: Connection
    tools: tuple[MCPTool, ...]


class MCPDiscoveryService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        transport: RemoteTransport,
        credentials: OAuthCredentialRefresh,
    ) -> None:
        self._sessions = sessions
        self._transport = transport
        self._credentials = credentials

    async def discover(
        self, connection_id: str, *, actor: AuthenticatedActor, expected_version: int | None = None
    ) -> DiscoveryResult:
        snapshot = await self._credentials.current(connection_id)
        if expected_version is not None and snapshot.version != expected_version:
            raise MCPConnectionError(
                "connection_changed", "Connection changed before discovery.", category=ErrorCategory.conflict
            )
        generation = snapshot.credential_generation

        async def headers() -> dict[str, str]:
            nonlocal generation
            current = await self._credentials.current(connection_id)
            if current.endpoint != snapshot.endpoint or current.version != snapshot.version:
                raise MCPConnectionError(
                    "connection_changed", "Connection changed during discovery.", category=ErrorCategory.conflict
                )
            generation = current.credential_generation
            return current.headers

        try:
            async with self._transport.connect(
                snapshot.endpoint, headers=snapshot.headers, refresh_headers=headers
            ) as client:
                tools = tuple(
                    MCPTool(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.input_schema,
                        output_schema=tool.output_schema,
                        annotations=tool.annotations.model_dump(mode="json", exclude_none=True)
                        if tool.annotations
                        else {},
                    )
                    for tool in await client.list_tools_bounded()
                )
        except Exception as error:
            raise MCPConnectionError(
                "mcp_discovery_unavailable", "Remote tool discovery failed.", category=ErrorCategory.unavailable
            ) from error
        async with transaction(self._sessions) as session:
            current = await require_connection(session, connection_id, lock=True)
            if (
                current.version != snapshot.version
                or current.credential_generation != generation
                or current.endpoint_url != snapshot.endpoint
                or current.status not in {"pending", "ready"}
            ):
                raise MCPConnectionError(
                    "connection_changed", "Connection changed during discovery.", category=ErrorCategory.conflict
                )
            await authorize_connection(session, actor, current, mode="manage")
            current.status = "ready"
            current.status_reason = None
            resource = current.to_resource()
        return DiscoveryResult(connection=resource, tools=tools)
