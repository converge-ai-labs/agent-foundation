"""Web Provider account management; transactions never span provider requests."""

import json
from datetime import timedelta

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
from a13n_service.iam import AuthenticatedActor, authorize_agent
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import AuthorizationError, WorkspaceAction
from a13n_service.iam.resource_scope import authorize_scope
from a13n_service.ids import new_object_id
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import is_unique_conflict, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import (
    CreateWebProviderRequest,
    UpdateWebProviderRequest,
    WebProvider,
    WebProviderCollection,
    WebProviderDefinitionCollection,
    WebProviderReference,
    WebProviderReferenceCollection,
)
from .models import WebProviderRecord
from .registry import WebProviderRegistry
from .resources import WebProviderError, require_etag, require_provider


class WebProviderService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        protector: SecretProtector,
        registry: WebProviderRegistry,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self.sessions = sessions
        self.protector = protector
        self.clock = clock
        self.registry = registry

    async def type_definitions(self, *, actor: AuthenticatedActor) -> WebProviderDefinitionCollection:
        async with transaction(self.sessions) as session:
            await authorize_scope(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.web_provider_read,
            )
        return WebProviderDefinitionCollection(items=self.registry.definitions())

    async def create(
        self, *, actor: AuthenticatedActor, workspace_id: str | None, request: CreateWebProviderRequest
    ) -> WebProvider:
        try:
            async with transaction(self.sessions) as session:
                scope = await authorize_scope(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.web_provider_manage
                )
                now = self.clock()
                configuration = self._validate_configuration(request.type, request.configuration)
                definition = self.registry.require(request.type)
                if definition.credential_required != (request.credential is not None) or (
                    not definition.credential_required and "credential" in request.model_fields_set
                ):
                    raise WebProviderError(
                        "web_provider_credential_invalid",
                        "Web Provider credential does not match its type.",
                        category=ErrorCategory.invalid_request,
                    )
                credentials = (
                    self._validate_credentials(request.type, request.credential)
                    if request.credential is not None
                    else None
                )
                record = WebProviderRecord(
                    id=new_object_id("wprov"),
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
                if credentials is not None:
                    record.replace_credential(json.dumps(credentials), self.protector)
                session.add(record)
                self.audit(session, actor, record, "create", tuple(request.model_fields_set))
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            _name_conflict(error)
            raise

    async def get(self, *, actor: AuthenticatedActor, workspace_id: str | None, provider_id: str) -> WebProvider:
        async with transaction(self.sessions) as session:
            scope = await authorize_scope(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.web_provider_read
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
        request: UpdateWebProviderRequest,
    ) -> WebProvider:
        try:
            async with transaction(self.sessions) as session:
                scope = await authorize_scope(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.web_provider_manage
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
                for key in request.model_fields_set - {"credential"}:
                    value = getattr(request, key)
                    if key == "configuration":
                        value = self._validate_configuration(record.type, value)
                    if getattr(record, key) != value:
                        setattr(record, key, value)
                        changes.append(key)
                if request.credential is not None:
                    if not self.registry.require(record.type).credential_required:
                        raise WebProviderError(
                            "web_provider_credential_invalid",
                            "Web Provider does not accept a credential.",
                            category=ErrorCategory.invalid_request,
                        )
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
            return self.registry.validate_configuration(provider_type, value)
        except ValueError as error:
            raise WebProviderError(
                "web_provider_configuration_invalid",
                "Web Provider configuration is invalid.",
                category=ErrorCategory.invalid_request,
            ) from error

    def _validate_credentials(self, provider_type: str, value: object) -> dict[str, object]:
        try:
            return self.registry.credential_payload(provider_type, value)
        except ValueError as error:
            raise WebProviderError(
                "web_provider_credential_invalid",
                "Web Provider credential is invalid.",
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
    ) -> WebProviderCollection:
        scope_key = _cursor_scope(actor, workspace_id, "accounts", provider_type, enabled)
        position = _position(cursor, scope_key)
        async with transaction(self.sessions) as session:
            scope = await authorize_scope(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.web_provider_read
            )
            query = select(WebProviderRecord).where(
                scope.visible(WebProviderRecord.organization_id, WebProviderRecord.workspace_id)
            )
            if provider_type is not None:
                query = query.where(WebProviderRecord.type == provider_type)
            if enabled is not None:
                query = query.where(WebProviderRecord.enabled == enabled)
            if position is not None:
                name, item_id = _account_position(position)
                query = query.where(
                    or_(
                        WebProviderRecord.normalized_name > name,
                        and_(WebProviderRecord.normalized_name == name, WebProviderRecord.id > item_id),
                    )
                )
            records = (
                await session.scalars(
                    query.order_by(WebProviderRecord.normalized_name, WebProviderRecord.id).limit(limit + 1)
                )
            ).all()
            page = records[:limit]
            next_cursor = (
                encode_collection_cursor({"name": page[-1].normalized_name, "id": page[-1].id}, scope=scope_key)
                if len(records) > limit
                else None
            )
            return WebProviderCollection(items=tuple(record.to_resource() for record in page), next_cursor=next_cursor)

    async def references(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        provider_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> WebProviderReferenceCollection:
        scope_key = _cursor_scope(actor, workspace_id, provider_id, None, None)
        position = _position(cursor, scope_key)
        async with transaction(self.sessions) as session:
            scope = await authorize_scope(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.web_provider_read
            )
            await require_provider(
                session, organization_id=scope.organization_id, workspace_id=workspace_id, provider_id=provider_id
            )
            query = (
                select(AgentRevisionRecord, AgentRecord)
                .join(AgentRecord, AgentRecord.id == AgentRevisionRecord.agent_id)
                .where(
                    AgentRecord.organization_id == scope.organization_id,
                    or_(
                        AgentRevisionRecord.config["toolsets"]["web"]["tools"]["search"]["config"][
                            "provider_id"
                        ].as_string()
                        == provider_id,
                        AgentRevisionRecord.config["toolsets"]["web"]["tools"]["scrape"]["config"][
                            "provider_id"
                        ].as_string()
                        == provider_id,
                    ),
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
            items: list[WebProviderReference] = []
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
                        WebProviderReference(
                            agent_id=agent.id,
                            agent_revision_id=revision.id,
                            version=revision.version,
                            is_current=agent.default_revision_id == revision.id,
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
            return WebProviderReferenceCollection(items=tuple(page), next_cursor=next_cursor)

    def audit(
        self,
        session: AsyncSession,
        actor: AuthenticatedActor,
        record: WebProviderRecord,
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
                action=f"web_provider.{action}",
                resource_type="web_provider",
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
        "resource": f"web_provider:{resource}",
        "organization_id": actor.boundary_organization_id,
        "workspace_id": workspace_id,
        "principal": actor.principal.model_dump(mode="json"),
        "type": provider_type,
        "enabled": enabled,
    }


def _invalid_cursor() -> WebProviderError:
    return WebProviderError("invalid_cursor", "Invalid collection cursor.", category=ErrorCategory.invalid_request)


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
        is_unique_conflict(error, constraint=constraint)
        for constraint in ("uq_web_providers_workspace_name", "uq_web_providers_organization_normalized_name")
    ):
        raise WebProviderError(
            "web_provider_name_conflict",
            "A Web Provider with this name already exists in this scope.",
            category=ErrorCategory.conflict,
        ) from error
