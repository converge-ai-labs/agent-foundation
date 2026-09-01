"""Transactional Foundation Environment Management application service."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from a13n_environment_provider import EnvironmentProviderError, EnvironmentProviderSpec
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.secrets.domain import WorkspaceSecretCredential
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import short_session, transaction

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
    EnvironmentCredentialBinding,
    EnvironmentProviderCatalogEntry,
    EnvironmentProviderCatalogEntryCollection,
    EnvironmentProviderSelection,
    EnvironmentRevision,
    EnvironmentRevisionCollection,
    PatchEnvironmentRequest,
    PutEnvironmentProviderSelectionRequest,
    environment_logical_digest,
    new_environment_id,
    new_environment_revision_id,
)
from .errors import (
    EnvironmentManagementError,
    environment_not_found,
    environment_provider_disabled,
    environment_provider_not_found,
    environment_provider_version_conflict,
    environment_revision_not_found,
    environment_version_conflict,
)
from .models import EnvironmentProviderSelectionRecord, EnvironmentRecord, EnvironmentRevisionRecord

IDEMPOTENCY_LIFETIME = timedelta(hours=24)
_MAX_IDEMPOTENCY_KEY_BYTES = 512


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
    ) -> None:
        self._sessions = sessions
        self._catalog = catalog
        self._clock = clock or (lambda: datetime.now(UTC))

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
                if request.expected_version is not None:
                    raise environment_provider_version_conflict(None)
                record = EnvironmentProviderSelectionRecord(
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    provider_key=provider_key,
                    provider_package_revision_id=None,
                    provider_lock=entry.provider_lock.model_dump(mode="json"),
                    enabled=request.enabled,
                    version=1,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                if request.expected_version != record.version:
                    raise environment_provider_version_conflict(record.version)
                current_lock = entry.provider_lock.model_dump(mode="json")
                if record.enabled == request.enabled and record.provider_lock == current_lock:
                    return record.to_resource()
                record.enabled = request.enabled
                record.provider_package_revision_id = None
                record.provider_lock = current_lock
                record.version += 1
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
        normalized_provider = self._validate_provider(request.provider)
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
                selection = await _require_selection(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    provider_key=normalized_provider.provider_key,
                    expected_lock=self._catalog.entry(normalized_provider.provider_key).provider_lock.model_dump(
                        mode="json"
                    ),
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
                    provider=normalized_provider,
                    provider_package_revision_id=selection.provider_package_revision_id,
                    provider_lock=self._catalog.entry(normalized_provider.provider_key).provider_lock,
                    credential_bindings=request.credential_bindings,
                    access=request.access,
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
                    revision_number=1,
                    provider=normalized_provider.model_dump(mode="json"),
                    provider_package_revision_id=selection.provider_package_revision_id,
                    provider_lock=self._catalog.entry(normalized_provider.provider_key).provider_lock.model_dump(
                        mode="json"
                    ),
                    credential_bindings=[item.model_dump(mode="json") for item in request.credential_bindings],
                    access=request.access.value,
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
            if _is_idempotency_race(error):
                return await self._replay_create(
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
        request: PatchEnvironmentRequest,
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
                if record.version != request.expected_version:
                    raise environment_version_conflict(record.version)
                if "name" in request.model_fields_set and request.name is not None:
                    record.name = request.name
                    record.normalized_name = _normalized_name(request.name)
                if "description" in request.model_fields_set:
                    record.description = request.description
                if "archived" in request.model_fields_set:
                    record.archived_at = now if request.archived else None
                record.version += 1
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
        normalized_provider = self._validate_provider(request.provider)
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
                if environment.version != request.expected_environment_version:
                    raise environment_version_conflict(environment.version)
                selection = await _require_selection(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    provider_key=normalized_provider.provider_key,
                    expected_lock=self._catalog.entry(normalized_provider.provider_key).provider_lock.model_dump(
                        mode="json"
                    ),
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
                lock = self._catalog.entry(normalized_provider.provider_key).provider_lock
                digest = environment_logical_digest(
                    provider=normalized_provider,
                    provider_package_revision_id=selection.provider_package_revision_id,
                    provider_lock=lock,
                    credential_bindings=request.credential_bindings,
                    access=request.access,
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
                        revision_number=current.revision_number + 1,
                        provider=normalized_provider.model_dump(mode="json"),
                        provider_package_revision_id=selection.provider_package_revision_id,
                        provider_lock=lock.model_dump(mode="json"),
                        credential_bindings=[item.model_dump(mode="json") for item in request.credential_bindings],
                        access=request.access.value,
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
            if _is_idempotency_race(error):
                return await self._replay_revision(
                    actor=actor,
                    environment_id=environment_id,
                    identity=identity,
                    now=now,
                )
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
                        EnvironmentRevisionRecord.revision_number < number,
                        and_(
                            EnvironmentRevisionRecord.revision_number == number,
                            EnvironmentRevisionRecord.id < revision_id,
                        ),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(
                            EnvironmentRevisionRecord.revision_number.desc(),
                            EnvironmentRevisionRecord.id.desc(),
                        ).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = None
            if len(records) > limit and page:
                next_cursor = encode_revision_cursor(
                    revision_number=page[-1].revision_number,
                    revision_id=page[-1].id,
                    scope=scope,
                )
            return EnvironmentRevisionCollection(
                items=tuple(item.to_resource() for item in page),
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

    def _validate_provider(self, spec: EnvironmentProviderSpec) -> EnvironmentProviderSpec:
        entry = self._catalog.entry(spec.provider_key)
        if spec.schema_version not in entry.configuration_versions:
            raise EnvironmentManagementError(
                "provider_schema_unsupported",
                "The Environment Provider configuration version is unsupported.",
                status_code=400,
            )
        provider = self._catalog.providers[spec.provider_key]
        try:
            configuration = provider.validate_configuration(
                schema_version=spec.schema_version,
                value=spec.configuration,
            )
        except EnvironmentProviderError as error:
            safe = error.safe_projection()
            raise EnvironmentManagementError(safe.code, safe.message, status_code=400) from error
        return EnvironmentProviderSpec(
            provider_key=spec.provider_key,
            schema_version=spec.schema_version,
            configuration=configuration.model_dump(mode="json"),
        )

    async def _authorize(
        self,
        actor: AuthenticatedActor,
        workspace_id: str,
        action: WorkspaceAction,
    ) -> None:
        async with short_session(self._sessions) as session:
            await _authorize(session, actor=actor, workspace_id=workspace_id, action=action)

    async def _replay_create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        identity: tuple[str, str],
        now: datetime,
    ) -> Environment:
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
            if replay is None:
                raise EnvironmentManagementError(
                    "write_conflict", "The Environment could not be created.", status_code=409
                )
            return (
                await _load_environment(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    environment_id=replay[1],
                )
            ).to_resource()

    async def _replay_revision(
        self,
        *,
        actor: AuthenticatedActor,
        environment_id: str,
        identity: tuple[str, str],
        now: datetime,
    ) -> EnvironmentRevisionMutationResult:
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
            if replay is None:
                raise EnvironmentManagementError(
                    "write_conflict", "The Revision could not be created.", status_code=409
                )
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


async def _authorize(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
):
    try:
        return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        if error.concealed:
            raise environment_not_found() from error
        raise EnvironmentManagementError("forbidden", "The operation is not allowed.", status_code=403) from error


async def _require_selection(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    provider_key: str,
    expected_lock: dict[str, object],
    for_update: bool,
) -> EnvironmentProviderSelectionRecord:
    statement = select(EnvironmentProviderSelectionRecord).where(
        EnvironmentProviderSelectionRecord.organization_id == organization_id,
        EnvironmentProviderSelectionRecord.workspace_id == workspace_id,
        EnvironmentProviderSelectionRecord.provider_key == provider_key,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None or not record.enabled:
        raise environment_provider_disabled()
    if record.provider_lock != expected_lock:
        raise EnvironmentManagementError(
            "environment_provider_lock_changed",
            "The enabled Environment Provider lock no longer matches this deployment.",
            status_code=409,
        )
    return record


async def _require_credential_bindings(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    bindings: tuple[EnvironmentCredentialBinding, ...],
    require_bind_authority: bool,
) -> None:
    workspace_credentials = tuple(
        item.credential for item in bindings if isinstance(item.credential, WorkspaceSecretCredential)
    )
    if workspace_credentials and require_bind_authority:
        await _authorize(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.secrets_bind,
        )
    for credential in workspace_credentials:
        available = await session.scalar(
            select(SecretRecord.id).where(
                SecretRecord.id == credential.secret_id,
                SecretRecord.organization_id == organization_id,
                SecretRecord.workspace_id == workspace_id,
                SecretRecord.owner_type == "workspace",
                SecretRecord.owner_id == workspace_id,
                SecretRecord.deleted_at.is_(None),
                SecretRecord.ciphertext.is_not(None),
            )
        )
        if available is None:
            raise EnvironmentManagementError(
                "environment_credential_unavailable",
                "An Environment credential is unavailable.",
                status_code=409,
            )


async def _load_environment(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    environment_id: str,
    for_update: bool = False,
) -> EnvironmentRecord:
    statement = select(EnvironmentRecord).where(
        EnvironmentRecord.id == environment_id,
        EnvironmentRecord.organization_id == organization_id,
        EnvironmentRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise environment_not_found()
    return record


async def _load_revision(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    revision_id: str,
    for_update: bool = False,
) -> EnvironmentRevisionRecord:
    statement = select(EnvironmentRevisionRecord).where(
        EnvironmentRevisionRecord.id == revision_id,
        EnvironmentRevisionRecord.organization_id == organization_id,
        EnvironmentRevisionRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise environment_revision_not_found()
    return record


def _identity(idempotency_key: str, request) -> tuple[str, str]:
    try:
        encoded = idempotency_key.encode("ascii")
    except UnicodeEncodeError as error:
        raise _invalid_idempotency_key() from error
    if not 1 <= len(encoded) <= _MAX_IDEMPOTENCY_KEY_BYTES or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise _invalid_idempotency_key()
    request_digest = hashlib.sha256(request.model_dump_json(by_alias=True, exclude_none=False).encode()).hexdigest()
    return hashlib.sha256(encoded).hexdigest(), request_digest


async def _load_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    identity: tuple[str, str],
    now: datetime,
) -> tuple[str, str] | None:
    key_digest, request_digest = identity
    evidence = await session.scalar(
        select(IdempotencyEvidenceRecord).where(
            IdempotencyEvidenceRecord.workspace_id == actor.boundary_workspace_id,
            IdempotencyEvidenceRecord.actor_type == actor.principal.principal_type.value,
            IdempotencyEvidenceRecord.actor_id == actor.principal.principal_id,
            IdempotencyEvidenceRecord.operation == operation,
            IdempotencyEvidenceRecord.scope_id == scope_id,
            IdempotencyEvidenceRecord.key_digest == key_digest,
            IdempotencyEvidenceRecord.expires_at > now,
        )
    )
    if evidence is None:
        return None
    if evidence.request_digest != request_digest:
        raise EnvironmentManagementError(
            "idempotency_conflict",
            "The Idempotency-Key was already used with different request content.",
            status_code=409,
        )
    return evidence.result_kind, evidence.result_ref


def _evidence(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    operation: str,
    scope_id: str,
    identity: tuple[str, str],
    result_kind: str,
    result_ref: str,
    now: datetime,
) -> IdempotencyEvidenceRecord:
    return IdempotencyEvidenceRecord(
        id=new_object_id("idem"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        operation=operation,
        scope_id=scope_id,
        key_digest=identity[0],
        request_digest=identity[1],
        result_kind=result_kind,
        result_ref=result_ref,
        created_at=now,
        expires_at=now + IDEMPOTENCY_LIFETIME,
    )


def _audit(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    now: datetime,
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("audit"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome="success",
        occurred_at=now,
        request_id=actor.request_id,
        details=None,
    )


def _normalized_name(value: str) -> str:
    return value.casefold()


def _invalid_idempotency_key() -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "invalid_request",
        "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
        status_code=400,
    )


def _is_idempotency_race(error: IntegrityError) -> bool:
    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    if diagnostic is not None:
        return getattr(diagnostic, "constraint_name", None) == "uq_idempotency_evidence_replay_scope"
    message = str(error).lower()
    return "unique constraint failed" in message and "idempotency_evidence.actor_type" in message
