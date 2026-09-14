"""Authorized Skill catalog reads, content retrieval, and metadata lifecycle."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.labels import LabelsBody, label_predicates, labels_etag
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, next_updated_at, utc_now

from .cursors import (
    SkillCursorError,
    decode_reference_cursor,
    decode_revision_cursor,
    decode_skill_cursor,
    encode_reference_cursor,
    encode_revision_cursor,
    encode_skill_cursor,
)
from .domain import (
    Skill,
    SkillAgentReferenceCollection,
    SkillCollection,
    SkillListItem,
    SkillPackageManifest,
    SkillRevision,
    SkillRevisionCollection,
    UpdateSkillRequest,
)
from .errors import (
    SkillError,
    invalid_skill_cursor,
    package_store_error,
    skill_in_use,
    skill_not_found,
)
from .models import SkillRecord, SkillRevisionRecord
from .objects import SkillPackageStore, SkillPackageStoreError
from .persistence import lock_active_skill, require_revision, require_skill
from .references import current_agent_references
from .support import authorize_skill_workspace, record_failed_skill_attempt, skill_audit_record


class SkillCatalogService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        packages: SkillPackageStore,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._sessions = sessions
        self._packages = packages
        self._clock = clock or utc_now

    async def get(self, *, actor: AuthenticatedActor, skill_id: str) -> Skill:
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=actor.workspace_id,
                action=WorkspaceAction.skill_read,
                concealed_code="skill_not_found",
            )
            record = await require_skill(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                skill_id=skill_id,
            )
            return record.to_resource()

    async def get_by_key(self, *, actor: AuthenticatedActor, workspace_id: str, skill_key: str) -> Skill:
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.skill_read,
                concealed_code="skill_not_found",
            )
            record = await session.scalar(
                select(SkillRecord).where(
                    SkillRecord.organization_id == workspace.organization_id,
                    SkillRecord.workspace_id == workspace.workspace_id,
                    SkillRecord.key == skill_key,
                    SkillRecord.deleted_at.is_(None),
                )
            )
            if record is None:
                raise skill_not_found()
            return record.to_resource()

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
        q: str | None = None,
        source_kind: Literal["zip", "github"] | None = None,
        labels: dict[str, str] | None = None,
    ) -> SkillCollection:
        _validate_limit(limit)
        term = q.strip().lower() if q else ""
        scope = _cursor_scope(actor=actor, workspace_id=workspace_id)
        if source_kind:
            scope["source_kind"] = source_kind
        if term:
            scope["q"] = term
        scope["labels"] = labels or {}
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
            source = SkillRevisionRecord.imported_from["kind"].as_string()
            query = (
                select(SkillRecord, source)
                .join(
                    SkillRevisionRecord,
                    and_(
                        SkillRevisionRecord.id == SkillRecord.current_revision_id,
                        SkillRevisionRecord.skill_id == SkillRecord.id,
                        SkillRevisionRecord.workspace_id == SkillRecord.workspace_id,
                        SkillRevisionRecord.organization_id == SkillRecord.organization_id,
                    ),
                )
                .where(
                    SkillRecord.organization_id == workspace.organization_id,
                    SkillRecord.workspace_id == workspace_id,
                    SkillRecord.deleted_at.is_(None),
                )
            )
            query = query.where(
                *label_predicates(
                    SkillRecord.labels,
                    labels or {},
                    dialect=session.bind.dialect.name,
                )
            )
            if source_kind:
                query = query.where(source == source_kind)
            if term:
                query = query.where(
                    or_(
                        func.lower(SkillRecord.name).contains(term, autoescape=True),
                        func.lower(SkillRecord.key).contains(term, autoescape=True),
                    )
                )
            if position is not None:
                name, skill_id = position
                query = query.where(
                    or_(
                        SkillRecord.name > name,
                        and_(SkillRecord.name == name, SkillRecord.id > skill_id),
                    )
                )
            records = tuple(
                (await session.execute(query.order_by(SkillRecord.name, SkillRecord.id).limit(limit + 1))).all()
            )
            page = records[:limit]
            next_cursor = None
            if len(records) > limit and page:
                next_cursor = encode_skill_cursor(
                    name=page[-1][0].name,
                    skill_id=page[-1][0].id,
                    scope=scope,
                )
            return SkillCollection(
                items=tuple(
                    SkillListItem(**record.to_resource().model_dump(), source_kind=kind) for record, kind in page
                ),
                next_cursor=next_cursor,
            )

    async def list_revisions(
        self,
        *,
        actor: AuthenticatedActor,
        skill_id: str,
        limit: int,
        cursor: str | None,
    ) -> SkillRevisionCollection:
        _validate_limit(limit)
        workspace_id = actor.workspace_id
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
            query = select(SkillRevisionRecord).where(
                SkillRevisionRecord.organization_id == workspace.organization_id,
                SkillRevisionRecord.workspace_id == workspace_id,
                SkillRevisionRecord.skill_id == skill_id,
            )
            if position is not None:
                version, revision_id = position
                query = query.where(
                    or_(
                        SkillRevisionRecord.version < version,
                        and_(
                            SkillRevisionRecord.version == version,
                            SkillRevisionRecord.id < revision_id,
                        ),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(
                            SkillRevisionRecord.version.desc(),
                            SkillRevisionRecord.id.desc(),
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
            return SkillRevisionCollection(
                items=tuple(record.to_resource() for record in page),
                next_cursor=next_cursor,
            )

    async def get_revision(
        self,
        *,
        actor: AuthenticatedActor,
        revision_id: str,
    ) -> SkillRevision:
        record, _organization_id = await self._authorized_revision(actor=actor, revision_id=revision_id)
        return record.to_resource()

    async def references(
        self,
        *,
        actor: AuthenticatedActor,
        skill_id: str,
        limit: int,
        cursor: str | None,
    ) -> SkillAgentReferenceCollection:
        _validate_limit(limit)
        workspace_id = actor.workspace_id
        scope = _cursor_scope(actor=actor, workspace_id=workspace_id, skill_id=skill_id)
        try:
            position = decode_reference_cursor(cursor, scope=scope) if cursor is not None else None
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
            references = await current_agent_references(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                skill_id=skill_id,
            )
            if position is not None:
                references = tuple(item for item in references if (item.agent_name, item.agent_id) > position)
            page = references[:limit]
            next_cursor = None
            if len(references) > limit and page:
                next_cursor = encode_reference_cursor(
                    agent_name=page[-1].agent_name,
                    agent_id=page[-1].agent_id,
                    scope=scope,
                )
            return SkillAgentReferenceCollection(items=page, next_cursor=next_cursor)

    async def content(
        self,
        *,
        actor: AuthenticatedActor,
        revision_id: str,
    ) -> tuple[bytes, str]:
        record, organization_id = await self._authorized_revision(actor=actor, revision_id=revision_id)
        manifest = SkillPackageManifest.model_validate(record.manifest)
        try:
            content = await self._packages.read_verified(
                organization_id=organization_id,
                workspace_id=record.workspace_id,
                manifest=manifest,
            )
        except SkillPackageStoreError as error:
            raise package_store_error(error) from error
        return content, manifest.content_digest

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        skill_id: str,
        if_match: str,
        request: UpdateSkillRequest,
    ) -> Skill:
        workspace_id = actor.workspace_id
        try:
            return await self._update(
                actor=actor,
                workspace_id=workspace_id,
                skill_id=skill_id,
                if_match=if_match,
                request=request,
            )
        except Exception as error:
            await self._audit_failure(
                error,
                actor=actor,
                workspace_id=workspace_id,
                skill_id=skill_id,
                action="skill.update",
            )
            raise

    async def get_labels(self, *, actor: AuthenticatedActor, skill_id: str) -> tuple[LabelsBody, str]:
        skill = await self.get(actor=actor, skill_id=skill_id)
        return LabelsBody(labels=skill.labels), labels_etag(skill.id, skill.labels)

    async def replace_labels(
        self, *, actor: AuthenticatedActor, skill_id: str, if_match: str, body: LabelsBody
    ) -> tuple[LabelsBody, str]:
        now = self._clock()
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=actor.workspace_id,
                action=WorkspaceAction.skill_update,
                concealed_code="skill_not_found",
            )
            locked = await lock_active_skill(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                skill_id=skill_id,
            )
            current = labels_etag(locked.id, locked.labels)
            if not etag_matches(if_match, current):
                raise SkillError(
                    "labels_etag_mismatch",
                    "Labels changed since they were read.",
                    category=ErrorCategory.stale_version,
                    details={"current_etag": current},
                )
            if locked.labels != body.labels:
                locked.labels = dict(body.labels)
                locked.updated_by_type = actor.principal.principal_type.value
                locked.updated_by_id = actor.principal.principal_id
                locked.updated_at = next_updated_at(locked.updated_at, now)
                session.add(
                    skill_audit_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        skill_id=skill_id,
                        action="skill.labels.update",
                        now=now,
                        details=None,
                    )
                )
            return LabelsBody(labels=locked.labels), labels_etag(locked.id, locked.labels)

    async def _update(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        skill_id: str,
        if_match: str,
        request: UpdateSkillRequest,
    ) -> Skill:
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
            _require_etag(locked, if_match)
            changed_fields: list[str] = []
            if locked.name != request.name:
                locked.name = request.name
                locked.updated_by_type = actor.principal.principal_type.value
                locked.updated_by_id = actor.principal.principal_id
                locked.updated_at = now
                changed_fields.append("name")
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
            return locked.to_resource()

    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        skill_id: str,
        if_match: str,
    ) -> None:
        workspace_id = actor.workspace_id
        try:
            await self._delete(
                actor=actor,
                workspace_id=workspace_id,
                skill_id=skill_id,
                if_match=if_match,
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
        if_match: str,
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
            _require_etag(locked, if_match)
            references = await current_agent_references(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                skill_id=skill_id,
            )
            if references:
                raise skill_in_use(len(references))
            locked.deleted_at = now
            locked.updated_by_type = actor.principal.principal_type.value
            locked.updated_by_id = actor.principal.principal_id
            locked.updated_at = now
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
    ) -> tuple[SkillRevisionRecord, str]:
        workspace_id = actor.workspace_id
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
                details=(
                    {"blocking_agent_count": error.details["blocking_agent_count"]}
                    if isinstance(error, SkillError) and error.code == "skill_in_use"
                    else None
                ),
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
        raise SkillError("invalid_request", "limit must be between 1 and 100.", category=ErrorCategory.invalid_request)


def _require_etag(record: SkillRecord, if_match: str) -> None:
    current = resource_etag(record.id, record.updated_at)
    if not etag_matches(if_match, current):
        raise SkillError(
            "precondition_failed",
            "The Skill changed after it was read.",
            category=ErrorCategory.stale_version,
            details={"current_etag": current},
        )
