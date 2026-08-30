"""Agent authoring discovery and managed Connector tool dispatch."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Protocol

from a13n_harness.tools import HarnessToolMetadata, ToolOutputPolicy
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session

from .domain import (
    AgentConnectorDeclaration,
    ConnectorProviderContractLock,
    ConnectorTurnSelection,
    FrozenConnectorTool,
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


class ConnectorRunAuthorizer(Protocol):
    """Current run-grant and product-policy decision for one managed call."""

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


class ConnectorToolRuntime:
    """Freeze Provider tools for Agent revisions and dispatch exact frozen tools."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: ConnectorProviderCatalog,
        connection_secrets: ConnectionSecretStore,
        authorizer: ConnectorRunAuthorizer,
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
        provider_connection = await self._provider_connection(
            target,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        provider = self._providers.require(target.revision.provider_key)
        if not isinstance(provider, ConnectorToolProvider):
            raise ConnectorError("Connector Provider exposes no tools.", code="tool_not_found")
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

    async def create_declaration(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_revision_id: str,
        connection_id: str | None,
        selected_provider_tool_names: Sequence[str],
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
        selected_names = tuple(selected_provider_tool_names)
        if len(set(selected_names)) != len(selected_names):
            raise ConnectorError("Selected Connector tool names must be unique.", code="invalid_request")
        available_by_name = {tool.name: tool for tool in available}
        try:
            selected = tuple(available_by_name[name] for name in selected_names)
        except KeyError:
            raise ConnectorError("A selected Connector tool was not found.", code="tool_not_found") from None
        async with short_session(self._sessions) as session:
            revision = await _get_revision(session, organization_id, workspace_id, connector_revision_id)
            connector = await _get_connector(session, organization_id, workspace_id, revision.connector_id)
        prefix = _connector_prefix(connector.name, connector.id)
        frozen = tuple(_freeze_tool(prefix, tool) for tool in selected)
        registration = self._providers.registration(revision.provider_key)
        return AgentConnectorDeclaration(
            connector_revision_id=connector_revision_id,
            connection_id=connection_id,
            tools=frozen,
            provider_lock=ConnectorProviderContractLock(
                provider_key=registration.provider_key,
                contract_version=registration.metadata.contract_version,
            ),
        )

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
        provider_connection = await self._provider_connection(
            target,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
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

    async def call_tool(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        declaration: AgentConnectorDeclaration,
        selection: ConnectorTurnSelection,
        provider_tool_name: str,
        arguments: Mapping[str, JsonValue],
        principal: PrincipalRef,
        context: ConnectorProviderContext,
    ) -> ConnectorProviderToolResult:
        if selection.connector_revision_id != declaration.connector_revision_id:
            raise ConnectorError(
                "Connector selection does not match the Agent declaration.", code="connection_incompatible"
            )
        if selection.connection_id != declaration.connection_id and declaration.connection_id is not None:
            raise ConnectorError("Connector selection changed a pinned Connection.", code="connection_incompatible")
        frozen = next((tool for tool in declaration.tools if tool.provider_tool_name == provider_tool_name), None)
        if frozen is None:
            raise ConnectorError("Connector tool is not frozen in the Agent revision.", code="tool_not_found")
        target = await self._load_target(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_revision_id=declaration.connector_revision_id,
            connection_id=selection.connection_id,
            principal=principal,
        )
        registration = self._providers.registration(target.revision.provider_key)
        lock = declaration.provider_lock
        provider = self._providers.require(target.revision.provider_key)
        if not isinstance(provider, ConnectorToolProvider):
            raise ConnectorError("Connector Provider exposes no tools.", code="tool_not_found")
        if (
            registration.provider_key != lock.provider_key
            or registration.metadata.contract_version != lock.contract_version
        ):
            raise ConnectorError("Connector Provider contract is unavailable.", code="provider_not_trusted")
        if frozen.credential_audiences and target.connection is None:
            raise ConnectorError("Connector tool requires a Connection.", code="connection_required")
        args = bounded_json_object(dict(arguments), field_name="arguments")
        await self._authorizer.authorize_connector_tool(
            organization_id=organization_id,
            workspace_id=workspace_id,
            principal=principal,
            connector_id=target.connector.id,
            connector_revision_id=target.revision.id,
            connection_id=selection.connection_id,
            tool_id=frozen.tool_id,
            effects=frozen.effects,
            credential_audiences=frozen.credential_audiences,
        )
        provider_connection = await self._provider_connection(
            target,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        result = await invoke_provider(
            context,
            provider.call_tool(
                context,
                provider_config_version=target.revision.provider_config_version,
                config=target.revision.config,
                connection=provider_connection,
                tool_name=frozen.provider_tool_name,
                arguments=args,
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
        return _RuntimeTarget(
            connector=connector,
            revision=revision,
            connection=connection,
        )

    async def _provider_connection(
        self,
        target: _RuntimeTarget,
        *,
        organization_id: str,
        workspace_id: str,
    ) -> ConnectorProviderConnection | None:
        if target.connection is None:
            return None
        secrets = await self._connection_secrets.read_connection_secrets(
            organization_id=organization_id,
            workspace_id=workspace_id,
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


def _validate_provider_tools(tools: Sequence[ConnectorProviderTool]) -> tuple[ConnectorProviderTool, ...]:
    values = tuple(tools)
    if len(values) > _MAX_TOOLS:
        raise ConnectorError("Connector Provider returned too many tools.", code="tool_contract_incompatible")
    names: set[str] = set()
    tool_ids: set[str] = set()
    for tool in values:
        if not isinstance(tool, ConnectorProviderTool) or _TOOL_NAME.fullmatch(tool.name) is None:
            raise ConnectorError("Connector Provider returned an invalid tool.", code="tool_contract_incompatible")
        if tool.name in names or tool.tool_id in tool_ids:
            raise ConnectorError("Connector Provider returned duplicate tools.", code="tool_contract_incompatible")
        bounded_json_object(dict(tool.parameters_json_schema), field_name="parameters_json_schema")
        bounded_json_object(dict(tool.output_policy), field_name="output_policy")
        try:
            FrozenConnectorTool(
                provider_tool_name=tool.name,
                model_tool_name=tool.name,
                tool_id=tool.tool_id,
                description=tool.description,
                parameters_json_schema=dict(tool.parameters_json_schema),
                effects=tool.effects,  # type: ignore[arg-type]
                credential_audiences=tool.credential_audiences,
                idempotency=tool.idempotency,  # type: ignore[arg-type]
                output_policy=dict(tool.output_policy),
            )
        except ValueError:
            raise ConnectorError(
                "Connector Provider returned an invalid tool.", code="tool_contract_incompatible"
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


def _connector_prefix(name: str, connector_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:32]
    if not normalized or not normalized[0].isalpha():
        normalized = "connector"
    suffix = connector_id.rsplit("_", 1)[-1][:8]
    return f"{normalized}_{suffix}"


def _freeze_tool(prefix: str, tool: ConnectorProviderTool) -> FrozenConnectorTool:
    model_tool_name = f"{prefix}_{tool.name}"
    if len(model_tool_name) > 256:
        raise ConnectorError("Connector tool name is too long after prefixing.", code="tool_contract_incompatible")
    try:
        metadata = HarnessToolMetadata(
            tool_id=tool.tool_id,
            effects=frozenset(tool.effects),  # type: ignore[arg-type]
            credential_audiences=tool.credential_audiences,
            idempotency=tool.idempotency,  # type: ignore[arg-type]
            output_policy=ToolOutputPolicy.model_validate(dict(tool.output_policy)),
        )
    except (TypeError, ValueError):
        raise ConnectorError("Connector tool metadata is invalid.", code="tool_contract_incompatible") from None
    return FrozenConnectorTool(
        provider_tool_name=tool.name,
        model_tool_name=model_tool_name,
        tool_id=tool.tool_id,
        description=tool.description,
        parameters_json_schema=bounded_json_object(
            dict(tool.parameters_json_schema), field_name="parameters_json_schema"
        ),
        effects=tuple(sorted(metadata.effects)),
        credential_audiences=metadata.credential_audiences,
        idempotency=metadata.idempotency,
        output_policy=bounded_json_object(metadata.output_policy.model_dump(mode="json"), field_name="output_policy"),
    )
