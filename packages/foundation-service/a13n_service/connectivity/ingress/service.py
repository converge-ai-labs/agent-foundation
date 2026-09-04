"""Transactional Ingress resource management."""

from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.models import AgentRecord
from a13n_service.connectivity.adapters import IngressAdapter, JsonObject
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.management import (
    canonical_digest,
    canonical_json,
    clear_credentials,
    fingerprint,
    record_command,
)
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
)
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from ._management import (
    audit,
    authorize,
    idempotency_key_digest,
    ingress_agent_ids,
    replay_command,
    require_adapter,
    require_ingress,
    require_limit,
    require_routes_fit_agents,
    require_version,
)
from .domain import (
    CreateIngressRequest,
    Ingress,
    IngressCollection,
    IngressStatus,
    ReplaceIngressCredentialsRequest,
    UpdateIngressRequest,
)
from .errors import IngressError
from .models import IngressAgentRecord, IngressRecord


class IngressService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterRegistry[IngressAdapter],
        protector: SecretProtector,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._protector = protector
        self._clock = clock

    async def create_ingress(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: CreateIngressRequest,
    ) -> Ingress:
        key_digest = idempotency_key_digest(idempotency_key)
        ingress_id = new_object_id("ing")
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize(session, actor, workspace_id, WorkspaceAction.ingress_manage)
                adapter = require_adapter(self._adapters, request.provider_key, request.provider_config_version)
                config = _validate_config(adapter, request.provider_config, request.provider_config_version)
                credentials = _validate_credentials(adapter, request.credentials, request.provider_config_version)
                request_fingerprint = fingerprint(request, credentials=credentials)
                replay = await replay_command(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    operation="ingress.create",
                    scope_id=workspace_id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                )
                if replay is not None:
                    return await _resource(session, replay.resource_id)
                await _validate_agents(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    execution_service_account_id=request.execution_service_account_id,
                    agent_ids=request.agents,
                )

                record = IngressRecord(
                    id=ingress_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    provider_key=request.provider_key,
                    provider_config_version=request.provider_config_version,
                    provider_config_json=config,
                    execution_service_account_id=request.execution_service_account_id,
                    default_agent_id=request.default_agent_id,
                    status=IngressStatus.active.value,
                    version=1,
                    credential_generation=0,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                record.replace_credential(canonical_json(credentials), self._protector)
                session.add(record)
                await session.flush()
                session.add_all(
                    IngressAgentRecord(
                        ingress_id=ingress_id,
                        agent_id=agent_id,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                    )
                    for agent_id in request.agents
                )
                record_command(
                    session,
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    operation="ingress.create",
                    scope_id=workspace_id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    resource_type="ingress",
                    resource_id=ingress_id,
                    result_version=1,
                    now=now,
                )
                session.add(audit(actor, workspace.organization_id, workspace_id, "ingress.create", ingress_id, now))
                await session.flush()
                return record.to_resource(tuple(sorted(request.agents)))
        except IntegrityError as error:
            raise IngressError(
                "ingress_conflict", "Ingress identity or name already exists.", status_code=409
            ) from error
        except SecretProtectionError as error:
            raise IngressError(
                "credential_conflict", "Ingress credentials could not be stored.", status_code=409
            ) from error

    async def list_ingresses(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> IngressCollection:
        require_limit(limit)
        scope = {"workspace_id": workspace_id, "actor": actor.principal.model_dump(mode="json")}
        try:
            position = decode_cursor(cursor, scope=scope, id_prefix="ing") if cursor is not None else None
        except CursorError as error:
            raise IngressError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        async with transaction(self._sessions) as session:
            workspace = await authorize(session, actor, workspace_id, WorkspaceAction.ingress_read)
            query = select(IngressRecord).where(
                IngressRecord.organization_id == workspace.organization_id,
                IngressRecord.workspace_id == workspace_id,
            )
            if position is not None:
                query = query.where(
                    or_(
                        IngressRecord.updated_at < position[0],
                        and_(IngressRecord.updated_at == position[0], IngressRecord.id < position[1]),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(IngressRecord.updated_at.desc(), IngressRecord.id.desc()).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            items = tuple([await _resource(session, record.id) for record in page])
            next_cursor = None
            if len(records) > limit:
                last = page[-1]
                next_cursor = encode_cursor(updated_at=last.updated_at, object_id=last.id, scope=scope)
            return IngressCollection(items=items, next_cursor=next_cursor)

    async def get_ingress(self, *, actor: AuthenticatedActor, ingress_id: str) -> Ingress:
        async with transaction(self._sessions) as session:
            record = await require_ingress(session, ingress_id)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.ingress_read)
            return await _resource(session, ingress_id)

    async def update_ingress(
        self,
        *,
        actor: AuthenticatedActor,
        ingress_id: str,
        request: UpdateIngressRequest,
    ) -> Ingress:
        async with transaction(self._sessions) as session:
            record = await require_ingress(session, ingress_id, lock=True)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.ingress_manage)
            require_version(record.version, request.expected_version)
            adapter = require_adapter(self._adapters, record.provider_key, record.provider_config_version)
            config = record.provider_config_json
            if request.provider_config is not None:
                config = _validate_config(adapter, request.provider_config, record.provider_config_version)
                if _configuration_identity(adapter, config, record.provider_config_version) != _configuration_identity(
                    adapter, record.provider_config_json, record.provider_config_version
                ):
                    raise IngressError(
                        "immutable_ingress_identity",
                        "Ingress installation identity cannot be changed.",
                        status_code=409,
                    )
            current_agents = await ingress_agent_ids(session, ingress_id)
            final_agents = request.agents if request.agents is not None else current_agents
            final_default = (
                request.default_agent_id if "default_agent_id" in request.model_fields_set else record.default_agent_id
            )
            if final_default is None or final_default not in final_agents:
                raise IngressError("invalid_agent_selection", "Default Agent must be allowed.", status_code=400)
            await _validate_agents(
                session,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                execution_service_account_id=record.execution_service_account_id,
                agent_ids=final_agents,
            )
            await require_routes_fit_agents(session, ingress_id, frozenset(final_agents))
            if request.agents is not None:
                await session.execute(delete(IngressAgentRecord).where(IngressAgentRecord.ingress_id == ingress_id))
                await session.flush()
                session.add_all(
                    IngressAgentRecord(
                        ingress_id=ingress_id,
                        agent_id=agent_id,
                        organization_id=record.organization_id,
                        workspace_id=record.workspace_id,
                    )
                    for agent_id in final_agents
                )
            if request.name is not None:
                record.name = request.name
                record.normalized_name = request.name.casefold()
            record.provider_config_json = config
            record.default_agent_id = final_default
            record.version += 1
            record.updated_at = self._clock()
            session.add(
                audit(
                    actor, record.organization_id, record.workspace_id, "ingress.update", record.id, record.updated_at
                )
            )
            await session.flush()
            return record.to_resource(tuple(sorted(final_agents)))

    async def replace_credentials(
        self,
        *,
        actor: AuthenticatedActor,
        ingress_id: str,
        idempotency_key: str,
        request: ReplaceIngressCredentialsRequest,
    ) -> Ingress:
        key_digest = idempotency_key_digest(idempotency_key)
        request_fingerprint = fingerprint(request, credentials=clear_credentials(request.credentials))
        async with transaction(self._sessions) as session:
            record = await require_ingress(session, ingress_id, lock=True)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.ingress_manage)
            replay = await replay_command(
                session,
                actor=actor,
                workspace_id=record.workspace_id,
                operation="ingress.credentials",
                scope_id=ingress_id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
            )
            if replay is not None:
                if replay.resource_id != record.id:
                    raise IngressError(
                        "idempotency_conflict", "Idempotency key was used for another resource.", status_code=409
                    )
                return await _resource(session, ingress_id)
            require_version(record.version, request.expected_version)
            adapter = require_adapter(self._adapters, record.provider_key, record.provider_config_version)
            credentials = _validate_credentials(adapter, request.credentials, record.provider_config_version)
            record.replace_credential(canonical_json(credentials), self._protector)

            record.version += 1
            record.updated_at = self._clock()
            _record_ingress_command(session, actor, record, "ingress.credentials", key_digest, request_fingerprint)
            session.add(
                audit(
                    actor,
                    record.organization_id,
                    record.workspace_id,
                    "ingress.credentials",
                    ingress_id,
                    record.updated_at,
                )
            )
            await session.flush()
            return record.to_resource(await ingress_agent_ids(session, ingress_id))

    async def set_status(
        self,
        *,
        actor: AuthenticatedActor,
        ingress_id: str,
        status: IngressStatus,
        expected_version: int,
        idempotency_key: str,
    ) -> Ingress:
        key_digest = idempotency_key_digest(idempotency_key)
        request_fingerprint = canonical_digest({"expected_version": expected_version, "status": status.value})
        operation = "ingress.enable" if status is IngressStatus.active else "ingress.disable"
        async with transaction(self._sessions) as session:
            record = await require_ingress(session, ingress_id, lock=True)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.ingress_manage)
            replay = await replay_command(
                session,
                actor=actor,
                workspace_id=record.workspace_id,
                operation=operation,
                scope_id=ingress_id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
            )
            if replay is not None:
                return await _resource(session, ingress_id)
            require_version(record.version, expected_version)
            if record.status != status.value:
                record.status = status.value
                record.version += 1
                record.updated_at = self._clock()
            _record_ingress_command(session, actor, record, operation, key_digest, request_fingerprint)
            session.add(
                audit(actor, record.organization_id, record.workspace_id, operation, ingress_id, record.updated_at)
            )
            await session.flush()
            return record.to_resource(await ingress_agent_ids(session, ingress_id))


