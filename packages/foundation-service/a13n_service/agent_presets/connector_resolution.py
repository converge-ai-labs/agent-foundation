"""Two-phase Connector selection for AgentPreset publication and invocation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectors import (
    ConnectionSecretStore,
    ConnectorError,
    ConnectorProviderConnection,
    ConnectorProviderContext,
    ConnectorProviderOperations,
    ConnectorProviderTool,
    freeze_provider_tools,
    resolve_connection,
)
from a13n_service.connectors.models import ConnectionRecord, ConnectorRecord, ConnectorRevisionRecord
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_workspace
from a13n_service.storage import short_session

from .domain import (
    ConnectorProviderContractLock,
    ConnectorSelection,
    FrozenConnectorTool,
    ResolvedConnectorSelection,
)


class ConnectorResolutionPurpose(StrEnum):
    publish = "publish"
    invoke = "invoke"


@dataclass(frozen=True, slots=True)
class PreparedConnectorSelection:
    name: str
    connector_id: str
    connector_revision_id: str
    provider_key: str
    provider_config_version: str
    provider_config: dict[str, object]
    connection_id: str | None
    connection_version: int | None
    resolved: ResolvedConnectorSelection


@dataclass(frozen=True, slots=True)
class PreparedConnectorSelections:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    purpose: ConnectorResolutionPurpose
    items: tuple[PreparedConnectorSelection, ...]


@dataclass(frozen=True, slots=True)
class _LoadedProviderConnection:
    value: ConnectorProviderConnection
    version: int


class AgentConnectorSelectionResolver:
    """Resolve Provider I/O outside a transaction and recheck exact facts inside it."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: ConnectorProviderOperations,
        connection_secrets: ConnectionSecretStore,
        *,
        operation_timeout: timedelta = timedelta(seconds=30),
        clock=None,
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._connection_secrets = connection_secrets
        self._operation_timeout = operation_timeout
        self._clock = clock or (lambda: datetime.now(UTC))

    async def prepare_publication(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        selections: Mapping[str, ConnectorSelection],
    ) -> PreparedConnectorSelections:
        return await self._prepare(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            purpose=ConnectorResolutionPurpose.publish,
            selections=selections,
            retained={},
            reuse_names=frozenset(),
        )

    async def prepare_invocation(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        selections: Mapping[str, ConnectorSelection],
        retained: Mapping[str, ResolvedConnectorSelection],
        reuse_names: frozenset[str],
        sensitive_headers: Mapping[str, Mapping[str, str]],
    ) -> PreparedConnectorSelections:
        if any(sensitive_headers.values()):
            raise ConnectorError(
                "The selected Connector Provider declares no runtime header schema.",
                code="runtime_headers_unsupported",
            )
        return await self._prepare(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            purpose=ConnectorResolutionPurpose.invoke,
            selections=selections,
            retained=retained,
            reuse_names=reuse_names,
        )

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedConnectorSelections,
    ) -> tuple[ResolvedConnectorSelection, ...]:
        await _authorize(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            purpose=prepared.purpose,
            has_connections=bool(prepared.items),
        )
        result: list[ResolvedConnectorSelection] = []
        for expected in prepared.items:
            revision = await session.scalar(
                select(ConnectorRevisionRecord)
                .where(
                    ConnectorRevisionRecord.id == expected.connector_revision_id,
                    ConnectorRevisionRecord.organization_id == prepared.organization_id,
                    ConnectorRevisionRecord.workspace_id == prepared.workspace_id,
                    ConnectorRevisionRecord.connector_id == expected.connector_id,
                )
                .with_for_update()
            )
            connector = await session.scalar(
                select(ConnectorRecord)
                .where(
                    ConnectorRecord.id == expected.connector_id,
                    ConnectorRecord.organization_id == prepared.organization_id,
                    ConnectorRecord.workspace_id == prepared.workspace_id,
                )
                .with_for_update()
            )
            if (
                revision is None
                or connector is None
                or not connector.enabled
                or revision.provider_key != expected.provider_key
                or revision.provider_config_version != expected.provider_config_version
                or revision.config != expected.provider_config
            ):
                raise ConnectorError("Connector selection changed during acceptance.", code="connector_changed")
            if expected.connection_id is not None:
                connection = await _load_connection(
                    session,
                    organization_id=prepared.organization_id,
                    workspace_id=prepared.workspace_id,
                    connector_id=expected.connector_id,
                    provider_key=expected.provider_key,
                    connection_id=expected.connection_id,
                    actor=prepared.actor,
                )
                if connection.version != expected.connection_version:
                    raise ConnectorError("Connection changed during acceptance.", code="connection_changed")
            result.append(expected.resolved)
        return tuple(result)

    async def _prepare(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        purpose: ConnectorResolutionPurpose,
        selections: Mapping[str, ConnectorSelection],
        retained: Mapping[str, ResolvedConnectorSelection],
        reuse_names: frozenset[str],
    ) -> PreparedConnectorSelections:
        if not selections:
            return PreparedConnectorSelections(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                purpose=purpose,
                items=(),
            )
        async with short_session(self._sessions) as session:
            await _authorize(
                session,
                actor=actor,
                workspace_id=workspace_id,
                purpose=purpose,
                has_connections=True,
            )
            targets = {
                name: await _load_target(
                    session,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    selection=selection,
                )
                for name, selection in selections.items()
            }

        result: list[PreparedConnectorSelection] = []
        for index, (name, selection) in enumerate(selections.items()):
            connector, revision = targets[name]
            retained_selection = retained.get(name) if name in reuse_names else None
            metadata = await self._providers.metadata(revision.provider_key)
            if retained_selection is not None:
                if (
                    retained_selection.name != name
                    or retained_selection.connector_revision_id != revision.id
                    or retained_selection.provider_lock.provider_key != revision.provider_key
                    or retained_selection.provider_lock.contract_version != metadata.contract_version
                ):
                    raise ConnectorError(
                        "Retained Connector contract is unavailable.", code="provider_contract_changed"
                    )
                connection_id = await self._resolve_connection_for_tools(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    connector=connector,
                    revision=revision,
                    requested_connection_id=selection.connection_id,
                    tools=retained_selection.tools,
                )
                resolved = retained_selection.model_copy(update={"connection_id": connection_id})
                connection_version = await self._connection_version(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    connector_id=connector.id,
                    provider_key=revision.provider_key,
                    connection_id=connection_id,
                )
            else:
                connection_id, connection_version, tools = await self._discover_tools(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    connector=connector,
                    revision=revision,
                    requested_connection_id=selection.connection_id,
                    selected_names=selection.tools,
                    resolve_required_connection=purpose is ConnectorResolutionPurpose.invoke,
                    context=ConnectorProviderContext(
                        operation_id=f"agent-preset-{purpose.value}-{index}",
                        deadline=self._clock() + self._operation_timeout,
                    ),
                )
                resolved = ResolvedConnectorSelection(
                    name=name,
                    connector_revision_id=revision.id,
                    connection_id=connection_id,
                    tools=tuple(_freeze_tool(tool) for tool in tools),
                    provider_lock=ConnectorProviderContractLock(
                        provider_key=revision.provider_key,
                        contract_version=metadata.contract_version,
                    ),
                    sensitive_binding_keys=(),
                )
            result.append(
                PreparedConnectorSelection(
                    name=name,
                    connector_id=connector.id,
                    connector_revision_id=revision.id,
                    provider_key=revision.provider_key,
                    provider_config_version=revision.provider_config_version,
                    provider_config=dict(revision.config),
                    connection_id=connection_id,
                    connection_version=connection_version,
                    resolved=resolved,
                )
            )
        return PreparedConnectorSelections(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            purpose=purpose,
            items=tuple(result),
        )

    async def _discover_tools(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connector: ConnectorRecord,
        revision: ConnectorRevisionRecord,
        requested_connection_id: str | None,
        selected_names: Sequence[str] | None,
        resolve_required_connection: bool,
        context: ConnectorProviderContext,
    ) -> tuple[str | None, int | None, tuple[ConnectorProviderTool, ...]]:
        connection_id = requested_connection_id
        loaded_connection = await self._provider_connection(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_id=connector.id,
            provider_key=revision.provider_key,
            connection_id=connection_id,
            actor=actor,
        )
        try:
            available = await self._providers.list_tools(
                revision.provider_key,
                context,
                provider_config_version=revision.provider_config_version,
                config=revision.config,
                connection=loaded_connection.value if loaded_connection else None,
            )
        except ConnectorError as error:
            if connection_id is not None or error.code != "connection_required":
                raise
            connection_id = await self._resolve_connection_id(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_id=connector.id,
                provider_key=revision.provider_key,
                requested_connection_id=None,
            )
            loaded_connection = await self._provider_connection(
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_id=connector.id,
                provider_key=revision.provider_key,
                connection_id=connection_id,
                actor=actor,
            )
            if loaded_connection is None:
                raise ConnectorError("Connector requires a Connection.", code="connection_required") from error
            available = await self._providers.list_tools(
                revision.provider_key,
                context,
                provider_config_version=revision.provider_config_version,
                config=revision.config,
                connection=loaded_connection.value,
            )
        selected = freeze_provider_tools(available, selected_names)
        if (
            connection_id is None
            and resolve_required_connection
            and any(tool.credential_audiences for tool in selected)
        ):
            connection_id = await self._resolve_connection_id(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_id=connector.id,
                provider_key=revision.provider_key,
                requested_connection_id=None,
            )
            loaded_connection = await self._provider_connection(
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_id=connector.id,
                provider_key=revision.provider_key,
                connection_id=connection_id,
                actor=actor,
            )
            if loaded_connection is None:
                raise ConnectorError("Connector requires a Connection.", code="connection_required")
            selected = freeze_provider_tools(
                await self._providers.list_tools(
                    revision.provider_key,
                    context,
                    provider_config_version=revision.provider_config_version,
                    config=revision.config,
                    connection=loaded_connection.value,
                ),
                selected_names,
            )
        return (
            connection_id,
            loaded_connection.version if loaded_connection else None,
            selected,
        )

    async def _resolve_connection_for_tools(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connector: ConnectorRecord,
        revision: ConnectorRevisionRecord,
        requested_connection_id: str | None,
        tools: tuple[FrozenConnectorTool, ...],
    ) -> str | None:
        requires_connection = any(tool.credential_audiences for tool in tools)
        if requested_connection_id is None and not requires_connection:
            return None
        return await self._resolve_connection_id(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_id=connector.id,
            provider_key=revision.provider_key,
            requested_connection_id=requested_connection_id,
        )

    async def _resolve_connection_id(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
        provider_key: str,
        requested_connection_id: str | None,
    ) -> str:
        connection = await resolve_connection(
            self._sessions,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_id=connector_id,
            provider_key=provider_key,
            principal=actor.principal,
            pinned_connection_id=requested_connection_id,
        )
        if connection is None:
            raise ConnectorError("Connector requires a Connection.", code="connection_required")
        return connection.id

    async def _provider_connection(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
        provider_key: str,
        connection_id: str | None,
        actor: AuthenticatedActor,
    ) -> _LoadedProviderConnection | None:
        if connection_id is None:
            return None
        async with short_session(self._sessions) as session:
            record = await _load_connection(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_id=connector_id,
                provider_key=provider_key,
                connection_id=connection_id,
                actor=actor,
            )
        secrets = await self._connection_secrets.read_connection_secrets(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connection_id=connection_id,
        )
        return _LoadedProviderConnection(
            value=ConnectorProviderConnection(
                provider_state_version=record.provider_state_version,
                provider_state=record.provider_state,
                secrets=secrets,
            ),
            version=record.version,
        )

    async def _connection_version(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
        provider_key: str,
        connection_id: str | None,
    ) -> int | None:
        if connection_id is None:
            return None
        async with short_session(self._sessions) as session:
            record = await _load_connection(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_id=connector_id,
                provider_key=provider_key,
                connection_id=connection_id,
                actor=actor,
            )
        return record.version


