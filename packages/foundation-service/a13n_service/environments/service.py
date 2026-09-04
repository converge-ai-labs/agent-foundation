"""Transactional Foundation Environment Management application service."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.idempotency import is_evidence_unique_race
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now

from .access import (
    authorize_environment_workspace as _authorize,
)
from .access import (
    load_environment as _load_environment,
)
from .access import (
    load_environment_revision as _load_revision,
)
from .access import (
    require_credential_bindings as _require_credential_bindings,
)
from .access import (
    require_provider_selection as _require_selection,
)
from .catalog import FoundationEnvironmentProviderCatalog
from .cursors import (
    EnvironmentCursorError,
    decode_environment_cursor,
    decode_revision_cursor,
    encode_environment_cursor,
    encode_revision_cursor,
)
from .domain import (
    CreateEnvironmentRequest,
    CreateEnvironmentRevisionRequest,
    Environment,
    EnvironmentCollection,
    EnvironmentProviderCatalogEntry,
    EnvironmentProviderCatalogEntryCollection,
    EnvironmentProviderSelection,
    EnvironmentRevision,
    EnvironmentRevisionCollection,
    EnvironmentRevisionTestResult,
    PutEnvironmentProviderSelectionRequest,
    UpdateEnvironmentRequest,
    environment_logical_digest,
    new_environment_id,
    new_environment_revision_id,
)
from .errors import (
    EnvironmentManagementError,
    environment_provider_not_found,
    environment_version_conflict,
)
from .models import EnvironmentProviderSelectionRecord, EnvironmentRecord, EnvironmentRevisionRecord
from .persistence import (
    audit_record as _audit,
)
from .persistence import (
    evidence_record as _evidence,
)
from .persistence import (
    load_replay as _load_replay,
)
from .persistence import (
    normalized_name as _normalized_name,
)
from .persistence import (
    precondition_failed as _precondition_failed,
)
from .persistence import replay_environment_create, replay_environment_revision_create
from .persistence import (
    request_identity as _identity,
)
from .persistence import (
    require_etag as _require_etag,
)
from .testing import EnvironmentAttachmentTester


@dataclass(frozen=True, slots=True)
class EnvironmentRevisionMutationResult:
    revision: EnvironmentRevision
    created: bool


class EnvironmentManagementService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        catalog: FoundationEnvironmentProviderCatalog,
        *,
        clock=None,
        attachment_tester: EnvironmentAttachmentTester | None = None,
    ) -> None:
        self._sessions = sessions
        self._catalog = catalog
        self._clock = clock or utc_now
        self._attachment_tester = attachment_tester

    async def list_provider_catalog(
        self,
        *,
        actor: AuthenticatedActor,
    ) -> EnvironmentProviderCatalogEntryCollection:
        await self._authorize(actor, actor.boundary_workspace_id, WorkspaceAction.environment_provider_read)
        return EnvironmentProviderCatalogEntryCollection(items=self._catalog.entries())

    async def get_provider_catalog_entry(
        self,
        *,
        actor: AuthenticatedActor,
        provider_key: str,
    ) -> EnvironmentProviderCatalogEntry:
        await self._authorize(actor, actor.boundary_workspace_id, WorkspaceAction.environment_provider_read)
        return self._catalog.entry(provider_key)

    async def get_provider_selection(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        provider_key: str,
    ) -> EnvironmentProviderSelection:
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.environment_provider_read,
            )
            record = await session.scalar(
                select(EnvironmentProviderSelectionRecord).where(
                    EnvironmentProviderSelectionRecord.organization_id == workspace.organization_id,
                    EnvironmentProviderSelectionRecord.workspace_id == workspace_id,
                    EnvironmentProviderSelectionRecord.provider_key == provider_key,
                )
            )
            if record is None:
                raise environment_provider_not_found()
            return record.to_resource()

    async def put_provider_selection(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        provider_key: str,
        request: PutEnvironmentProviderSelectionRequest,
        if_match: str | None = None,
    ) -> EnvironmentProviderSelection:
        entry = self._catalog.entry(provider_key)
        now = self._clock()
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.environment_provider_select,
            )
            record = await session.scalar(
                select(EnvironmentProviderSelectionRecord)
                .where(
                    EnvironmentProviderSelectionRecord.organization_id == workspace.organization_id,
                    EnvironmentProviderSelectionRecord.workspace_id == workspace_id,
                    EnvironmentProviderSelectionRecord.provider_key == provider_key,
                )
                .with_for_update()
            )
            if record is None:
                if if_match is not None:
                    raise _precondition_failed(None)
                record = EnvironmentProviderSelectionRecord(
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    provider_key=provider_key,
                    provider_package_revision_id=None,
                    provider_lock=entry.provider_lock.model_dump(mode="json"),
                    enabled=request.enabled,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                _require_etag(record, if_match, resource_id=f"{workspace_id}:{provider_key}")
                current_lock = entry.provider_lock.model_dump(mode="json")
                if record.enabled == request.enabled and record.provider_lock == current_lock:
                    return record.to_resource()
                record.enabled = request.enabled
                record.provider_package_revision_id = None
                record.provider_lock = current_lock
                record.updated_by_type = actor.principal.principal_type.value
                record.updated_by_id = actor.principal.principal_id
                record.updated_at = now
            session.add(
                _audit(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    action="environment_provider.select",
                    resource_type="environment_provider",
                    resource_id=provider_key,
                    now=now,
                )
            )
            await session.flush()
            return record.to_resource()

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: CreateEnvironmentRequest,
    ) -> Environment:
        identity = _identity(idempotency_key, request)
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.environment_manage,
                )
                replay = await _load_replay(
                    session,
                    actor=actor,
                    operation="environment.create",
                    scope_id=workspace_id,
                    identity=identity,
                    now=now,
                )
                if replay is not None:
                    record = await _load_environment(
                        session,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        environment_id=replay[1],
                    )
                    return record.to_resource()
                validated = self._catalog.validate_connection(request.connection)
                entry = self._catalog.entry(validated.spec.provider_key)
                selection = await _require_selection(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    provider_key=validated.spec.provider_key,
                    expected_lock=entry.provider_lock.model_dump(mode="json"),
                    for_update=True,
                )
                await _require_credential_bindings(
                    session,
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    bindings=request.credential_bindings,
                    require_bind_authority=True,
                )
                environment_id = new_environment_id()
                revision_id = new_environment_revision_id()
                digest = environment_logical_digest(
                    connection=validated.spec,
                    provider_package_revision_id=selection.provider_package_revision_id,
                    provider_lock=entry.provider_lock,
                    credential_bindings=request.credential_bindings,
                    access=request.access,
                    target_key=validated.target_key,
                )
                environment = EnvironmentRecord(
                    id=environment_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    name=request.name,
                    normalized_name=_normalized_name(request.name),
                    description=request.description,
                    version=1,
                    current_revision_id=revision_id,
                    archived_at=None,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                revision = EnvironmentRevisionRecord(
                    id=revision_id,
                    environment_id=environment_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    version=1,
                    connection=validated.spec.model_dump(mode="json"),
                    provider_package_revision_id=selection.provider_package_revision_id,
                    provider_lock=entry.provider_lock.model_dump(mode="json"),
                    credential_bindings=[item.model_dump(mode="json") for item in request.credential_bindings],
                    access=request.access.value,
                    target_key=validated.target_key,
                    logical_digest_sha256=digest,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                )
                session.add_all((environment, revision))
                session.add(
                    _evidence(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        operation="environment.create",
                        scope_id=workspace_id,
                        identity=identity,
                        result_kind="environment",
                        result_ref=environment_id,
                        now=now,
                    )
                )
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        action="environment.create",
                        resource_type="environment",
                        resource_id=environment_id,
                        now=now,
                    )
                )
                await session.flush()
                return environment.to_resource()
        except IntegrityError as error:
            if is_evidence_unique_race(error):
                return await replay_environment_create(
                    self._sessions,
                    actor=actor,
                    workspace_id=workspace_id,
                    identity=identity,
                    now=now,
                )
            raise EnvironmentManagementError(
                "environment_name_conflict",
                "An Environment with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
        include_archived: bool,
    ) -> EnvironmentCollection:
        scope = {"workspace_id": workspace_id, "include_archived": include_archived}
        try:
            after = decode_environment_cursor(cursor, scope=scope) if cursor is not None else None
        except EnvironmentCursorError as error:
            raise EnvironmentManagementError(
                "invalid_cursor", "The collection cursor is invalid.", status_code=400
            ) from error
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.environment_read,
            )
            query = select(EnvironmentRecord).where(
                EnvironmentRecord.organization_id == workspace.organization_id,
                EnvironmentRecord.workspace_id == workspace_id,
            )
            if not include_archived:
                query = query.where(EnvironmentRecord.archived_at.is_(None))
            if after is not None:
                updated_at, environment_id = after
                query = query.where(
                    or_(
                        EnvironmentRecord.updated_at < updated_at,
                        and_(EnvironmentRecord.updated_at == updated_at, EnvironmentRecord.id < environment_id),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(EnvironmentRecord.updated_at.desc(), EnvironmentRecord.id.desc()).limit(
                            limit + 1
                        )
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = None
            if len(records) > limit and page:
                next_cursor = encode_environment_cursor(
                    updated_at=page[-1].updated_at,
                    environment_id=page[-1].id,
                    scope=scope,
                )
            return EnvironmentCollection(items=tuple(item.to_resource() for item in page), next_cursor=next_cursor)

    async def get(self, *, actor: AuthenticatedActor, environment_id: str) -> Environment:
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.environment_read,
            )
            record = await _load_environment(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                environment_id=environment_id,
            )
            return record.to_resource()

    async def patch(
        self,
        *,
        actor: AuthenticatedActor,
        environment_id: str,
        if_match: str,
        request: UpdateEnvironmentRequest,
    ) -> Environment:
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize(
                    session,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    action=WorkspaceAction.environment_manage,
                )
                record = await _load_environment(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    environment_id=environment_id,
                    for_update=True,
                )
                _require_etag(record, if_match, resource_id=record.id)
                if "name" in request.model_fields_set and request.name is not None:
                    record.name = request.name
                    record.normalized_name = _normalized_name(request.name)
                if "description" in request.model_fields_set:
                    record.description = request.description
                if "archived" in request.model_fields_set:
                    record.archived_at = now if request.archived else None
                record.updated_by_type = actor.principal.principal_type.value
                record.updated_by_id = actor.principal.principal_id
                record.updated_at = now
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        action="environment.update",
                        resource_type="environment",
                        resource_id=environment_id,
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            raise EnvironmentManagementError(
                "environment_name_conflict",
                "An Environment with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def create_revision(
        self,
        *,
        actor: AuthenticatedActor,
        environment_id: str,
        idempotency_key: str,
        request: CreateEnvironmentRevisionRequest,
    ) -> EnvironmentRevisionMutationResult:
        identity = _identity(idempotency_key, request)
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize(
                    session,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    action=WorkspaceAction.environment_manage,
                )
                replay = await _load_replay(
                    session,
                    actor=actor,
                    operation="environment.revision.create",
                    scope_id=environment_id,
                    identity=identity,
                    now=now,
                )
                if replay is not None:
                    revision = await _load_revision(
                        session,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        revision_id=replay[1],
                    )
                    return EnvironmentRevisionMutationResult(
                        revision=revision.to_resource(),
                        created=replay[0] == "environment_revision_created",
                    )
                validated = self._catalog.validate_connection(request.connection)
                entry = self._catalog.entry(validated.spec.provider_key)
                environment = await _load_environment(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    environment_id=environment_id,
                    for_update=True,
                )
                if environment.archived_at is not None:
                    raise EnvironmentManagementError(
                        "environment_archived",
                        "The Environment is archived.",
                        status_code=409,
                    )
                if environment.version != request.expected_version:
                    raise environment_version_conflict(environment.version)
                selection = await _require_selection(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    provider_key=validated.spec.provider_key,
                    expected_lock=entry.provider_lock.model_dump(mode="json"),
                    for_update=True,
                )
                await _require_credential_bindings(
                    session,
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    bindings=request.credential_bindings,
                    require_bind_authority=True,
                )
                lock = entry.provider_lock
                digest = environment_logical_digest(
                    connection=validated.spec,
                    provider_package_revision_id=selection.provider_package_revision_id,
                    provider_lock=lock,
                    credential_bindings=request.credential_bindings,
                    access=request.access,
                    target_key=validated.target_key,
                )
                current = await _load_revision(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    revision_id=environment.current_revision_id,
                    for_update=True,
                )
                created = current.logical_digest_sha256 != digest
                if created:
                    revision = EnvironmentRevisionRecord(
                        id=new_environment_revision_id(),
                        environment_id=environment.id,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        version=current.version + 1,
                        connection=validated.spec.model_dump(mode="json"),
                        provider_package_revision_id=selection.provider_package_revision_id,
                        provider_lock=lock.model_dump(mode="json"),
                        credential_bindings=[item.model_dump(mode="json") for item in request.credential_bindings],
                        access=request.access.value,
                        target_key=validated.target_key,
                        logical_digest_sha256=digest,
                        created_by_type=actor.principal.principal_type.value,
                        created_by_id=actor.principal.principal_id,
                        created_at=now,
                    )
                    session.add(revision)
                    environment.current_revision_id = revision.id
                    environment.version += 1
                    environment.updated_by_type = actor.principal.principal_type.value
                    environment.updated_by_id = actor.principal.principal_id
                    environment.updated_at = now
                else:
                    revision = current
                result_kind = "environment_revision_created" if created else "environment_revision_unchanged"
                session.add(
                    _evidence(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        operation="environment.revision.create",
                        scope_id=environment_id,
                        identity=identity,
                        result_kind=result_kind,
                        result_ref=revision.id,
                        now=now,
                    )
                )
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        action="environment.revision.create",
                        resource_type="environment_revision",
                        resource_id=revision.id,
                        now=now,
                    )
                )
                await session.flush()
                return EnvironmentRevisionMutationResult(revision=revision.to_resource(), created=created)
        except IntegrityError as error:
            if is_evidence_unique_race(error):
                revision, created = await replay_environment_revision_create(
                    self._sessions,
                    actor=actor,
                    environment_id=environment_id,
                    identity=identity,
                    now=now,
                )
                return EnvironmentRevisionMutationResult(revision=revision, created=created)
            raise

    async def list_revisions(
        self,
        *,
        actor: AuthenticatedActor,
        environment_id: str,
        limit: int,
        cursor: str | None,
    ) -> EnvironmentRevisionCollection:
        scope: dict[str, object] = {"environment_id": environment_id}
        try:
            after = decode_revision_cursor(cursor, scope=scope) if cursor is not None else None
        except EnvironmentCursorError as error:
            raise EnvironmentManagementError(
                "invalid_cursor", "The collection cursor is invalid.", status_code=400
            ) from error
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.environment_read,
            )
            await _load_environment(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                environment_id=environment_id,
            )
            query = select(EnvironmentRevisionRecord).where(
                EnvironmentRevisionRecord.organization_id == workspace.organization_id,
                EnvironmentRevisionRecord.workspace_id == workspace.workspace_id,
                EnvironmentRevisionRecord.environment_id == environment_id,
            )
            if after is not None:
                number, revision_id = after
                query = query.where(
                    or_(
                        EnvironmentRevisionRecord.version < number,
                        and_(
                            EnvironmentRevisionRecord.version == number,
                            EnvironmentRevisionRecord.id < revision_id,
                        ),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(
                            EnvironmentRevisionRecord.version.desc(),
                            EnvironmentRevisionRecord.id.desc(),
                        ).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = None
            if len(records) > limit and page:
                next_cursor = encode_revision_cursor(
                    version=page[-1].version,
                    revision_id=page[-1].id,
                    scope=scope,
                )
            return EnvironmentRevisionCollection(
                items=tuple(item.to_resource().summary() for item in page),
                next_cursor=next_cursor,
            )

    async def get_revision(self, *, actor: AuthenticatedActor, revision_id: str) -> EnvironmentRevision:
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.environment_read,
            )
            record = await _load_revision(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                revision_id=revision_id,
            )
            return record.to_resource()

    async def test_revision(
        self,
        *,
        actor: AuthenticatedActor,
        revision_id: str,
    ) -> EnvironmentRevisionTestResult:
        if self._attachment_tester is None:
            raise EnvironmentManagementError(
                "environment_attachment_testing_unavailable",
                "Environment attachment testing is unavailable.",
                status_code=503,
            )
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.environment_test,
            )
            record = await _load_revision(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                revision_id=revision_id,
            )
            environment = await _load_environment(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                environment_id=record.environment_id,
            )
            if environment.archived_at is not None:
                raise EnvironmentManagementError(
                    "environment_archived",
                    "The Environment is archived.",
                    status_code=409,
                )
            revision = record.to_resource()
            entry = self._catalog.entry(revision.connection.provider_key)
            await _require_selection(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                provider_key=revision.connection.provider_key,
                expected_lock=revision.provider_lock.model_dump(mode="json"),
                for_update=False,
            )
            if entry.provider_lock != revision.provider_lock:
                raise EnvironmentManagementError(
                    "environment_provider_lock_changed",
                    "The Environment Provider lock no longer matches this deployment.",
                    status_code=409,
                )
            await _require_credential_bindings(
                session,
                actor=actor,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                bindings=revision.credential_bindings,
                require_bind_authority=False,
            )
        await self._attachment_tester(
            actor=actor,
            organization_id=workspace.organization_id,
            workspace_id=workspace.workspace_id,
            revision=revision,
        )
        return EnvironmentRevisionTestResult()

    async def _authorize(
        self,
        actor: AuthenticatedActor,
        workspace_id: str,
        action: WorkspaceAction,
    ) -> None:
        async with short_session(self._sessions) as session:
            await _authorize(session, actor=actor, workspace_id=workspace_id, action=action)
