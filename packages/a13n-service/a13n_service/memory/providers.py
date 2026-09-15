"""Memory Provider account management; transactions never span provider requests."""

import json
from datetime import timedelta

from a13n_harness.memory_plugins import MemoryBackendCatalog
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.application_errors import ErrorCategory
from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from a13n_service.credentials import credential_payload
from a13n_service.iam import AuthenticatedActor, authorize_agent
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import AuthorizationError, WorkspaceAction
from a13n_service.iam.resource_scope import authorize_scope
from a13n_service.ids import new_object_id
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import is_unique_conflict, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import (
    CreateMemoryProviderRequest,
    MemoryProvider,
    MemoryProviderCollection,
    MemoryProviderDefinition,
    MemoryProviderDefinitionCollection,
    MemoryProviderReference,
    MemoryProviderReferenceCollection,
    UpdateMemoryProviderRequest,
)
from .models import MemoryProviderRecord
from .resources import MemoryProviderError, require_etag, require_provider


class MemoryProviderService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        protector: SecretProtector,
        catalog: MemoryBackendCatalog,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self.sessions = sessions
        self.protector = protector
        self.clock = clock
        self.catalog = catalog

    async def type_definitions(self, *, actor: AuthenticatedActor) -> MemoryProviderDefinitionCollection:
        async with transaction(self.sessions) as session:
            await authorize_scope(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.memory_provider_read,
            )
        return MemoryProviderDefinitionCollection(
            items=tuple(
                MemoryProviderDefinition(
                    type=key,
                    display_name=plugin.display_name,
                    configuration_schema=plugin.configuration_model.model_json_schema(),
                    credential_schema={**plugin.credential_model.model_json_schema(), "writeOnly": True},
                )
                for key, plugin in sorted(self.catalog.items())
            )
        )

    async def create(
        self, *, actor: AuthenticatedActor, workspace_id: str | None, request: CreateMemoryProviderRequest
    ) -> MemoryProvider:
        try:
            async with transaction(self.sessions) as session:
                scope = await authorize_scope(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.memory_provider_manage
                )
                now = self.clock()
                configuration = self._validate_configuration(request.type, request.configuration)
                credentials = self._validate_credentials(request.type, request.credential)
                record = MemoryProviderRecord(
                    id=new_object_id("memprov"),
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    type=request.type,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    configuration=configuration,
                    enabled=request.enabled,
                    credential_generation=0,
                    ciphertext=None,
                    nonce=None,
                    encryption_key_id=None,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                record.replace_credential(json.dumps(credentials), self.protector)
                session.add(record)
                self.audit(session, actor, record, "create", tuple(request.model_fields_set))
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            _name_conflict(error)
            raise

    async def get(self, *, actor: AuthenticatedActor, workspace_id: str | None, provider_id: str) -> MemoryProvider:
        async with transaction(self.sessions) as session:
            scope = await authorize_scope(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.memory_provider_read
            )
            return (
                await require_provider(
                    session, organization_id=scope.organization_id, workspace_id=workspace_id, provider_id=provider_id
                )
            ).to_resource()

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        provider_id: str,
        if_match: str,
        request: UpdateMemoryProviderRequest,
    ) -> MemoryProvider:
        try:
            async with transaction(self.sessions) as session:
                scope = await authorize_scope(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.memory_provider_manage
                )
                record = await require_provider(
                    session,
                    organization_id=scope.organization_id,
                    workspace_id=workspace_id,
                    provider_id=provider_id,
                    owning_scope=True,
                    lock=True,
                )
                require_etag(record, if_match)
                changes: list[str] = []
                if request.name is not None and record.name != request.name:
                    record.name = request.name
                    changes.append("name")
                if request.enabled is not None and record.enabled != request.enabled:
                    record.enabled = request.enabled
                    changes.append("enabled")
                if request.credential is not None:
                    credentials = self._validate_credentials(record.type, request.credential)
                    record.replace_credential(json.dumps(credentials), self.protector)
                    changes.append("credential")
                if changes:
                    record.normalized_name = record.name.casefold()
                    record.updated_at = max(self.clock(), assume_utc(record.updated_at) + timedelta(microseconds=1))
                    record.updated_by_type = actor.principal.principal_type.value
                    record.updated_by_id = actor.principal.principal_id
                    self.audit(session, actor, record, "update", tuple(sorted(changes)))
                    await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            _name_conflict(error)
            raise

    def _validate_configuration(self, provider_type: str, value: object) -> dict[str, object]:
        try:
            return (
                self.catalog[provider_type]
                .configuration_model.model_validate(value)
                .model_dump(mode="json", by_alias=True, exclude_none=False)
            )
        except (KeyError, ValueError) as error:
            raise MemoryProviderError(
                "memory_provider_configuration_invalid",
                "Memory Provider configuration is invalid.",
                category=ErrorCategory.invalid_request,
            ) from error

    def _validate_credentials(self, provider_type: str, value: object) -> dict[str, object]:
        try:
            return credential_payload(self.catalog[provider_type].credential_model.model_validate(value))
        except (KeyError, ValueError) as error:
            raise MemoryProviderError(
                "memory_provider_credential_invalid",
                "Memory Provider credential is invalid.",
                category=ErrorCategory.invalid_request,
            ) from error

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        limit: int = 50,
        cursor: str | None = None,
        provider_type: str | None = None,
        enabled: bool | None = None,
    ) -> MemoryProviderCollection:
        scope_key = _cursor_scope(actor, workspace_id, "accounts", provider_type, enabled)
        position = _position(cursor, scope_key)
        async with transaction(self.sessions) as session:
            scope = await authorize_scope(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.memory_provider_read
            )
            query = select(MemoryProviderRecord).where(
                scope.visible(MemoryProviderRecord.organization_id, MemoryProviderRecord.workspace_id)
            )
            if provider_type is not None:
                query = query.where(MemoryProviderRecord.type == provider_type)
            if enabled is not None:
                query = query.where(MemoryProviderRecord.enabled == enabled)
            if position is not None:
                name, item_id = _account_position(position)
                query = query.where(
                    or_(
                        MemoryProviderRecord.normalized_name > name,
                        and_(MemoryProviderRecord.normalized_name == name, MemoryProviderRecord.id > item_id),
                    )
                )
            records = (
                await session.scalars(
                    query.order_by(MemoryProviderRecord.normalized_name, MemoryProviderRecord.id).limit(limit + 1)
                )
            ).all()
            page = records[:limit]
            next_cursor = (
                encode_collection_cursor({"name": page[-1].normalized_name, "id": page[-1].id}, scope=scope_key)
                if len(records) > limit
                else None
            )
            return MemoryProviderCollection(
                items=tuple(record.to_resource() for record in page), next_cursor=next_cursor
            )

    async def references(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        provider_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> MemoryProviderReferenceCollection:
        scope_key = _cursor_scope(actor, workspace_id, provider_id, None, None)
        position = _position(cursor, scope_key)
        async with transaction(self.sessions) as session:
            scope = await authorize_scope(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.memory_provider_read
            )
            await require_provider(
                session, organization_id=scope.organization_id, workspace_id=workspace_id, provider_id=provider_id
            )
            query = (
                select(AgentRevisionRecord, AgentRecord)
                .join(AgentRecord, AgentRecord.id == AgentRevisionRecord.agent_id)
                .where(
                    AgentRecord.organization_id == scope.organization_id,
                    AgentRevisionRecord.config["memory"]["provider_id"].as_string() == provider_id,
                )
            )
            if workspace_id is not None:
                query = query.where(AgentRecord.workspace_id == workspace_id)
            if position is not None:
                agent_id, revision_id = _account_position(position)
                version = position.get("version")
                if type(version) is not int or version < 1:
                    raise _invalid_cursor()
                query = query.where(
                    or_(
                        AgentRevisionRecord.agent_id > agent_id,
                        and_(AgentRevisionRecord.agent_id == agent_id, AgentRevisionRecord.version > version),
                        and_(
                            AgentRevisionRecord.agent_id == agent_id,
                            AgentRevisionRecord.version == version,
                            AgentRevisionRecord.id > revision_id,
                        ),
                    )
                )
            query = query.order_by(AgentRevisionRecord.agent_id, AgentRevisionRecord.version, AgentRevisionRecord.id)
            items: list[MemoryProviderReference] = []
            # Page in bounded batches, applying Agent visibility before counting.
            offset = 0
            while len(items) <= limit:
                rows = (await session.execute(query.offset(offset).limit(100))).all()
                if not rows:
                    break
                for revision, agent in rows:
                    try:
                        await authorize_agent(
                            session,
                            actor=actor,
                            workspace_id=agent.workspace_id,
                            agent_id=agent.id,
                            action=WorkspaceAction.agent_read,
                        )
                    except AuthorizationError:
                        continue
                    items.append(
                        MemoryProviderReference(
                            agent_id=agent.id,
                            agent_revision_id=revision.id,
                            version=revision.version,
                            is_current=agent.current_revision_id == revision.id,
                        )
                    )
                    if len(items) > limit:
                        break
                offset += len(rows)
            page = items[:limit]
            next_cursor = None
            if len(items) > limit:
                last = page[-1]
                next_cursor = encode_collection_cursor(
                    {"name": last.agent_id, "id": last.agent_revision_id, "version": last.version}, scope=scope_key
                )
            return MemoryProviderReferenceCollection(items=tuple(page), next_cursor=next_cursor)

    def audit(
        self,
        session: AsyncSession,
        actor: AuthenticatedActor,
        record: MemoryProviderRecord,
        action: str,
        fields: tuple[str, ...] = (),
        *,
        code: str | None = None,
    ) -> None:
        session.add(
            security_audit_record(
                audit_id=new_object_id("aud"),
                actor=actor,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                action=f"memory_provider.{action}",
                resource_type="memory_provider",
                resource_id=record.id,
                outcome="failure" if code else "success",
                occurred_at=self.clock(),
                details={"changed_fields": list(fields), "code": code},
            )
        )


def _cursor_scope(
    actor: AuthenticatedActor, workspace_id: str | None, resource: str, provider_type: str | None, enabled: bool | None
) -> dict[str, object]:
    return {
        "resource": f"memory_provider:{resource}",
        "organization_id": actor.boundary_organization_id,
        "workspace_id": workspace_id,
        "principal": actor.principal.model_dump(mode="json"),
        "type": provider_type,
        "enabled": enabled,
    }


def _invalid_cursor() -> MemoryProviderError:
    return MemoryProviderError("invalid_cursor", "Invalid collection cursor.", category=ErrorCategory.invalid_request)


def _position(cursor: str | None, scope: dict[str, object]) -> dict[str, object] | None:
    try:
        return decode_collection_cursor(cursor, scope=scope) if cursor is not None else None
    except (InvalidCollectionCursorError, CollectionCursorMismatchError) as error:
        raise _invalid_cursor() from error


def _account_position(position: dict[str, object]) -> tuple[str, str]:
    name, item_id = position.get("name"), position.get("id")
    if not isinstance(name, str) or not isinstance(item_id, str):
        raise _invalid_cursor()
    return name, item_id


def _name_conflict(error: IntegrityError) -> None:
    # Only the two owning-scope name constraints map to a reconciliation conflict.
    if any(
        is_unique_conflict(error, constraint=constraint, sqlite_columns=columns)
        for constraint, columns in (
            ("uq_memory_providers_workspace_name", "memory_providers.workspace_id, memory_providers.normalized_name"),
            (
                "uq_memory_providers_organization_normalized_name",
                "memory_providers.organization_id, memory_providers.normalized_name",
            ),
        )
    ):
        raise MemoryProviderError(
            "memory_provider_name_conflict",
            "A Memory Provider with this name already exists in this scope.",
            category=ErrorCategory.conflict,
        ) from error
