"""Worker-owned external tools bound to existing Attempt and IAM authority."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Protocol

import httpx2
from a13n_harness import AgentContext
from a13n_harness.observation import record_tool_outcome_unknown
from anyio import to_thread
from jsonschema import Draft202012Validator, ValidationError
from pydantic import JsonValue, TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import CallToolFunc, MCPToolset, ToolResult
from pydantic_core import to_jsonable_python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent
from a13n_service.iam.attempts import AttemptAuthorization
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.iam.resource_scope import ResourceScope
from a13n_service.ids import new_object_id
from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.interactions.models import SessionRecord
from a13n_service.secrets import SecretProtector
from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from .connectors.connection_access import connection_binding
from .connectors.contracts import ConnectorProviderError, ConnectorToolOutcome
from .connectors.management import (
    ProviderSnapshot,
    configure_provider,
    decode_credentials,
    require_connection,
    require_connector_provider,
)
from .connectors.registry import ConnectorProviderRegistry
from .connectors.tool_discovery import discover_tools, mcp_tool
from .connectors.tool_errors import rejected_tool_outcome
from .domain import JsonObject
from .mcp.management import require_connection as require_mcp_connection
from .mcp.refresh import OAuthCredentialRefresh
from .mcp.transport import RemoteTransport
from .native import native_capability
from .native_context import NativeToolContext, parse_native_contexts
from .selection_domain import ConnectionRunSelection
from .selection_resolution import ConnectivitySelectionResolver, FrozenRunConnectivity
from .tool_validation import validate_result
from .toolsets import local_capability, namespaced, selected_tools, source_key

_CONNECTIONS = TypeAdapter(tuple[ConnectionRunSelection, ...])


@dataclass(frozen=True, slots=True)
class AttemptToolScope:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    selections: FrozenRunConnectivity
    native_tool_contexts: tuple[NativeToolContext, ...] = field(repr=False)
    authorization: AttemptAuthorization = field(repr=False)
    protected_inputs: tuple[object, ...] = field(default=(), repr=False)


class ScopeGuard(Protocol):
    async def __call__(self, session: AsyncSession | None = None) -> None: ...


class ExternalToolRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        protector: SecretProtector,
        providers: ConnectorProviderRegistry,
        remote: RemoteTransport,
        endpoints: EndpointPolicy,
        http_client: httpx2.AsyncClient,
        oauth_refresh: OAuthCredentialRefresh,
    ) -> None:
        self._sessions = sessions
        self._protector = protector
        self._providers = providers
        self._remote = remote
        self._endpoints = endpoints
        self._http = http_client
        self._oauth_refresh = oauth_refresh
        self._selections = ConnectivitySelectionResolver(sessions)

    async def _scope(
        self,
        context: AttemptContext,
        *,
        child_agent_id: str | None = None,
        accepted: AttemptToolScope | None = None,
    ) -> AttemptToolScope:
        async with short_session(self._sessions) as session:
            return await self._scope_in_session(session, context, child_agent_id=child_agent_id, accepted=accepted)

    async def _scope_in_session(
        self,
        session: AsyncSession,
        context: AttemptContext,
        *,
        child_agent_id: str | None = None,
        accepted: AttemptToolScope | None = None,
    ) -> AttemptToolScope:
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
            snapshot=context.authorization.snapshot,
        )
        if child_agent_id is not None:
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=conversation.workspace_id,
                agent_id=child_agent_id,
                action=WorkspaceAction.agent_invoke,
                snapshot=context.authorization.snapshot,
            )
        protected_inputs = (
            run.connection_selections_json,
            run.native_tool_contexts_json,
        )
        if accepted is not None:
            if (actor, run.organization_id, conversation.workspace_id, protected_inputs) != (
                accepted.actor,
                accepted.organization_id,
                accepted.workspace_id,
                accepted.protected_inputs,
            ):
                raise ValueError("external_tool_scope_changed")
            return accepted
        return AttemptToolScope(
            actor,
            run.organization_id,
            conversation.workspace_id,
            FrozenRunConnectivity(
                _CONNECTIONS.validate_python(run.connection_selections_json),
            ),
            parse_native_contexts(run.native_tool_contexts_json),
            context.authorization,
            deepcopy(protected_inputs),
        )

    async def validate(
        self,
        current_context: Callable[[], AttemptContext],
        *,
        child_agent_id: str | None = None,
        selections: FrozenRunConnectivity | None = None,
    ) -> None:
        """Check retained scope and connection eligibility without opening tool clients."""
        async with short_session(self._sessions) as session:
            scope = await self._scope_in_session(session, current_context(), child_agent_id=child_agent_id)
            selected = scope.selections if selections is None else selections
            for selection in selected.connection_selections:
                await self._selections.require_current_source(
                    session,
                    actor=scope.actor,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    selection=selection,
                    snapshot=current_context().authorization.snapshot,
                )

    @asynccontextmanager
    async def capabilities(
        self, current_context: Callable[[], AttemptContext]
    ) -> AsyncIterator[tuple[MCP[AgentContext], ...]]:
        accepted = await self._scope(current_context())

        async def guard(session: AsyncSession | None = None) -> None:
            current = (
                await self._scope(current_context(), accepted=accepted)
                if session is None
                else await self._scope_in_session(session, current_context(), accepted=accepted)
            )
            if current != accepted:
                raise ValueError("external_tool_scope_changed")

        async with self._capabilities(accepted, guard) as capabilities:
            yield capabilities

    @asynccontextmanager
    async def child_capabilities(
        self,
        current_context: Callable[[], AttemptContext],
        *,
        agent_id: str,
        selections: FrozenRunConnectivity,
    ) -> AsyncIterator[tuple[MCP[AgentContext], ...]]:
        """Bind an accepted inline child's tools to the owning Attempt, without native ingress context."""
        parent = await self._scope(current_context(), child_agent_id=agent_id)

        async def guard(session: AsyncSession | None = None) -> None:
            current = (
                await self._scope(current_context(), child_agent_id=agent_id, accepted=parent)
                if session is None
                else await self._scope_in_session(session, current_context(), child_agent_id=agent_id, accepted=parent)
            )
            if current != parent:
                raise ValueError("external_tool_scope_changed")

        accepted = replace(parent, selections=selections, native_tool_contexts=())
        async with self._capabilities(accepted, guard) as capabilities:
            yield capabilities

    @asynccontextmanager
    async def _capabilities(
        self,
        accepted: AttemptToolScope,
        guard: ScopeGuard,
    ) -> AsyncIterator[tuple[MCP[AgentContext], ...]]:
        capabilities: list[MCP[AgentContext]] = []
        async with AsyncExitStack() as stack:
            for selection in accepted.selections.connection_selections:
                if selection.tools == ():
                    continue
                if selection.kind == "connector":
                    capability = await self._connector(selection, guard, accepted)
                else:
                    capability = await stack.enter_async_context(self._mcp(selection, guard, accepted))
                if capability is not None:
                    capabilities.append(capability)
            for context in accepted.native_tool_contexts:
                capability = await native_capability(
                    self._sessions, self._protector, accepted, context, guard, self._endpoints, self._http
                )
                if capability is not None:
                    capabilities.append(capability)
            yield tuple(capabilities)

    async def _connector(
        self, selection: ConnectionRunSelection, guard: ScopeGuard, scope: AttemptToolScope
    ) -> MCP[AgentContext] | None:
        async def current_binding():
            async with short_session(self._sessions) as session:
                await guard(session)
                await self._selections.require_current_source(
                    session,
                    actor=scope.actor,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    selection=selection,
                    snapshot=scope.authorization.snapshot,
                )
                record = await require_connection(session, selection.connection_id)
                assert selection.connector_provider_id is not None
                provider = await require_connector_provider(
                    session,
                    selection.connector_provider_id,
                    scope=ResourceScope(record.organization_id, record.workspace_id),
                )
                return (
                    record.version,
                    connection_binding(record),
                    ProviderSnapshot.from_record(provider),
                    provider.credential_snapshot(),
                )

        @asynccontextmanager
        async def connection():
            accepted = await current_binding()
            _, binding, provider, credential = accepted
            raw = credential.decrypt(self._protector)
            runtime = configure_provider(self._providers, provider, decode_credentials(raw))

            async def before_dispatch() -> None:
                if await current_binding() != accepted:
                    raise ValueError("connector_connection_changed")

            async with aclosing(runtime), aclosing(runtime.connect(binding)) as connected:
                yield connected, before_dispatch

        async with connection() as (connected, _):
            definitions, _ = await discover_tools(connected)
        by_name = {tool.key: tool for tool in definitions}
        # Preserve the typed outcome envelope while validating its successful payload
        # against the original source schema, including its own local references.
        outcome_schema = ConnectorToolOutcome.model_json_schema()
        tools = tuple(mcp_tool(tool).model_copy(update={"output_schema": outcome_schema}) for tool in definitions)

        async def call(name: str, arguments: JsonObject) -> JsonValue:
            if name not in by_name or (selection.tools is not None and name not in selection.tools):
                raise ValueError("tool_not_authorized")
            request_id = new_object_id("tool")
            try:
                async with connection() as (connected, before_dispatch):
                    outcome = await connected.execute_tool(
                        tool_key=name,
                        provider_version=by_name[name].provider_version,
                        arguments=arguments,
                        request_id=request_id,
                        before_dispatch=before_dispatch,
                    )
            except ConnectorProviderError as error:
                if error.outcome_unknown:
                    record_tool_outcome_unknown()
                    return ConnectorToolOutcome(kind="outcome_unknown", request_id=request_id).model_dump(mode="json")
                rejected = rejected_tool_outcome(error, request_id=request_id)
                if rejected is not None:
                    return rejected.model_dump(mode="json")
                raise ValueError("connector_tool_failed") from error

            if outcome.kind == "outcome_unknown":
                record_tool_outcome_unknown()

            def validated_result() -> JsonObject:
                result = outcome.model_dump(mode="json")
                validate_result(result)
                output_schema = by_name[name].output_schema
                if outcome.kind == "succeeded" and output_schema is not None:
                    Draft202012Validator(output_schema).validate(outcome.result)
                return result

            try:
                return await to_thread.run_sync(validated_result)
            except (ValueError, ValidationError):
                record_tool_outcome_unknown()
                # Dispatch has finished; invalid evidence cannot establish rollback
                # or justify repeating an effect. Never expose the rejected payload.
                return ConnectorToolOutcome(kind="outcome_unknown", request_id=request_id).model_dump(mode="json")

        return await local_capability(
            key=source_key("connector", selection.connection_id),
            tools=tools,
            allowed=selection.tools,
            handler=call,
            defer_loading=selection.defer_loading,
        )

    @asynccontextmanager
    async def _mcp(
        self, selection: ConnectionRunSelection, guard: ScopeGuard, scope: AttemptToolScope
    ) -> AsyncIterator[MCP[AgentContext] | None]:
        async with short_session(self._sessions) as session:
            record = await require_mcp_connection(session, selection.connection_id)
            endpoint = record.endpoint_url

        async def headers() -> dict[str, str]:
            async with short_session(self._sessions) as session:
                await guard(session)
                await self._selections.require_current_source(
                    session,
                    actor=scope.actor,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    selection=selection,
                    snapshot=scope.authorization.snapshot,
                )
                record = await require_mcp_connection(session, selection.connection_id)
                if record.endpoint_url != endpoint:
                    raise ValueError("mcp_endpoint_changed")
            current = await self._oauth_refresh.current(selection.connection_id)
            async with short_session(self._sessions) as session:
                await guard(session)
                await self._selections.require_current_source(
                    session,
                    actor=scope.actor,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    selection=selection,
                    snapshot=scope.authorization.snapshot,
                )
                record = await require_mcp_connection(session, selection.connection_id)
                if record.version != current.version or record.credential_generation != current.credential_generation:
                    raise ValueError("mcp_connection_changed")
            if current.endpoint != endpoint:
                raise ValueError("mcp_endpoint_changed")
            return current.headers

        async def call(
            ctx: RunContext[AgentContext], call_tool: CallToolFunc, name: str, arguments: JsonObject
        ) -> ToolResult:
            await guard()
            if selection.tools is not None and name not in selection.tools:
                raise ValueError("tool_not_authorized")
            result = await call_tool(name, arguments)
            await to_thread.run_sync(validate_result, to_jsonable_python(result))
            return result

        key = source_key("mcp", selection.connection_id)
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
