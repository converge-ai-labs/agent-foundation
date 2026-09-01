"""Connector Provider discovery and data-plane execution."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Protocol

from a13n_harness.errors import DefinitionError
from a13n_harness.tools.metadata import normalize_harness_tool_metadata
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session

from .domain import (
    AgentConnectorDeclaration,
    ConnectorProviderContractLock,
    ConnectorTurnSelection,
    PrincipalRef,
    bounded_json_object,
    bounded_json_value,
)
from .errors import ConnectorError
from .models import ConnectionRecord, ConnectorRecord, ConnectorRevisionRecord
from .provider import (
    ConnectorConnectionProvider,
    ConnectorEventProvider,
    ConnectorProviderConnection,
    ConnectorProviderContext,
    ConnectorProviderEventType,
    ConnectorProviderTool,
    ConnectorProviderToolResult,
    ConnectorToolProvider,
    invoke_provider,
)
from .registry import ConnectorProviderCatalog
from .service import ConnectionSecretStore

_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,199}$")
_MAX_TOOLS = 256
_MAX_EVENTS = 256


class ConnectorInvocationAuthorizer(Protocol):
    """Current policy decision used by the Connector data plane."""

    async def authorize_connector_discovery(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        principal: PrincipalRef,
        connector_id: str,
        connector_revision_id: str,
        connection_id: str | None,
    ) -> None: ...

    async def authorize_connector_tool(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        principal: PrincipalRef,
        connector_id: str,
        connector_revision_id: str,
        connection_id: str | None,
        tool_id: str,
        effects: Sequence[str],
        credential_audiences: Sequence[str],
    ) -> None: ...


class ConnectorProviderRuntime:
    """Execute trusted Providers behind the Connector Service boundary."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: ConnectorProviderCatalog,
        connection_secrets: ConnectionSecretStore,
        authorizer: ConnectorInvocationAuthorizer,
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._connection_secrets = connection_secrets
        self._authorizer = authorizer

    async def discover_tools(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_revision_id: str,
        connection_id: str | None,
        principal: PrincipalRef,
        context: ConnectorProviderContext,
    ) -> tuple[ConnectorProviderTool, ...]:
        target = await self._load_target(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_revision_id=connector_revision_id,
            connection_id=connection_id,
            principal=principal,
        )
        await self._authorizer.authorize_connector_discovery(
            organization_id=organization_id,
            workspace_id=workspace_id,
            principal=principal,
            connector_id=target.connector.id,
            connector_revision_id=target.revision.id,
            connection_id=connection_id,
        )
        return await self._provider_tools(target, context=context)

    async def create_declaration(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_revision_id: str,
        connection_id: str | None,
        selected_provider_tool_names: Sequence[str] | None,
        principal: PrincipalRef,
        context: ConnectorProviderContext,
    ) -> AgentConnectorDeclaration:
        available = await self.discover_tools(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_revision_id=connector_revision_id,
            connection_id=connection_id,
            principal=principal,
            context=context,
        )
        selected_names: tuple[str, ...] | None = None
        if selected_provider_tool_names is not None:
            selected_names = tuple(selected_provider_tool_names)
            if not selected_names or len(set(selected_names)) != len(selected_names):
                raise ConnectorError(
                    "Selected Connector tool names must be a non-empty unique list.",
                    code="invalid_request",
                )
            available_names = {tool.name for tool in available}
            if any(name not in available_names for name in selected_names):
                raise ConnectorError("A selected Connector tool was not found.", code="tool_not_found")
        async with short_session(self._sessions) as session:
            revision = await _get_revision(session, organization_id, workspace_id, connector_revision_id)
        registration = self._providers.registration(revision.provider_key)
        return AgentConnectorDeclaration(
            connector_revision_id=connector_revision_id,
            connection_id=connection_id,
            tools=selected_names,
            provider_lock=ConnectorProviderContractLock(
                provider_key=registration.provider_key,
                contract_version=registration.metadata.contract_version,
            ),
        )

    async def prepare_turn_selection(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        declaration_index: int,
        declaration: AgentConnectorDeclaration,
        principal: PrincipalRef,
        context: ConnectorProviderContext,
    ) -> ConnectorTurnSelection:
        connection_id = declaration.connection_id
        target = await self._load_target(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_revision_id=declaration.connector_revision_id,
            connection_id=connection_id,
            principal=principal,
        )
        self._require_provider_contract(target, declaration.provider_lock.contract_version)
        try:
            available = await self.discover_tools(
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_revision_id=declaration.connector_revision_id,
                connection_id=connection_id,
                principal=principal,
                context=context,
            )
        except ConnectorError as error:
            if connection_id is not None or error.code != "connection_required":
                raise
            connection_id = await self._resolve_connection_id(target, principal=principal)
            target = await self._load_target(
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_revision_id=declaration.connector_revision_id,
                connection_id=connection_id,
                principal=principal,
            )
            available = await self.discover_tools(
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_revision_id=declaration.connector_revision_id,
                connection_id=connection_id,
                principal=principal,
                context=context,
            )

        effective = _select_tools(available, declaration.tools)
        if target.connection is None and any(tool.credential_audiences for tool in effective):
            connection_id = await self._resolve_connection_id(target, principal=principal)
            target = await self._load_target(
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_revision_id=declaration.connector_revision_id,
                connection_id=connection_id,
                principal=principal,
            )
            effective = _select_tools(
                await self.discover_tools(
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    connector_revision_id=declaration.connector_revision_id,
                    connection_id=connection_id,
                    principal=principal,
                    context=context,
                ),
                declaration.tools,
            )

        return ConnectorTurnSelection(
            declaration_index=declaration_index,
            connector_revision_id=declaration.connector_revision_id,
            connection_id=connection_id,
            effective_tools=tuple(tool.name for tool in effective),
            provider_contract_version=declaration.provider_lock.contract_version,
        )

    async def list_selected_tools(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
        connector_revision_id: str,
        connection_id: str | None,
        effective_tools: Sequence[str] | None,
        provider_contract_version: str,
        principal: PrincipalRef,
        context: ConnectorProviderContext,
    ) -> tuple[ConnectorProviderTool, ...]:
        target = await self._load_target(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_revision_id=connector_revision_id,
            connection_id=connection_id,
            principal=principal,
        )
        if target.connector.id != connector_id:
            raise ConnectorError("Connector target is unavailable.", code="not_found")
        self._require_provider_contract(target, provider_contract_version)
        await self._authorizer.authorize_connector_discovery(
            organization_id=organization_id,
            workspace_id=workspace_id,
            principal=principal,
            connector_id=connector_id,
            connector_revision_id=connector_revision_id,
            connection_id=connection_id,
        )
        return _select_tools(await self._provider_tools(target, context=context), effective_tools)

    async def call_tool(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
        connector_revision_id: str,
        connection_id: str | None,
        effective_tools: Sequence[str] | None,
        provider_contract_version: str,
        provider_tool_name: str,
        arguments: Mapping[str, JsonValue],
        principal: PrincipalRef,
        context: ConnectorProviderContext,
    ) -> ConnectorProviderToolResult:
        tools = await self.list_selected_tools(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_id=connector_id,
            connector_revision_id=connector_revision_id,
            connection_id=connection_id,
            effective_tools=effective_tools,
            provider_contract_version=provider_contract_version,
            principal=principal,
            context=context,
        )
        selected = next((tool for tool in tools if tool.name == provider_tool_name), None)
        if selected is None:
            raise ConnectorError("Connector tool was not found.", code="tool_not_found")
        try:
            Draft202012Validator(dict(selected.parameters_json_schema)).validate(arguments)
        except ValidationError:
            raise ConnectorError("Connector tool arguments are invalid.", code="invalid_request") from None
        target = await self._load_target(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_revision_id=connector_revision_id,
            connection_id=connection_id,
            principal=principal,
        )
        if selected.credential_audiences and target.connection is None:
            raise ConnectorError("Connector tool requires a Connection.", code="connection_required")
        await self._authorizer.authorize_connector_tool(
            organization_id=organization_id,
            workspace_id=workspace_id,
            principal=principal,
            connector_id=connector_id,
            connector_revision_id=connector_revision_id,
            connection_id=connection_id,
            tool_id=selected.tool_id,
            effects=selected.effects,
            credential_audiences=selected.credential_audiences,
        )
        provider = self._providers.require(target.revision.provider_key)
        if not isinstance(provider, ConnectorToolProvider):
            raise ConnectorError("Connector Provider exposes no tools.", code="tool_not_found")
        provider_connection = await self._provider_connection(target)
        result = await invoke_provider(
            context,
            provider.call_tool(
                context,
                provider_config_version=target.revision.provider_config_version,
                config=target.revision.config,
                connection=provider_connection,
                tool_name=selected.name,
                arguments=bounded_json_object(dict(arguments), field_name="arguments"),
            ),
        )
        try:
            value = bounded_json_value(result.value, field_name="tool_result")
        except ConnectorError:
            raise ConnectorError(
                "Connector Provider returned an invalid tool result.",
                code="tool_contract_incompatible",
            ) from None
        return ConnectorProviderToolResult(value=value)

    async def discover_events(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_revision_id: str,
        connection_id: str,
        principal: PrincipalRef,
        context: ConnectorProviderContext,
    ) -> tuple[ConnectorProviderEventType, ...]:
        target = await self._load_target(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_revision_id=connector_revision_id,
            connection_id=connection_id,
            principal=principal,
        )
        await self._authorizer.authorize_connector_discovery(
            organization_id=organization_id,
            workspace_id=workspace_id,
            principal=principal,
            connector_id=target.connector.id,
            connector_revision_id=target.revision.id,
            connection_id=connection_id,
        )
        provider_connection = await self._provider_connection(target)
        if provider_connection is None:
            raise ConnectorError("Event discovery requires a Connection.", code="connection_required")
        provider = self._providers.require(target.revision.provider_key)
        if not isinstance(provider, ConnectorEventProvider):
            raise ConnectorError("Connector Provider exposes no events.", code="trigger_source_incompatible")
        events = await invoke_provider(
            context,
            provider.list_events(
                context,
                provider_config_version=target.revision.provider_config_version,
                config=target.revision.config,
                connection=provider_connection,
            ),
        )
        return _validate_provider_events(events)

    async def latest_revision_id(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
    ) -> str:
        async with short_session(self._sessions) as session:
            revision_id = await session.scalar(
                select(ConnectorRevisionRecord.id)
                .where(
                    ConnectorRevisionRecord.organization_id == organization_id,
                    ConnectorRevisionRecord.workspace_id == workspace_id,
                    ConnectorRevisionRecord.connector_id == connector_id,
                )
                .order_by(ConnectorRevisionRecord.version.desc())
                .limit(1)
            )
        if revision_id is None:
            raise ConnectorError("Connector revision was not found.", code="not_found")
        return revision_id

    async def provider_contract_version(self, connector_revision_id: str) -> str:
        async with short_session(self._sessions) as session:
            revision = await session.get(ConnectorRevisionRecord, connector_revision_id)
        if revision is None:
            raise ConnectorError("Connector revision was not found.", code="not_found")
        return self._providers.registration(revision.provider_key).metadata.contract_version

    async def resolve_standard_connection(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_revision_id: str,
        requested_connection_id: str | None,
        principal: PrincipalRef,
    ) -> str | None:
        target = await self._load_target(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_revision_id=connector_revision_id,
            connection_id=requested_connection_id,
            principal=principal,
        )
        if requested_connection_id is not None:
            return requested_connection_id
        try:
            return await self._resolve_connection_id(target, principal=principal)
        except ConnectorError as error:
            if error.code == "connection_required":
                return None
            raise

    async def _load_target(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_revision_id: str,
        connection_id: str | None,
        principal: PrincipalRef,
    ) -> _RuntimeTarget:
        async with short_session(self._sessions) as session:
            revision = await _get_revision(session, organization_id, workspace_id, connector_revision_id)
            connector = await _get_connector(session, organization_id, workspace_id, revision.connector_id)
            connection = None
            if connection_id is not None:
                connection = await session.scalar(
                    select(ConnectionRecord).where(
                        ConnectionRecord.id == connection_id,
                        ConnectionRecord.organization_id == organization_id,
                        ConnectionRecord.workspace_id == workspace_id,
                        ConnectionRecord.connector_id == connector.id,
                        ConnectionRecord.provider_key == revision.provider_key,
                        ConnectionRecord.status == "active",
                    )
                )
        if not connector.enabled:
            raise ConnectorError("Connector is disabled.", code="connector_disabled")
        if connection_id is not None and connection is None:
            raise ConnectorError("Connection is unavailable.", code="connection_required")
        if connection is not None and connection.principal_type is not None:
            if (
                connection.principal_type != principal.principal_type
                or connection.principal_id != principal.principal_id
            ):
                raise ConnectorError("Connection is unavailable.", code="connection_required")
        return _RuntimeTarget(connector=connector, revision=revision, connection=connection)

    async def _resolve_connection_id(self, target: _RuntimeTarget, *, principal: PrincipalRef) -> str:
        async with short_session(self._sessions) as session:
            personal = tuple(
                await session.scalars(
                    select(ConnectionRecord.id)
                    .where(
                        ConnectionRecord.organization_id == target.revision.organization_id,
                        ConnectionRecord.workspace_id == target.revision.workspace_id,
                        ConnectionRecord.connector_id == target.connector.id,
                        ConnectionRecord.provider_key == target.revision.provider_key,
                        ConnectionRecord.status == "active",
                        ConnectionRecord.principal_type == principal.principal_type,
                        ConnectionRecord.principal_id == principal.principal_id,
                    )
                    .order_by(ConnectionRecord.id)
                    .limit(2)
                )
            )
            if len(personal) > 1:
                raise ConnectorError("Connection selection is ambiguous.", code="connection_ambiguous")
            if personal:
                return personal[0]
            shared = tuple(
                await session.scalars(
                    select(ConnectionRecord.id)
                    .where(
                        ConnectionRecord.organization_id == target.revision.organization_id,
                        ConnectionRecord.workspace_id == target.revision.workspace_id,
                        ConnectionRecord.connector_id == target.connector.id,
                        ConnectionRecord.provider_key == target.revision.provider_key,
                        ConnectionRecord.status == "active",
                        ConnectionRecord.principal_type.is_(None),
                        ConnectionRecord.principal_id.is_(None),
                    )
                    .order_by(ConnectionRecord.id)
                    .limit(2)
                )
            )
        if len(shared) > 1:
            raise ConnectorError("Connection selection is ambiguous.", code="connection_ambiguous")
        if shared:
            return shared[0]
        raise ConnectorError("A Connection is required.", code="connection_required")

    async def _provider_tools(
        self,
        target: _RuntimeTarget,
        *,
        context: ConnectorProviderContext,
    ) -> tuple[ConnectorProviderTool, ...]:
        provider = self._providers.require(target.revision.provider_key)
        if not isinstance(provider, ConnectorToolProvider):
            raise ConnectorError("Connector Provider exposes no tools.", code="tool_not_found")
        provider_connection = await self._provider_connection(target)
        tools = await invoke_provider(
            context,
            provider.list_tools(
                context,
                provider_config_version=target.revision.provider_config_version,
                config=target.revision.config,
                connection=provider_connection,
            ),
        )
        return _validate_provider_tools(tools)

    async def _provider_connection(self, target: _RuntimeTarget) -> ConnectorProviderConnection | None:
        if target.connection is None:
            return None
        secrets = await self._connection_secrets.read_connection_secrets(
            organization_id=target.revision.organization_id,
            workspace_id=target.revision.workspace_id,
            connection_id=target.connection.id,
        )
        provider_connection = ConnectorProviderConnection(
            provider_state_version=target.connection.provider_state_version,
            provider_state=target.connection.provider_state,
            secrets=secrets,
        )
        provider = self._providers.require(target.revision.provider_key)
        if not isinstance(provider, ConnectorConnectionProvider):
            raise ConnectorError("Connector Provider exposes no Connections.", code="connection_incompatible")
        provider.validate_connection(
            provider_config_version=target.revision.provider_config_version,
            config=target.revision.config,
            connection=provider_connection,
        )
        return provider_connection

    def _require_provider_contract(self, target: _RuntimeTarget, expected: str) -> None:
        actual = self._providers.registration(target.revision.provider_key)
        if actual.metadata.contract_version != expected:
            raise ConnectorError("Connector Provider contract is unavailable.", code="provider_not_trusted")


