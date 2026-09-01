"""Authorized Skill catalog reads, content retrieval, and metadata lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.storage import transaction

from .cursors import (
    SkillCursorError,
    decode_revision_cursor,
    decode_skill_cursor,
    encode_revision_cursor,
    encode_skill_cursor,
)
from .domain import (
    ManagedSkillPackageManifest,
    UpdateSkillRequest,
    WorkspaceSkill,
    WorkspaceSkillCollection,
    WorkspaceSkillRevision,
    WorkspaceSkillRevisionCollection,
)
from .errors import (
    SkillManagementError,
    invalid_skill_cursor,
    package_store_management_error,
    skill_version_conflict,
)
from .models import WorkspaceSkillHeadRecord, WorkspaceSkillRecord, WorkspaceSkillRevisionRecord
from .objects import SkillPackageStore, SkillPackageStoreError
from .persistence import lock_active_skill, require_revision, require_skill
from .support import authorize_skill_workspace, record_failed_skill_attempt, skill_audit_record


class SkillCatalogService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        packages: SkillPackageStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._packages = packages
        self._clock = clock or (lambda: datetime.now(UTC))

    async def get(self, *, actor: AuthenticatedActor, skill_id: str) -> WorkspaceSkill:
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.skill_read,
                concealed_code="skill_not_found",
            )
            record = await require_skill(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                skill_id=skill_id,
            )
            return record.skill.to_resource(current_revision_id=record.head.current_revision_id)

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> WorkspaceSkillCollection:
        _validate_limit(limit)
        scope = _cursor_scope(actor=actor, workspace_id=workspace_id)
        try:
            position = decode_skill_cursor(cursor, scope=scope) if cursor is not None else None
        except SkillCursorError as error:
            raise invalid_skill_cursor() from error
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.skill_read,
            )
            query = (
                select(WorkspaceSkillRecord, WorkspaceSkillHeadRecord)
                .join(WorkspaceSkillHeadRecord, WorkspaceSkillHeadRecord.skill_id == WorkspaceSkillRecord.id)
                .where(
                    WorkspaceSkillRecord.organization_id == workspace.organization_id,
                    WorkspaceSkillRecord.workspace_id == workspace_id,
                )
            )
            if position is not None:
                display_name, skill_id = position
                query = query.where(
                    or_(
                        WorkspaceSkillRecord.display_name > display_name,
                        and_(WorkspaceSkillRecord.display_name == display_name, WorkspaceSkillRecord.id > skill_id),
                    )
                )
            records = tuple(
                (
                    await session.execute(
                        query.order_by(WorkspaceSkillRecord.display_name, WorkspaceSkillRecord.id).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = None
            if len(records) > limit and page:
                next_cursor = encode_skill_cursor(
                    display_name=page[-1][0].display_name,
                    skill_id=page[-1][0].id,
                    scope=scope,
                )
            return WorkspaceSkillCollection(
                items=tuple(record.to_resource(current_revision_id=head.current_revision_id) for record, head in page),
                next_cursor=next_cursor,
            )

    async def list_revisions(
        self,
        *,
        actor: AuthenticatedActor,
        skill_id: str,
        limit: int,
        cursor: str | None,
    ) -> WorkspaceSkillRevisionCollection:
        _validate_limit(limit)
        workspace_id = actor.boundary_workspace_id
        scope = _cursor_scope(actor=actor, workspace_id=workspace_id, skill_id=skill_id)
        try:
            position = decode_revision_cursor(cursor, scope=scope) if cursor is not None else None
        except SkillCursorError as error:
            raise invalid_skill_cursor() from error
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.skill_read,
                concealed_code="skill_not_found",
            )
            await require_skill(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                skill_id=skill_id,
            )
            query = select(WorkspaceSkillRevisionRecord).where(
                WorkspaceSkillRevisionRecord.organization_id == workspace.organization_id,
                WorkspaceSkillRevisionRecord.workspace_id == workspace_id,
                WorkspaceSkillRevisionRecord.skill_id == skill_id,
            )
            if position is not None:
                revision_number, revision_id = position
                query = query.where(
                    or_(
                        WorkspaceSkillRevisionRecord.revision_number < revision_number,
                        and_(
                            WorkspaceSkillRevisionRecord.revision_number == revision_number,
                            WorkspaceSkillRevisionRecord.id < revision_id,
                        ),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(
                            WorkspaceSkillRevisionRecord.revision_number.desc(),
                            WorkspaceSkillRevisionRecord.id.desc(),
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
            return WorkspaceSkillRevisionCollection(
                items=tuple(record.to_resource() for record in page),
                next_cursor=next_cursor,
            )

    async def get_revision(
        self,
        *,
        actor: AuthenticatedActor,
        revision_id: str,
    ) -> WorkspaceSkillRevision:
        record, _organization_id = await self._authorized_revision(actor=actor, revision_id=revision_id)
        return record.to_resource()

    async def content(
        self,
        *,
        actor: AuthenticatedActor,
        revision_id: str,
    ) -> tuple[bytes, str]:
        record, organization_id = await self._authorized_revision(actor=actor, revision_id=revision_id)
        manifest = ManagedSkillPackageManifest.model_validate(record.manifest)
        try:
            content = await self._packages.read_verified(
                organization_id=organization_id,
                workspace_id=record.workspace_id,
                manifest=manifest,
            )
        except SkillPackageStoreError as error:
            raise package_store_management_error(error) from error
        return content, manifest.content_digest

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        skill_id: str,
        request: UpdateSkillRequest,
    ) -> WorkspaceSkill:
        workspace_id = actor.boundary_workspace_id
        try:
            return await self._update(actor=actor, workspace_id=workspace_id, skill_id=skill_id, request=request)
        except Exception as error:
            await self._audit_failure(
                error,
                actor=actor,
                workspace_id=workspace_id,
                skill_id=skill_id,
                action="skill.update",
            )
            raise

    async def _update(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        skill_id: str,
        request: UpdateSkillRequest,
    ) -> WorkspaceSkill:
        now = self._clock()
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.skill_update,
                concealed_code="skill_not_found",
            )
            locked = await lock_active_skill(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                skill_id=skill_id,
            )
            if locked.skill.version != request.expected_version:
                raise skill_version_conflict(locked.skill.version)
            changed_fields: list[str] = []
            if locked.skill.display_name != request.display_name:
                locked.skill.display_name = request.display_name
                locked.skill.version += 1
                locked.skill.updated_at = now
                changed_fields.append("display_name")
            session.add(
                skill_audit_record(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    skill_id=skill_id,
                    action="skill.update",
                    now=now,
                    details={"changed_fields": changed_fields},
                )
            )
            await session.flush()
            return locked.skill.to_resource(current_revision_id=locked.head.current_revision_id)

    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        skill_id: str,
        expected_version: int,
    ) -> None:
        workspace_id = actor.boundary_workspace_id
        try:
            await self._delete(
                actor=actor,
                workspace_id=workspace_id,
                skill_id=skill_id,
                expected_version=expected_version,
            )
        except Exception as error:
            await self._audit_failure(
                error,
                actor=actor,
                workspace_id=workspace_id,
                skill_id=skill_id,
                action="skill.delete",
            )
            raise

    async def _delete(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        skill_id: str,
        expected_version: int,
    ) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.skill_delete,
                concealed_code="skill_not_found",
            )
            locked = await lock_active_skill(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                skill_id=skill_id,
            )
            if locked.skill.version != expected_version:
                raise skill_version_conflict(locked.skill.version)
            locked.skill.deleted_at = now
            locked.skill.updated_at = now
            locked.skill.version += 1
            session.add(
                skill_audit_record(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    skill_id=skill_id,
                    action="skill.delete",
                    now=now,
                )
            )

    async def _authorized_revision(
        self,
        *,
        actor: AuthenticatedActor,
        revision_id: str,
    ) -> tuple[WorkspaceSkillRevisionRecord, str]:
        workspace_id = actor.boundary_workspace_id
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.skill_read,
                concealed_code="skill_not_found",
            )
            record = await require_revision(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                revision_id=revision_id,
            )
            return record, workspace.organization_id

    async def _audit_failure(
        self,
        error: Exception,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        skill_id: str,
        action: str,
    ) -> None:
        try:
            await record_failed_skill_attempt(
                self._sessions,
                actor=actor,
                workspace_id=workspace_id,
                skill_id=skill_id,
                action=action,
                now=self._clock(),
            )
        except Exception as audit_error:
            error.add_note(f"security audit persistence failed with {type(audit_error).__name__}")


def _cursor_scope(*, actor: AuthenticatedActor, workspace_id: str, skill_id: str | None = None) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "skill_id": skill_id,
        "principal_type": actor.principal.principal_type.value,
        "principal_id": actor.principal.principal_id,
    }


def _validate_limit(limit: int) -> None:
    if limit < 1 or limit > 100:
        raise SkillManagementError("invalid_request", "limit must be between 1 and 100.", status_code=400)
