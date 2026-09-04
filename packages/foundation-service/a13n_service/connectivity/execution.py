"""Worker-owned external tools bound to existing Attempt and IAM authority."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from dataclasses import dataclass, field

from a13n_harness import AgentContext
from anyio import to_thread
from jsonschema import Draft202012Validator
from pydantic import JsonValue, TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import CallToolFunc, MCPToolset, ToolResult
from pydantic_core import to_jsonable_python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.ids import new_object_id
from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.interactions.models import SessionRecord
from a13n_service.secrets import SecretProtector
from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from .connectors.connection_access import connection_binding
from .connectors.contracts import ConnectorProviderError, ConnectorToolOutcome
from .connectors.management import decode_credentials, require_connection, require_connector_provider
from .connectors.registry import ConnectorProviderRegistry
from .connectors.tool_discovery import discover_tools, mcp_tool
from .domain import JsonObject
from .mcp.credentials import decode_request_headers
from .mcp.management import require_connection as require_mcp_connection
from .mcp.oauth_bundles import decode_oauth_bundle, optional_expiration
from .mcp.transport import RemoteTransport
from .native import native_capability
from .native_context import NativeToolContext, parse_native_contexts
from .selection_domain import ConnectorConnectionRunSelection, MCPConnectionRunSelection
from .selection_resolution import ConnectivitySelectionResolver, FrozenRunConnectivity, PreparedRevisionConnectivity
from .tool_validation import validate_result
from .toolsets import local_capability, namespaced, selected_tools, source_key

_CONNECTORS = TypeAdapter(tuple[ConnectorConnectionRunSelection, ...])
_MCPS = TypeAdapter(tuple[MCPConnectionRunSelection, ...])


@dataclass(frozen=True, slots=True)
class AttemptToolScope:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    selections: FrozenRunConnectivity
    native_tool_contexts: tuple[NativeToolContext, ...] = field(repr=False)


class ExternalToolRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        protector: SecretProtector,
        providers: ConnectorProviderRegistry,
        remote: RemoteTransport,
        endpoints: EndpointPolicy,
    ) -> None:
        self._sessions = sessions
        self._protector = protector
        self._providers = providers
        self._remote = remote
        self._endpoints = endpoints
        self._selections = ConnectivitySelectionResolver(sessions)

    async def _scope(self, context: AttemptContext) -> AttemptToolScope:
        async with short_session(self._sessions) as session:
            run, _, _ = await read_attempt_authority(session, context, utc_now())
            conversation = await session.get(SessionRecord, run.session_id)
            if conversation is None:
                raise ValueError("run_session_unavailable")
            actor = AuthenticatedActor(
                principal=PrincipalRef(
                    principal_type=PrincipalType(run.authority_principal_type), principal_id=run.authority_principal_id
                ),
                auth_method="internal",
                credential_id="attempt-tools",
                boundary_workspace_id=conversation.workspace_id,
            )
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=conversation.workspace_id,
                agent_id=run.agent_id,
                action=WorkspaceAction.agent_invoke,
            )
            return AttemptToolScope(
                actor,
                run.tenant_id,
                conversation.workspace_id,
                FrozenRunConnectivity(
                    _CONNECTORS.validate_python(run.connector_connection_selections_json),
                    _MCPS.validate_python(run.mcp_connection_selections_json),
                ),
                parse_native_contexts(run.native_tool_contexts_json),
            )

    @asynccontextmanager
    async def capabilities(
        self, current_context: Callable[[], AttemptContext]
    ) -> AsyncIterator[tuple[MCP[AgentContext], ...]]:
        accepted = await self._scope(current_context())

        async def guard() -> None:
            current = await self._scope(current_context())
            if current != accepted:
                raise ValueError("external_tool_scope_changed")
            async with short_session(self._sessions) as session:
                await self._selections.freeze_revision_creation(
                    session,
                    prepared=PreparedRevisionConnectivity(
                        accepted.actor,
                        accepted.organization_id,
                        accepted.workspace_id,
                        accepted.selections,
                    ),
                )

        await guard()
        capabilities: list[MCP[AgentContext]] = []
        async with AsyncExitStack() as stack:
            for selection in accepted.selections.connector_connection_selections:
                if selection.tools == ():
                    continue
                capability = await self._connector(selection, guard)
                if capability is not None:
                    capabilities.append(capability)
            for selection in accepted.selections.mcp_connection_selections:
                if selection.tools == ():
                    continue
                capability = await stack.enter_async_context(self._mcp(selection, guard))
                if capability is not None:
                    capabilities.append(capability)
            for context in accepted.native_tool_contexts:
                capability = await native_capability(
                    self._sessions, self._protector, accepted, context, guard, self._endpoints
                )
                if capability is not None:
                    capabilities.append(capability)
            yield tuple(capabilities)

    async def _connector(
        self, selection: ConnectorConnectionRunSelection, guard: Callable[[], Awaitable[None]]
    ) -> MCP[AgentContext] | None:
        @asynccontextmanager
        async def connection():
            await guard()
            async with short_session(self._sessions) as session:
                record = await require_connection(session, selection.connector_connection_id)
                provider = await require_connector_provider(session, selection.connector_provider_id)
                binding = await connection_binding(session, record)
                provider_type, configuration = provider.type, dict(provider.configuration_json)
                context = provider.credential_snapshot()
            raw = context.decrypt(self._protector)
            runtime = self._providers.require(provider_type).configure(configuration, decode_credentials(raw))
            async with aclosing(runtime), aclosing(runtime.connect(binding)) as connected:
                yield connected

        async with connection() as connected:
            definitions, _ = await discover_tools(connected)
        by_name = {tool.key: tool for tool in definitions}
        # Preserve the typed outcome envelope while validating its successful payload
        # against the original source schema, including its own local references.
        outcome_schema = ConnectorToolOutcome.model_json_schema()
        tools = tuple(mcp_tool(tool).model_copy(update={"outputSchema": outcome_schema}) for tool in definitions)

        async def call(name: str, arguments: JsonObject) -> JsonValue:
            if name not in by_name or (selection.tools is not None and name not in selection.tools):
                raise ValueError("tool_not_authorized")
            try:
                async with connection() as connected:
                    await guard()
                    outcome = await connected.execute_tool(
                        tool_key=name,
                        provider_version=by_name[name].provider_version,
                        arguments=arguments,
                        request_id=new_object_id("tool"),
                    )
            except ConnectorProviderError as error:
                if error.outcome_unknown:
                    return {"kind": "outcome_unknown"}
                raise ValueError("connector_tool_failed") from error
            output_schema = by_name[name].output_schema
            if outcome.kind == "succeeded" and output_schema is not None:
                await to_thread.run_sync(Draft202012Validator(output_schema).validate, outcome.result)
            return outcome.model_dump(mode="json")

        return await local_capability(
            key=source_key("connector", selection.connector_connection_id),
            tools=tools,
            allowed=selection.tools,
            handler=call,
            defer_loading=selection.defer_loading,
        )

    @asynccontextmanager
    async def _mcp(
        self, selection: MCPConnectionRunSelection, guard: Callable[[], Awaitable[None]]
    ) -> AsyncIterator[MCP[AgentContext] | None]:
        async with short_session(self._sessions) as session:
            record = await require_mcp_connection(session, selection.mcp_connection_id)
            endpoint = record.endpoint_url

        async def headers() -> dict[str, str]:
            await guard()
            async with short_session(self._sessions) as session:
                record = await require_mcp_connection(session, selection.mcp_connection_id)
                if record.endpoint_url != endpoint:
                    raise ValueError("mcp_endpoint_changed")
                if record.auth_mode == "none":
                    return {}
                context = record.credential_snapshot()
                auth_mode = record.auth_mode
            value = context.decrypt(self._protector)
            if auth_mode == "oauth":
                expires_at = optional_expiration(decode_oauth_bundle(value).get("expires_at"))
                if expires_at is not None and expires_at <= utc_now():
                    raise ValueError("mcp_credentials_expired")
            return decode_request_headers(value)

        async def call(
            ctx: RunContext[AgentContext], call_tool: CallToolFunc, name: str, arguments: JsonObject
        ) -> ToolResult:
            await guard()
            if selection.tools is not None and name not in selection.tools:
                raise ValueError("tool_not_authorized")
            result = await call_tool(name, arguments)
            await to_thread.run_sync(validate_result, to_jsonable_python(result))
            return result

        key = source_key("mcp", selection.mcp_connection_id)
        async with self._remote.connect(endpoint, headers=await headers(), refresh_headers=headers) as client:
            toolset = MCPToolset[AgentContext](client, id=key, process_tool_call=call, tool_error_behavior="error")
            async with toolset:
                definitions = await to_thread.run_sync(selected_tools, await toolset.list_tools(), selection.tools)
                names = frozenset(tool.name for tool in definitions)
                if not names:
                    yield None
                else:
                    yield MCP(
                        local=namespaced(
                            toolset.filtered(
                                lambda _ctx, tool: selection.tools is None or tool.name in selection.tools
                            ),
                            key,
                        ),  # type: ignore[arg-type]
                        id=key,
                        defer_loading=selection.defer_loading,
                    )
