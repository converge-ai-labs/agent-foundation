"""Advisory remote discovery through the upstream MCP client."""

from __future__ import annotations

from pydantic_ai.mcp import MCPToolset
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.secrets import SecretProtector
from a13n_service.storage import short_session, transaction

from .credentials import decode_request_headers
from .domain import MCPTool
from .errors import MCPConnectionError
from .management import require_connection
from .transport import RemoteTransport


class MCPDiscoveryService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], transport: RemoteTransport, protector: SecretProtector
    ) -> None:
        self._sessions = sessions
        self._transport = transport
        self._protector = protector

    async def discover(self, connection_id: str) -> tuple[MCPTool, ...]:
        async with short_session(self._sessions) as session:
            record = await require_connection(session, connection_id)
            if record.status == "disabled":
                raise MCPConnectionError("connection_disabled", "MCPConnection is disabled.", status_code=409)
            endpoint, generation, version = record.endpoint_url, record.credential_generation, record.version
            context = record.credential_snapshot() if record.auth_mode != "none" else None
        headers = decode_request_headers(context.decrypt(self._protector)) if context is not None else {}
        try:
            async with self._transport.connect(endpoint, headers=headers) as client:
                toolset = MCPToolset(client)
                async with toolset:
                    tools = tuple(
                        MCPTool(
                            name=tool.name,
                            description=tool.description or "",
                            input_schema=tool.inputSchema,
                            output_schema=tool.outputSchema,
                            annotations=tool.annotations.model_dump(mode="json", exclude_none=True)
                            if tool.annotations
                            else {},
                        )
                        for tool in await toolset.list_tools()
                    )
        except Exception as error:
            raise MCPConnectionError(
                "mcp_discovery_unavailable", "Remote tool discovery failed.", status_code=503
            ) from error
        async with transaction(self._sessions) as session:
            current = await require_connection(session, connection_id, lock=True)
            if (
                current.version != version
                or current.credential_generation != generation
                or current.status == "disabled"
            ):
                raise MCPConnectionError(
                    "connection_changed", "MCPConnection changed during discovery.", status_code=409
                )
            current.status = "ready"
            current.status_reason = None
        return tools