async def _authorize(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    purpose: ConnectorResolutionPurpose,
    has_connections: bool,
) -> None:
    await authorize_workspace(
        session,
        actor=actor,
        workspace_id=workspace_id,
        action=(
            WorkspaceAction.connector_read
            if purpose is ConnectorResolutionPurpose.publish
            else WorkspaceAction.connector_invoke
        ),
    )
    if has_connections:
        await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.connection_read,
        )


async def _load_target(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    selection: ConnectorSelection,
) -> tuple[ConnectorRecord, ConnectorRevisionRecord]:
    revision = await session.scalar(
        select(ConnectorRevisionRecord).where(
            ConnectorRevisionRecord.id == selection.connector_revision_id,
            ConnectorRevisionRecord.organization_id == organization_id,
            ConnectorRevisionRecord.workspace_id == workspace_id,
        )
    )
    if revision is None:
        raise ConnectorError("Connector revision is unavailable.", code="not_found")
    connector = await session.scalar(
        select(ConnectorRecord).where(
            ConnectorRecord.id == revision.connector_id,
            ConnectorRecord.organization_id == organization_id,
            ConnectorRecord.workspace_id == workspace_id,
        )
    )
    if connector is None or not connector.enabled:
        raise ConnectorError("Connector is unavailable.", code="connector_disabled")
    return connector, revision


