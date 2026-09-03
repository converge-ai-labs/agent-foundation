"""Plugin catalog, immutable Version upload, and lifecycle operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import PluginRuntimeMode
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now

from .artifact import InspectedPluginWheel, inspect_plugin_wheel
from .commands import PluginRuntimeCommandDispatcher
from .cursors import (
    PluginCursorError,
    decode_plugin_cursor,
    decode_plugin_version_cursor,
    encode_plugin_cursor,
    encode_plugin_version_cursor,
)
from .domain import (
    PLUGIN_ID_PREFIX,
    PLUGIN_VERSION_ID_PREFIX,
    BuiltinPluginRegistration,
    Plugin,
    PluginCollection,
    PluginSource,
    PluginTaskReceipt,
    PluginVersion,
    PluginVersionCollection,
)
from .errors import (
    PluginError,
    plugin_artifact_invalid,
    plugin_etag_mismatch,
    plugin_idempotency_conflict,
    plugin_identity_conflict,
    plugin_not_found,
    plugin_operation_not_found,
    plugin_runtime_control_unavailable,
    plugin_runtime_mode_unsupported,
    plugin_state_conflict,
    plugin_version_conflict,
    plugin_version_not_found,
)
from .models import PluginRecord, PluginRuntimeStateRecord, PluginVersionRecord
from .objects import PluginObjectStore
from .staging import PluginStaging

IDEMPOTENCY_LIFETIME = timedelta(hours=24)
_MAX_IDEMPOTENCY_KEY_BYTES = 512


@dataclass(frozen=True, slots=True)
class PluginUploadResult:
    version: PluginVersion
    created: bool


class PluginService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        objects: PluginObjectStore,
        staging: PluginStaging,
        *,
        runtime_mode: PluginRuntimeMode,
        max_wheel_bytes: int,
        max_expanded_bytes: int,
        max_archive_members: int,
        runtime_command_dispatcher: PluginRuntimeCommandDispatcher | None = None,
        clock=None,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._staging = staging
        self._runtime_mode = runtime_mode
        self._max_wheel_bytes = max_wheel_bytes
        self._max_expanded_bytes = max_expanded_bytes
        self._max_archive_members = max_archive_members
        self._runtime_command_dispatcher = runtime_command_dispatcher
        self._clock = clock or utc_now

    async def ensure_runtime_mode(self) -> None:
        """Persist and verify the deployment-wide immutable Plugin Runtime profile."""

        now = self._clock()
        async with transaction(self._sessions) as session:
            state = await session.get(PluginRuntimeStateRecord, "runtime", with_for_update=True)
            if state is None:
                revision_modes = frozenset(
                    await session.scalars(select(AgentRevisionRecord.plugin_runtime_mode).distinct())
                )
                if revision_modes and revision_modes != {self._runtime_mode.value}:
                    raise RuntimeError("configured Plugin Runtime mode conflicts with retained AgentRevisions")
                session.add(
                    PluginRuntimeStateRecord(
                        id="runtime",
                        mode=self._runtime_mode.value,
                        active_lock_digest=None,
                        runtime_generation=1,
                        command_operation_id=None,
                        command_claim_generation=0,
                        command_lease_expires_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif state.mode != self._runtime_mode.value:
                raise RuntimeError("configured Plugin Runtime mode conflicts with persisted Plugin Runtime state")

    async def register_builtin(
        self,
        *,
        registration: BuiltinPluginRegistration,
        body: AsyncIterable[bytes],
        content_length: int | None,
    ) -> PluginVersion:
        """Publish and idempotently register one distribution-owned PluginVersion."""

        staged = await self._staging.stage(
            body,
            max_size_bytes=self._max_wheel_bytes,
            content_length=content_length,
        )
        try:
            if staged.content_digest != registration.content_digest:
                raise plugin_artifact_invalid("builtin_manifest_digest_mismatch")
            inspected = await inspect_plugin_wheel(
                staged.path,
                max_expanded_bytes=self._max_expanded_bytes,
                max_members=self._max_archive_members,
            )
            if not _builtin_manifest_matches(registration, inspected):
                raise plugin_artifact_invalid("builtin_manifest_identity_mismatch")
            artifact_ref = await self._objects.publish(staged)
            try:
                return await self._commit_builtin_registration(
                    registration=registration,
                    inspected=inspected,
                    artifact_ref=artifact_ref,
                    size_bytes=staged.size_bytes,
                )
            except IntegrityError:
                replay = await self._load_builtin_registration(
                    registration=registration,
                    inspected=inspected,
                    artifact_ref=artifact_ref,
                    size_bytes=staged.size_bytes,
                )
                if replay is not None:
                    return replay
                raise plugin_identity_conflict() from None
        finally:
            await staged.remove()

    async def upload(
        self,
        *,
        actor: AuthenticatedActor,
        idempotency_key: str,
        filename: str,
        body: AsyncIterable[bytes],
        content_length: int | None,
        plugin_id: str | None = None,
    ) -> PluginUploadResult:
        if not filename.lower().endswith(".whl"):
            raise plugin_artifact_invalid("wheel_extension_required")
        organization_id = await self._authorize(actor, WorkspaceAction.plugin_manage)
        key_digest = _idempotency_key_digest(idempotency_key)
        staged = await self._staging.stage(
            body,
            max_size_bytes=self._max_wheel_bytes,
            content_length=content_length,
        )
        try:
            inspected = await inspect_plugin_wheel(
                staged.path,
                max_expanded_bytes=self._max_expanded_bytes,
                max_members=self._max_archive_members,
            )
            operation = "plugin.version.upload" if plugin_id is not None else "plugin.upload"
            scope_id = plugin_id or "plugins"
            request_digest = _request_digest(
                {
                    "plugin_id": plugin_id,
                    "content_digest": staged.content_digest,
                }
            )
            replay = await self._load_version_replay(
                actor=actor,
                operation=operation,
                scope_id=scope_id,
                key_digest=key_digest,
                request_digest=request_digest,
            )
            if replay is not None:
                return PluginUploadResult(version=replay, created=False)
            artifact_ref = await self._objects.publish(staged)
            try:
                return await self._commit_upload(
                    actor=actor,
                    organization_id=organization_id,
                    operation=operation,
                    scope_id=scope_id,
                    key_digest=key_digest,
                    request_digest=request_digest,
                    plugin_id=plugin_id,
                    inspected=inspected,
                    artifact_ref=artifact_ref,
                    size_bytes=staged.size_bytes,
                    content_digest=staged.content_digest,
                )
            except IntegrityError:
                replay = await self._load_semantic_upload(
                    actor=actor,
                    plugin_id=plugin_id,
                    inspected=inspected,
                    content_digest=staged.content_digest,
                )
                if replay is not None:
                    return PluginUploadResult(version=replay, created=False)
                raise
        finally:
            await staged.remove()

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        limit: int,
        cursor: str | None,
        source: PluginSource | None,
        include_archived: bool,
    ) -> PluginCollection:
        scope: dict[str, object] = {
            "source": source.value if source is not None else None,
            "include_archived": include_archived,
        }
        try:
            after = decode_plugin_cursor(cursor, scope=scope) if cursor is not None else None
        except PluginCursorError as error:
            raise PluginError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        await self._authorize(actor, WorkspaceAction.plugin_read)
        query = select(PluginRecord)
        if not include_archived:
            query = query.where(PluginRecord.archived_at.is_(None))
        if source is not None:
            query = query.where(PluginRecord.source == source.value)
        if after is not None:
            updated_at, after_id = after
            query = query.where(
                or_(
                    PluginRecord.updated_at < updated_at,
                    and_(PluginRecord.updated_at == updated_at, PluginRecord.id < after_id),
                )
            )
        async with short_session(self._sessions) as session:
            rows = tuple(
                (
                    await session.scalars(
                        query.order_by(PluginRecord.updated_at.desc(), PluginRecord.id.desc()).limit(limit + 1)
                    )
                ).all()
            )
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            next_cursor = encode_plugin_cursor(updated_at=page[-1].updated_at, plugin_id=page[-1].id, scope=scope)
        return PluginCollection(items=tuple(item.to_resource() for item in page), next_cursor=next_cursor)

    async def get(self, *, actor: AuthenticatedActor, plugin_id: str) -> Plugin:
        await self._authorize(actor, WorkspaceAction.plugin_read)
        async with short_session(self._sessions) as session:
            record = await session.get(PluginRecord, plugin_id)
        if record is None:
            raise plugin_not_found()
        return record.to_resource()

    async def list_versions(
        self,
        *,
        actor: AuthenticatedActor,
        plugin_id: str,
        limit: int,
        cursor: str | None,
    ) -> PluginVersionCollection:
        scope: dict[str, object] = {"plugin_id": plugin_id}
        try:
            after = decode_plugin_version_cursor(cursor, scope=scope) if cursor is not None else None
        except PluginCursorError as error:
            raise PluginError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        await self._authorize(actor, WorkspaceAction.plugin_read)
        async with short_session(self._sessions) as session:
            if await session.get(PluginRecord, plugin_id) is None:
                raise plugin_not_found()
            query = select(PluginVersionRecord).where(PluginVersionRecord.plugin_id == plugin_id)
            if after is not None:
                created_at, after_id = after
                query = query.where(
                    or_(
                        PluginVersionRecord.created_at < created_at,
                        and_(PluginVersionRecord.created_at == created_at, PluginVersionRecord.id < after_id),
                    )
                )
            rows = tuple(
                (
                    await session.scalars(
                        query.order_by(PluginVersionRecord.created_at.desc(), PluginVersionRecord.id.desc()).limit(
                            limit + 1
                        )
                    )
                ).all()
            )
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            next_cursor = encode_plugin_version_cursor(
                created_at=page[-1].created_at,
                version_id=page[-1].id,
                scope=scope,
            )
        return PluginVersionCollection(items=tuple(item.to_resource() for item in page), next_cursor=next_cursor)

    async def get_version(self, *, actor: AuthenticatedActor, version_id: str) -> PluginVersion:
        await self._authorize(actor, WorkspaceAction.plugin_read)
        async with short_session(self._sessions) as session:
            record = await session.get(PluginVersionRecord, version_id)
        if record is None:
            raise plugin_version_not_found()
        return record.to_resource()

    async def change_lifecycle(
        self,
        *,
        actor: AuthenticatedActor,
        plugin_id: str,
        action: Literal["archive", "unarchive"],
        idempotency_key: str,
        if_match: str,
    ) -> Plugin:
        organization_id = await self._authorize(actor, WorkspaceAction.plugin_manage)
        operation = f"plugin.{action}"
        key_digest = _idempotency_key_digest(idempotency_key)
        request_digest = _request_digest({"plugin_id": plugin_id, "action": action})
        replay = await self._load_plugin_replay(
            actor=actor,
            operation=operation,
            scope_id=plugin_id,
            key_digest=key_digest,
            request_digest=request_digest,
        )
        if replay is not None:
            return replay
        now = self._clock()
        async with transaction(self._sessions) as session:
            await authorize_workspace(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.plugin_manage,
            )
            record = await session.scalar(select(PluginRecord).where(PluginRecord.id == plugin_id).with_for_update())
            if record is None:
                raise plugin_not_found()
            if not etag_matches(if_match, resource_etag(record.id, record.updated_at)):
                raise plugin_etag_mismatch()
            if record.source != PluginSource.uploaded.value:
                raise plugin_state_conflict()
            if action == "archive":
                if record.archived_at is not None or record.active_version_id is not None:
                    raise plugin_state_conflict()
                record.archived_at = now
            elif record.archived_at is None:
                raise plugin_state_conflict()
            else:
                record.archived_at = None
            record.updated_at = now
            session.add(
                _evidence(
                    actor=actor,
                    organization_id=organization_id,
                    operation=operation,
                    scope_id=plugin_id,
                    key_digest=key_digest,
                    request_digest=request_digest,
                    result_kind="plugin",
                    result_ref=plugin_id,
                    now=now,
                )
            )
            session.add(
                _audit(actor=actor, organization_id=organization_id, action=operation, resource_id=plugin_id, now=now)
            )
            await session.flush()
            return record.to_resource()

    async def activate(
        self,
        *,
        actor: AuthenticatedActor,
        plugin_version_id: str,
        idempotency_key: str,
    ) -> PluginTaskReceipt:
        dispatcher = self._require_runtime_command_dispatcher()
        organization_id = await self._authorize(actor, WorkspaceAction.plugin_runtime_manage)
        async with short_session(self._sessions) as session:
            row = await session.execute(
                select(PluginVersionRecord, PluginRecord)
                .join(PluginRecord, PluginRecord.id == PluginVersionRecord.plugin_id)
                .where(PluginVersionRecord.id == plugin_version_id)
            )
            current = row.one_or_none()
        if current is None:
            raise plugin_version_not_found()
        plugin_version, plugin = current
        return await dispatcher.activate(
            actor=actor,
            organization_id=organization_id,
            workspace_id=actor.boundary_workspace_id,
            plugin=plugin.to_resource(),
            plugin_version=plugin_version.to_resource(),
            idempotency_key=idempotency_key,
        )

    async def deactivate(
        self,
        *,
        actor: AuthenticatedActor,
        plugin_id: str,
        idempotency_key: str,
    ) -> PluginTaskReceipt:
        dispatcher = self._require_runtime_command_dispatcher()
        organization_id = await self._authorize(actor, WorkspaceAction.plugin_runtime_manage)
        async with short_session(self._sessions) as session:
            plugin = await session.get(PluginRecord, plugin_id)
        if plugin is None:
            raise plugin_not_found()
        return await dispatcher.deactivate(
            actor=actor,
            organization_id=organization_id,
            workspace_id=actor.boundary_workspace_id,
            plugin=plugin.to_resource(),
            idempotency_key=idempotency_key,
        )

    async def get_operation(self, *, actor: AuthenticatedActor, operation_id: str) -> PluginTaskReceipt:
        organization_id = await self._authorize(actor, WorkspaceAction.plugin_runtime_manage)
        if self._runtime_mode is PluginRuntimeMode.on_demand:
            raise plugin_operation_not_found()
        dispatcher = self._runtime_command_dispatcher
        if dispatcher is None:
            raise plugin_runtime_control_unavailable()
        return await dispatcher.get_receipt(
            actor=actor,
            organization_id=organization_id,
            workspace_id=actor.boundary_workspace_id,
            operation_id=operation_id,
        )

    async def _commit_upload(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        operation: str,
        scope_id: str,
        key_digest: str,
        request_digest: str,
        plugin_id: str | None,
        inspected: InspectedPluginWheel,
        artifact_ref: str,
        size_bytes: int,
        content_digest: str,
    ) -> PluginUploadResult:
        now = self._clock()
        async with transaction(self._sessions) as session:
            await authorize_workspace(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.plugin_manage,
            )
            plugin = await self._select_upload_plugin(
                session, plugin_id=plugin_id, inspected=inspected, actor=actor, now=now
            )
            existing = await session.scalar(
                select(PluginVersionRecord)
                .where(
                    PluginVersionRecord.plugin_id == plugin.id,
                    PluginVersionRecord.version == inspected.version,
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.content_digest != content_digest:
                    raise plugin_version_conflict()
                version = existing
                created = False
            else:
                version = PluginVersionRecord(
                    id=new_object_id(PLUGIN_VERSION_ID_PREFIX),
                    plugin_id=plugin.id,
                    version=inspected.version,
                    content_digest=content_digest,
                    artifact_ref=artifact_ref,
                    size_bytes=size_bytes,
                    requires_dist=list(inspected.requires_dist),
                    requires_python=inspected.requires_python,
                    wheel_tags=list(inspected.wheel_tags),
                    root_is_purelib=inspected.root_is_purelib,
                    entry_point_target=inspected.entry_point_target,
                    status="ready",
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                )
                session.add(version)
                created = True
            session.add(
                _evidence(
                    actor=actor,
                    organization_id=organization_id,
                    operation=operation,
                    scope_id=scope_id,
                    key_digest=key_digest,
                    request_digest=request_digest,
                    result_kind="plugin_version",
                    result_ref=version.id,
                    now=now,
                )
            )
            session.add(
                _audit(
                    actor=actor,
                    organization_id=organization_id,
                    action=operation,
                    resource_id=version.id,
                    now=now,
                )
            )
            await session.flush()
            return PluginUploadResult(version=version.to_resource(), created=created)

    async def _commit_builtin_registration(
        self,
        *,
        registration: BuiltinPluginRegistration,
        inspected: InspectedPluginWheel,
        artifact_ref: str,
        size_bytes: int,
    ) -> PluginVersion:
        now = self._clock()
        async with transaction(self._sessions) as session:
            plugins = tuple(
                (
                    await session.scalars(
                        select(PluginRecord)
                        .where(
                            or_(
                                PluginRecord.id == registration.plugin_id,
                                PluginRecord.plugin_key == registration.plugin_key,
                                PluginRecord.distribution_name == registration.distribution_name,
                                PluginRecord.top_level_package == registration.top_level_package,
                            )
                        )
                        .with_for_update()
                    )
                ).all()
            )
            if len(plugins) > 1:
                raise plugin_identity_conflict()
            if plugins:
                plugin = plugins[0]
                if not _builtin_plugin_matches(plugin, registration):
                    raise plugin_identity_conflict()
                if plugin.archived_at is not None:
                    raise plugin_state_conflict()
                if plugin.required != registration.required:
                    plugin.required = registration.required
                    plugin.updated_at = now
            else:
                plugin = PluginRecord(
                    id=registration.plugin_id,
                    source=PluginSource.builtin.value,
                    plugin_key=registration.plugin_key,
                    distribution_name=registration.distribution_name,
                    top_level_package=registration.top_level_package,
                    active_version_id=None,
                    archived_at=None,
                    required=registration.required,
                    created_by_type="service_account",
                    created_by_id=registration.system_actor_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(plugin)
                await session.flush()

            versions = tuple(
                (
                    await session.scalars(
                        select(PluginVersionRecord)
                        .where(
                            or_(
                                PluginVersionRecord.id == registration.plugin_version_id,
                                and_(
                                    PluginVersionRecord.plugin_id == registration.plugin_id,
                                    PluginVersionRecord.version == registration.version,
                                ),
                            )
                        )
                        .with_for_update()
                    )
                ).all()
            )
            if len(versions) > 1:
                raise plugin_version_conflict()
            if versions:
                version = versions[0]
                if not _builtin_version_matches(
                    version,
                    registration=registration,
                    inspected=inspected,
                    artifact_ref=artifact_ref,
                    size_bytes=size_bytes,
                ):
                    raise plugin_version_conflict()
            else:
                version = PluginVersionRecord(
                    id=registration.plugin_version_id,
                    plugin_id=registration.plugin_id,
                    version=registration.version,
                    content_digest=registration.content_digest,
                    artifact_ref=artifact_ref,
                    size_bytes=size_bytes,
                    requires_dist=list(inspected.requires_dist),
                    requires_python=inspected.requires_python,
                    wheel_tags=list(inspected.wheel_tags),
                    root_is_purelib=inspected.root_is_purelib,
                    entry_point_target=inspected.entry_point_target,
                    status="ready",
                    created_by_type="service_account",
                    created_by_id=registration.system_actor_id,
                    created_at=now,
                )
                session.add(version)
            await session.flush()
            return version.to_resource()

    async def _load_builtin_registration(
        self,
        *,
        registration: BuiltinPluginRegistration,
        inspected: InspectedPluginWheel,
        artifact_ref: str,
        size_bytes: int,
    ) -> PluginVersion | None:
        async with short_session(self._sessions) as session:
            plugin = await session.get(PluginRecord, registration.plugin_id)
            version = await session.get(PluginVersionRecord, registration.plugin_version_id)
        if plugin is None or version is None:
            return None
        if (
            not _builtin_plugin_matches(plugin, registration)
            or plugin.required != registration.required
            or not _builtin_version_matches(
                version,
                registration=registration,
                inspected=inspected,
                artifact_ref=artifact_ref,
                size_bytes=size_bytes,
            )
        ):
            return None
        return version.to_resource()

    async def _select_upload_plugin(
        self,
        session: AsyncSession,
        *,
        plugin_id: str | None,
        inspected: InspectedPluginWheel,
        actor: AuthenticatedActor,
        now: datetime,
    ) -> PluginRecord:
        if plugin_id is not None:
            record = await session.scalar(select(PluginRecord).where(PluginRecord.id == plugin_id).with_for_update())
            if record is None:
                raise plugin_not_found()
            if record.source != PluginSource.uploaded.value or record.archived_at is not None:
                raise plugin_state_conflict()
            if (
                record.plugin_key != inspected.plugin_key
                or record.distribution_name != inspected.distribution_name
                or record.top_level_package != inspected.top_level_package
            ):
                raise plugin_identity_conflict()
            return record

        record = await session.scalar(
            select(PluginRecord).where(PluginRecord.plugin_key == inspected.plugin_key).with_for_update()
        )
        if record is not None:
            if record.source != PluginSource.uploaded.value or record.archived_at is not None:
                raise plugin_state_conflict()
            if (
                record.distribution_name != inspected.distribution_name
                or record.top_level_package != inspected.top_level_package
            ):
                raise plugin_identity_conflict()
            existing = await session.scalar(
                select(PluginVersionRecord).where(
                    PluginVersionRecord.plugin_id == record.id,
                    PluginVersionRecord.version == inspected.version,
                )
            )
            if existing is None:
                raise plugin_identity_conflict()
            return record
        identity_collision = await session.scalar(
            select(func.count())
            .select_from(PluginRecord)
            .where(
                or_(
                    PluginRecord.distribution_name == inspected.distribution_name,
                    PluginRecord.top_level_package == inspected.top_level_package,
                )
            )
        )
        if identity_collision:
            raise plugin_identity_conflict()
        record = PluginRecord(
            id=new_object_id(PLUGIN_ID_PREFIX),
            source=PluginSource.uploaded.value,
            plugin_key=inspected.plugin_key,
            distribution_name=inspected.distribution_name,
            top_level_package=inspected.top_level_package,
            active_version_id=None,
            archived_at=None,
            required=False,
            created_by_type=actor.principal.principal_type.value,
            created_by_id=actor.principal.principal_id,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        await session.flush()
        return record

    async def _authorize(self, actor: AuthenticatedActor, action: WorkspaceAction) -> str:
        try:
            async with short_session(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    action=action,
                )
            return workspace.organization_id
        except AuthorizationError as error:
            raise PluginError("forbidden", "The operation is not allowed.", status_code=403) from error

    def _require_runtime_command_dispatcher(self) -> PluginRuntimeCommandDispatcher:
        if self._runtime_mode is PluginRuntimeMode.on_demand:
            raise plugin_runtime_mode_unsupported()
        if self._runtime_command_dispatcher is None:
            raise plugin_runtime_control_unavailable()
        return self._runtime_command_dispatcher

    async def _load_version_replay(
        self,
        *,
        actor: AuthenticatedActor,
        operation: str,
        scope_id: str,
        key_digest: str,
        request_digest: str,
    ) -> PluginVersion | None:
        await self._authorize(actor, WorkspaceAction.plugin_manage)
        async with short_session(self._sessions) as session:
            evidence = await _load_evidence(session, actor, operation, scope_id, key_digest)
            if evidence is None:
                return None
            if evidence.request_digest != request_digest or evidence.result_kind != "plugin_version":
                raise plugin_idempotency_conflict()
            version = await session.get(PluginVersionRecord, evidence.result_ref)
        if version is None:
            raise plugin_version_not_found()
        return version.to_resource()

    async def _load_plugin_replay(
        self,
        *,
        actor: AuthenticatedActor,
        operation: str,
        scope_id: str,
        key_digest: str,
        request_digest: str,
    ) -> Plugin | None:
        await self._authorize(actor, WorkspaceAction.plugin_manage)
        async with short_session(self._sessions) as session:
            evidence = await _load_evidence(session, actor, operation, scope_id, key_digest)
            if evidence is None:
                return None
            if evidence.request_digest != request_digest or evidence.result_kind != "plugin":
                raise plugin_idempotency_conflict()
            plugin = await session.get(PluginRecord, evidence.result_ref)
        if plugin is None:
            raise plugin_not_found()
        return plugin.to_resource()

    async def _load_semantic_upload(
        self,
        *,
        actor: AuthenticatedActor,
        plugin_id: str | None,
        inspected: InspectedPluginWheel,
        content_digest: str,
    ) -> PluginVersion | None:
        await self._authorize(actor, WorkspaceAction.plugin_manage)
        async with short_session(self._sessions) as session:
            plugin_query = select(PluginRecord).where(
                PluginRecord.id == plugin_id
                if plugin_id is not None
                else PluginRecord.plugin_key == inspected.plugin_key
            )
            plugin = await session.scalar(plugin_query)
            if plugin is None:
                return None
            version = await session.scalar(
                select(PluginVersionRecord).where(
                    PluginVersionRecord.plugin_id == plugin.id,
                    PluginVersionRecord.version == inspected.version,
                )
            )
        if version is None:
            return None
        if version.content_digest != content_digest:
            raise plugin_version_conflict()
        return version.to_resource()


async def _load_evidence(
    session: AsyncSession,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    key_digest: str,
) -> IdempotencyEvidenceRecord | None:
    return await session.scalar(
        select(IdempotencyEvidenceRecord).where(
            IdempotencyEvidenceRecord.actor_type == actor.principal.principal_type.value,
            IdempotencyEvidenceRecord.actor_id == actor.principal.principal_id,
            IdempotencyEvidenceRecord.operation == operation,
            IdempotencyEvidenceRecord.scope_id == scope_id,
            IdempotencyEvidenceRecord.key_digest == key_digest,
        )
    )


def _evidence(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    operation: str,
    scope_id: str,
    key_digest: str,
    request_digest: str,
    result_kind: str,
    result_ref: str,
    now: datetime,
) -> IdempotencyEvidenceRecord:
    return IdempotencyEvidenceRecord(
        id=new_object_id("idem"),
        organization_id=organization_id,
        workspace_id=actor.boundary_workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        operation=operation,
        scope_id=scope_id,
        key_digest=key_digest,
        request_digest=request_digest,
        result_kind=result_kind,
        result_ref=result_ref,
        created_at=now,
        expires_at=now + IDEMPOTENCY_LIFETIME,
    )


def _audit(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    action: str,
    resource_id: str,
    now: datetime,
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("audit"),
        organization_id=organization_id,
        workspace_id=actor.boundary_workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type="plugin",
        resource_id=resource_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome="success",
        occurred_at=now,
        request_id=actor.request_id,
        details=None,
    )


def _idempotency_key_digest(value: str) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PluginError("invalid_request", "Idempotency-Key is invalid.", status_code=400) from error
    if not encoded or len(encoded) > _MAX_IDEMPOTENCY_KEY_BYTES:
        raise PluginError("invalid_request", "Idempotency-Key is invalid.", status_code=400)
    return hashlib.sha256(encoded).hexdigest()


def _request_digest(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _builtin_manifest_matches(
    registration: BuiltinPluginRegistration,
    inspected: InspectedPluginWheel,
) -> bool:
    return (
        registration.plugin_key == inspected.plugin_key
        and registration.distribution_name == inspected.distribution_name
        and registration.top_level_package == inspected.top_level_package
        and registration.version == inspected.version
    )


def _builtin_plugin_matches(plugin: PluginRecord, registration: BuiltinPluginRegistration) -> bool:
    return (
        plugin.id == registration.plugin_id
        and plugin.source == PluginSource.builtin.value
        and plugin.plugin_key == registration.plugin_key
        and plugin.distribution_name == registration.distribution_name
        and plugin.top_level_package == registration.top_level_package
    )


def _builtin_version_matches(
    version: PluginVersionRecord,
    *,
    registration: BuiltinPluginRegistration,
    inspected: InspectedPluginWheel,
    artifact_ref: str,
    size_bytes: int,
) -> bool:
    return (
        version.id == registration.plugin_version_id
        and version.plugin_id == registration.plugin_id
        and version.version == registration.version
        and version.content_digest == registration.content_digest
        and version.artifact_ref == artifact_ref
        and version.size_bytes == size_bytes
        and tuple(version.requires_dist) == inspected.requires_dist
        and version.requires_python == inspected.requires_python
        and tuple(version.wheel_tags) == inspected.wheel_tags
        and version.root_is_purelib == inspected.root_is_purelib
        and version.entry_point_target == inspected.entry_point_target
        and version.status == "ready"
    )