class _RuntimeTarget:
    __slots__ = ("connection", "connector", "revision")

    def __init__(
        self,
        *,
        connector: ConnectorRecord,
        revision: ConnectorRevisionRecord,
        connection: ConnectionRecord | None,
    ) -> None:
        self.connector = connector
        self.revision = revision
        self.connection = connection


async def _get_revision(
    session: AsyncSession,
    organization_id: str,
    workspace_id: str,
    revision_id: str,
) -> ConnectorRevisionRecord:
    revision = await session.scalar(
        select(ConnectorRevisionRecord).where(
            ConnectorRevisionRecord.id == revision_id,
            ConnectorRevisionRecord.organization_id == organization_id,
            ConnectorRevisionRecord.workspace_id == workspace_id,
        )
    )
    if revision is None:
        raise ConnectorError("Connector revision was not found.", code="not_found")
    return revision


async def _get_connector(
    session: AsyncSession,
    organization_id: str,
    workspace_id: str,
    connector_id: str,
) -> ConnectorRecord:
    connector = await session.scalar(
        select(ConnectorRecord).where(
            ConnectorRecord.id == connector_id,
            ConnectorRecord.organization_id == organization_id,
            ConnectorRecord.workspace_id == workspace_id,
        )
    )
    if connector is None:
        raise ConnectorError("Connector was not found.", code="not_found")
    return connector