async def _load_connection(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    connector_id: str,
    provider_key: str,
    connection_id: str,
    actor: AuthenticatedActor,
) -> ConnectionRecord:
    record = await session.scalar(
        select(ConnectionRecord).where(
            ConnectionRecord.id == connection_id,
            ConnectionRecord.organization_id == organization_id,
            ConnectionRecord.workspace_id == workspace_id,
            ConnectionRecord.connector_id == connector_id,
            ConnectionRecord.provider_key == provider_key,
            ConnectionRecord.status == "active",
        )
    )
    if record is None or (
        record.principal_type is not None
        and (
            record.principal_type != actor.principal.principal_type
            or record.principal_id != actor.principal.principal_id
        )
    ):
        raise ConnectorError("Connection is unavailable.", code="connection_required")
    if record.expires_at is not None:
        expires_at = record.expires_at.replace(tzinfo=UTC) if record.expires_at.tzinfo is None else record.expires_at
        if expires_at <= datetime.now(UTC):
            raise ConnectorError("Connection is expired.", code="connection_required")
    return record


def _freeze_tool(tool: ConnectorProviderTool) -> FrozenConnectorTool:
    return FrozenConnectorTool(
        name=tool.name,
        tool_id=tool.tool_id,
        description=tool.description,
        parameters_json_schema=dict(tool.parameters_json_schema),
        effects=tool.effects,
        credential_audiences=tool.credential_audiences,
        idempotency=tool.idempotency,
        output_policy=dict(tool.output_policy),
    )