def _validate_config(adapter: IngressAdapter, value: object, version: str) -> JsonObject:
    try:
        return adapter.validate_config(value, config_version=version)
    except ValueError as error:
        raise IngressError(
            "invalid_provider_config", "Ingress provider configuration is invalid.", status_code=400
        ) from error


def _validate_credentials(adapter: IngressAdapter, value: dict[str, SecretStr], version: str) -> JsonObject:
    try:
        return adapter.validate_credentials(clear_credentials(value), config_version=version)
    except ValueError as error:
        raise IngressError("invalid_credentials", "Ingress credentials are invalid.", status_code=400) from error


def _configuration_identity(adapter: IngressAdapter, value: JsonObject, version: str) -> object:
    return adapter.configuration_identity(value, config_version=version)


async def _validate_agents(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    execution_service_account_id: str,
    agent_ids: tuple[str, ...],
) -> None:
    execution_actor = AuthenticatedActor(
        principal=PrincipalRef(
            principal_type=PrincipalType.service_account,
            principal_id=execution_service_account_id,
        ),
        auth_method="internal",
        credential_id="connectivity-management-validation",
        boundary_workspace_id=workspace_id,
    )
    for agent_id in agent_ids:
        agent = await session.scalar(
            select(AgentRecord).where(
                AgentRecord.id == agent_id,
                AgentRecord.organization_id == organization_id,
                AgentRecord.workspace_id == workspace_id,
                AgentRecord.enabled.is_(True),
                AgentRecord.archived_at.is_(None),
            )
        )
        if agent is None:
            raise IngressError("invalid_agent_selection", "Ingress Agent is unavailable.", status_code=400)
        try:
            await authorize_agent(
                session,
                actor=execution_actor,
                workspace_id=workspace_id,
                agent_id=agent_id,
                action=WorkspaceAction.agent_invoke,
            )
        except AuthorizationError as error:
            raise IngressError(
                "invalid_execution_principal",
                "Ingress execution Principal cannot invoke every selected Agent.",
                status_code=400,
            ) from error


async def _resource(session: AsyncSession, ingress_id: str) -> Ingress:
    record = await require_ingress(session, ingress_id)
    return record.to_resource(await ingress_agent_ids(session, ingress_id))


def _record_ingress_command(
    session: AsyncSession,
    actor: AuthenticatedActor,
    record: IngressRecord,
    operation: str,
    key_digest: str,
    request_fingerprint: str,
) -> None:
    record_command(
        session,
        actor=actor,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        operation=operation,
        scope_id=record.id,
        idempotency_key_digest=key_digest,
        fingerprint=request_fingerprint,
        resource_type="ingress",
        resource_id=record.id,
        result_version=record.version,
        now=record.updated_at,
    )