def _select_tools(
    available: Sequence[ConnectorProviderTool],
    selected_names: Sequence[str] | None,
) -> tuple[ConnectorProviderTool, ...]:
    values = tuple(available)
    if selected_names is None:
        if not values:
            raise ConnectorError("Connector Provider exposes no tools.", code="tool_not_found")
        return values
    names = tuple(selected_names)
    if not names or len(names) != len(set(names)):
        raise ConnectorError("Connector tool selection is invalid.", code="tool_contract_incompatible")
    by_name = {tool.name: tool for tool in values}
    try:
        return tuple(by_name[name] for name in names)
    except KeyError:
        raise ConnectorError("A selected Connector tool was not found.", code="tool_not_found") from None


def _validate_provider_tools(tools: Sequence[ConnectorProviderTool]) -> tuple[ConnectorProviderTool, ...]:
    values = tuple(tools)
    if len(values) > _MAX_TOOLS:
        raise ConnectorError("Connector Provider returned too many tools.", code="tool_contract_incompatible")
    names: set[str] = set()
    tool_ids: set[str] = set()
    for tool in values:
        if (
            not isinstance(tool, ConnectorProviderTool)
            or _TOOL_NAME.fullmatch(tool.name) is None
            or not 1 <= len(tool.tool_id) <= 256
            or len(tool.description) > 4_000
            or tool.name in names
            or tool.tool_id in tool_ids
            or tool.idempotency not in {"none", "read_only", "provider_key"}
            or any(
                effect not in {"read", "write", "delete", "execute", "external_communication"}
                for effect in tool.effects
            )
            or any(not audience or len(audience) > 256 for audience in tool.credential_audiences)
        ):
            raise ConnectorError("Connector Provider returned an invalid tool.", code="tool_contract_incompatible")
        bounded_json_object(dict(tool.parameters_json_schema), field_name="parameters_json_schema")
        try:
            normalize_harness_tool_metadata(
                {
                    "tool_id": tool.tool_id,
                    "effects": tool.effects,
                    "credential_audiences": tool.credential_audiences,
                    "idempotency": tool.idempotency,
                    "output_policy": tool.output_policy,
                }
            )
        except DefinitionError:
            raise ConnectorError(
                "Connector Provider returned invalid managed tool metadata.",
                code="tool_contract_incompatible",
            ) from None
        names.add(tool.name)
        tool_ids.add(tool.tool_id)
    return values


def _validate_provider_events(events: Sequence[ConnectorProviderEventType]) -> tuple[ConnectorProviderEventType, ...]:
    values = tuple(events)
    if len(values) > _MAX_EVENTS:
        raise ConnectorError("Connector Provider returned too many events.", code="trigger_source_incompatible")
    names: set[str] = set()
    for event in values:
        if (
            not isinstance(event, ConnectorProviderEventType)
            or _TOOL_NAME.fullmatch(event.name) is None
            or event.name in names
            or len(event.description) > 4_000
        ):
            raise ConnectorError("Connector Provider returned an invalid event.", code="trigger_source_incompatible")
        bounded_json_object(dict(event.config_schema), field_name="event_config_schema")
        names.add(event.name)
    return values
