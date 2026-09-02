"""Short-transaction repositories for Agent UI durable heads."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from a13n_environment_provider import EnvironmentProviderSafeError
from pydantic import JsonValue
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_ui.errors import StoreConflictError, StoreIntegrityError

from .contracts import (
    ChildExecutionHead,
    EnvironmentBindingHead,
    EnvironmentBindingKey,
    SafeFailure,
    Session,
    SnapshotRef,
)
from .database import short_session, transaction
from .models import (
    AcceptedConfigurationRecord,
    ChildExecutionRecord,
    ChildThreadRecord,
    CompositionSnapshotRecord,
    ConfigurationSnapshotRecord,
    CurrentConfigurationRecord,
    EnvironmentBindingRecord,
    SessionRecord,
)
from .objects import ObjectKind, ObjectRef


class ConfigurationRepository:
    """Select complete accepted configuration and exact immutable snapshots."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def accept(
        self,
        *,
        source_digest: str,
        yaml_digest: str,
        document: JsonValue,
        snapshots: Mapping[tuple[Literal["agent", "environment"], str], ObjectRef],
        restart_required: bool,
        expected_current_digest: str | None,
        accepted_at: datetime | None = None,
    ) -> None:
        """Atomically select one already-published complete snapshot set."""

        now = _utc(accepted_at)
        document_json = _json(document)
        async with transaction(self._sessions) as session:
            current = await session.get(CurrentConfigurationRecord, 1)
            actual = None if current is None else current.source_digest
            if actual != expected_current_digest and actual != source_digest:
                _conflict("configuration_selection_conflict", expected_current_digest, actual)

            accepted = await session.get(AcceptedConfigurationRecord, source_digest)
            if accepted is None:
                session.add(
                    AcceptedConfigurationRecord(
                        source_digest=source_digest,
                        yaml_digest=yaml_digest,
                        document_json=document_json,
                        accepted_at=now,
                        restart_required=restart_required,
                    )
                )
            elif (
                accepted.yaml_digest != yaml_digest
                or accepted.document_json != document_json
                or accepted.restart_required != restart_required
            ):
                raise StoreIntegrityError(
                    "Accepted configuration digest maps to different normalized content.",
                    code="configuration_digest_collision",
                )

            for (snapshot_kind, name), reference in sorted(snapshots.items()):
                _require_snapshot_kind(reference, snapshot_kind)
                snapshot = await session.get(CompositionSnapshotRecord, reference.logical_digest)
                if snapshot is None:
                    session.add(
                        CompositionSnapshotRecord(
                            logical_digest=reference.logical_digest,
                            snapshot_kind=snapshot_kind,
                            object_schema_version=reference.object_schema_version,
                            created_at=now,
                        )
                    )
                elif (
                    snapshot.snapshot_kind != snapshot_kind
                    or snapshot.object_schema_version != reference.object_schema_version
                ):
                    raise StoreIntegrityError(
                        "Snapshot digest maps to incompatible metadata.",
                        code="snapshot_digest_collision",
                    )
                existing = await session.get(
                    ConfigurationSnapshotRecord,
                    {
                        "source_digest": source_digest,
                        "snapshot_kind": snapshot_kind,
                        "name": name,
                    },
                )
                if existing is None:
                    session.add(
                        ConfigurationSnapshotRecord(
                            source_digest=source_digest,
                            snapshot_kind=snapshot_kind,
                            name=name,
                            logical_digest=reference.logical_digest,
                        )
                    )
                elif existing.logical_digest != reference.logical_digest:
                    raise StoreIntegrityError(
                        "Accepted configuration name maps to a different snapshot.",
                        code="configuration_snapshot_collision",
                    )

            if current is None:
                session.add(CurrentConfigurationRecord(singleton_id=1, source_digest=source_digest))
            elif current.source_digest != source_digest:
                current.source_digest = source_digest

    async def current_digest(self) -> str | None:
        async with short_session(self._sessions) as session:
            current = await session.get(CurrentConfigurationRecord, 1)
            return None if current is None else current.source_digest

    async def snapshots(self, source_digest: str) -> dict[tuple[Literal["agent", "environment"], str], ObjectRef]:
        async with short_session(self._sessions) as session:
            rows = (
                await session.execute(
                    select(ConfigurationSnapshotRecord, CompositionSnapshotRecord)
                    .join(
                        CompositionSnapshotRecord,
                        ConfigurationSnapshotRecord.logical_digest == CompositionSnapshotRecord.logical_digest,
                    )
                    .where(ConfigurationSnapshotRecord.source_digest == source_digest)
                )
            ).all()
        result: dict[tuple[Literal["agent", "environment"], str], ObjectRef] = {}
        for selection, snapshot in rows:
            kind = _snapshot_literal(snapshot.snapshot_kind)
            result[(kind, selection.name)] = _snapshot_object_ref(snapshot)
        return result


class SessionRepository:
    """Create Sessions and compare-and-select their root continuation heads."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        session_id: str,
        root_thread_id: str,
        agent_snapshot: SnapshotRef,
        environment_snapshot: SnapshotRef,
        continuation: ObjectRef,
        title: str | None = None,
        parent_fork: JsonValue | None = None,
        pinned: bool = False,
        created_at: datetime | None = None,
    ) -> Session:
        _require_snapshot_ref(agent_snapshot, "agent")
        _require_snapshot_ref(environment_snapshot, "environment")
        _require_object_kind(continuation, ObjectKind.session_continuation)
        now = _utc(created_at)
        async with transaction(self._sessions) as session:
            await _require_snapshot_row(session, agent_snapshot)
            await _require_snapshot_row(session, environment_snapshot)
            record = SessionRecord(
                session_id=session_id,
                root_thread_id=root_thread_id,
                created_at=now,
                updated_at=now,
                title=title,
                archived_at=None,
                pinned=pinned,
                status="active",
                agent_snapshot_digest=agent_snapshot.object.logical_digest,
                environment_snapshot_digest=environment_snapshot.object.logical_digest,
                parent_fork_json=None if parent_fork is None else _json(parent_fork),
                continuation_schema_version=continuation.object_schema_version,
                continuation_digest=continuation.logical_digest,
            )
            session.add(record)
            await session.flush()
            return _session_value(record, agent_snapshot=agent_snapshot, environment_snapshot=environment_snapshot)

    async def get(self, session_id: str) -> Session | None:
        async with short_session(self._sessions) as session:
            record = await session.get(SessionRecord, session_id)
            if record is None:
                return None
            agent = await _snapshot_ref_for(session, record.agent_snapshot_digest)
            environment = await _snapshot_ref_for(session, record.environment_snapshot_digest)
            return _session_value(record, agent_snapshot=agent, environment_snapshot=environment)

    async def list(
        self,
        *,
        query: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[tuple[Session, ...], int]:
        """List bounded detached Session metadata in stable recent-first order."""

        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Session page is outside supported bounds")
        async with short_session(self._sessions) as session:
            statement = select(SessionRecord)
            count_statement = select(func.count()).select_from(SessionRecord)
            normalized = None if query is None else query.strip().casefold()
            if normalized:
                pattern = f"%{_escape_like(normalized)}%"
                predicate = or_(
                    func.lower(SessionRecord.session_id).like(pattern, escape="\\"),
                    func.lower(func.coalesce(SessionRecord.title, "")).like(pattern, escape="\\"),
                )
                statement = statement.where(predicate)
                count_statement = count_statement.where(predicate)
            records = (
                await session.execute(
                    statement.order_by(
                        SessionRecord.pinned.desc(),
                        SessionRecord.updated_at.desc(),
                        SessionRecord.session_id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars()
            values: list[Session] = []
            for record in records:
                agent = await _snapshot_ref_for(session, record.agent_snapshot_digest)
                environment = await _snapshot_ref_for(session, record.environment_snapshot_digest)
                values.append(_session_value(record, agent_snapshot=agent, environment_snapshot=environment))
            total = int((await session.execute(count_statement)).scalar_one())
        return tuple(values), total

    async def select_continuation(
        self,
        *,
        session_id: str,
        expected: ObjectRef,
        replacement: ObjectRef,
        updated_at: datetime | None = None,
    ) -> Session:
        _require_object_kind(expected, ObjectKind.session_continuation)
        _require_object_kind(replacement, ObjectKind.session_continuation)
        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            record = await session.get(SessionRecord, session_id)
            if record is None:
                raise StoreIntegrityError("Session does not exist.", code="session_missing")
            actual = _continuation_ref(record)
            if actual != expected:
                _conflict("session_continuation_conflict", expected.logical_digest, actual.logical_digest)
            if record.status != "active":
                raise StoreIntegrityError("Session is not active.", code="session_not_active")
            record.continuation_schema_version = replacement.object_schema_version
            record.continuation_digest = replacement.logical_digest
            record.updated_at = now
            agent = await _snapshot_ref_for(session, record.agent_snapshot_digest)
            environment = await _snapshot_ref_for(session, record.environment_snapshot_digest)
            await session.flush()
            return _session_value(record, agent_snapshot=agent, environment_snapshot=environment)


class ChildExecutionRepository:
    """Persist child Thread identity and contiguous execution-segment heads."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        execution_id: str,
        session_id: str,
        parent_thread_id: str,
        child_thread_id: str,
        child_run_id: str,
        subagent_name: str,
        child_definition_id: str,
        child_definition_digest: str,
        input: str,
        owner_app_instance_id: str,
        created_at: datetime | None = None,
    ) -> ChildExecutionHead:
        now = _utc(created_at)
        async with transaction(self._sessions) as session:
            root = await session.get(SessionRecord, session_id)
            if root is None or root.status != "active":
                raise StoreIntegrityError("Session is unavailable for child admission.", code="session_not_active")
            if root.root_thread_id != parent_thread_id:
                parent = await session.get(ChildThreadRecord, parent_thread_id)
                if parent is None or parent.session_id != session_id:
                    raise StoreIntegrityError(
                        "Parent Thread is outside the child admission Session.",
                        code="child_parent_scope_mismatch",
                    )
            thread = ChildThreadRecord(
                child_thread_id=child_thread_id,
                session_id=session_id,
                parent_thread_id=parent_thread_id,
                subagent_name=subagent_name,
                child_definition_id=child_definition_id,
                child_definition_digest=child_definition_digest,
                created_at=now,
            )
            execution = ChildExecutionRecord(
                execution_id=execution_id,
                session_id=session_id,
                child_thread_id=child_thread_id,
                child_run_id=child_run_id,
                segment_index=0,
                input_text=input,
                status="running",
                selected_checkpoint_schema_version=None,
                selected_checkpoint_digest=None,
                selected_checkpoint_terminal=False,
                resumable=False,
                resumed_from=None,
                failure_json=None,
                owner_app_instance_id=owner_app_instance_id,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            session.add(thread)
            await session.flush()
            session.add(execution)
            await session.flush()
            return _child_value(execution, thread)

    async def resume(
        self,
        *,
        previous_execution_id: str,
        execution_id: str,
        child_run_id: str,
        child_definition_digest: str,
        input: str,
        owner_app_instance_id: str,
        session_id: str | None = None,
        parent_thread_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ChildExecutionHead:
        now = _utc(created_at)
        async with transaction(self._sessions) as session:
            previous = await session.get(ChildExecutionRecord, previous_execution_id)
            if previous is None:
                raise StoreIntegrityError("Previous child execution does not exist.", code="child_execution_missing")
            thread = await session.get(ChildThreadRecord, previous.child_thread_id)
            if thread is None:
                raise StoreIntegrityError("Child Thread index is missing.", code="child_thread_missing")
            root = await session.get(SessionRecord, previous.session_id)
            if root is None or root.status != "active":
                raise StoreIntegrityError("Session is unavailable for child resume.", code="session_not_active")
            if session_id is not None and previous.session_id != session_id:
                raise StoreIntegrityError(
                    "Child execution is outside the parent Session.", code="child_execution_scope_mismatch"
                )
            if parent_thread_id is not None and thread.parent_thread_id != parent_thread_id:
                raise StoreIntegrityError(
                    "Child execution is outside the parent Thread.", code="child_execution_scope_mismatch"
                )
            if previous.status == "running" or not previous.resumable or previous.selected_checkpoint_digest is None:
                raise StoreIntegrityError(
                    "Child execution does not have a resumable selected checkpoint.",
                    code="child_execution_not_resumable",
                )
            if thread.child_definition_digest != child_definition_digest:
                raise StoreIntegrityError(
                    "Child definition is incompatible with the retained Thread.",
                    code="child_definition_incompatible",
                )
            previous.resumable = False
            previous.updated_at = now
            execution = ChildExecutionRecord(
                execution_id=execution_id,
                session_id=previous.session_id,
                child_thread_id=previous.child_thread_id,
                child_run_id=child_run_id,
                segment_index=previous.segment_index + 1,
                input_text=input,
                status="running",
                selected_checkpoint_schema_version=None,
                selected_checkpoint_digest=None,
                selected_checkpoint_terminal=False,
                resumable=False,
                resumed_from=previous.execution_id,
                failure_json=None,
                owner_app_instance_id=owner_app_instance_id,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            session.add(execution)
            await session.flush()
            return _child_value(execution, thread)

    async def get(self, execution_id: str) -> ChildExecutionHead | None:
        async with short_session(self._sessions) as session:
            execution = await session.get(ChildExecutionRecord, execution_id)
            if execution is None:
                return None
            thread = await session.get(ChildThreadRecord, execution.child_thread_id)
            if thread is None:
                raise StoreIntegrityError("Child Thread index is missing.", code="child_thread_missing")
            return _child_value(execution, thread)

    async def get_scoped(
        self,
        *,
        execution_id: str,
        session_id: str,
        parent_thread_id: str,
    ) -> ChildExecutionHead | None:
        head = await self.get(execution_id)
        if head is None or head.session_id != session_id or head.parent_thread_id != parent_thread_id:
            return None
        return head

    async def owns_thread(self, *, session_id: str, thread_id: str) -> bool:
        async with short_session(self._sessions) as session:
            root = await session.get(SessionRecord, session_id)
            if root is None:
                return False
            if root.root_thread_id == thread_id:
                return True
            child = await session.get(ChildThreadRecord, thread_id)
            return child is not None and child.session_id == session_id

    async def list_scope(
        self,
        *,
        session_id: str,
        parent_thread_id: str,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[tuple[ChildExecutionHead, ...], int]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("child execution page is outside supported bounds")
        async with short_session(self._sessions) as session:
            scoped = (
                select(ChildExecutionRecord, ChildThreadRecord)
                .join(ChildThreadRecord, ChildExecutionRecord.child_thread_id == ChildThreadRecord.child_thread_id)
                .where(
                    ChildExecutionRecord.session_id == session_id,
                    ChildThreadRecord.parent_thread_id == parent_thread_id,
                )
            )
            rows = (
                await session.execute(
                    scoped.order_by(
                        ChildExecutionRecord.created_at.desc(),
                        ChildExecutionRecord.execution_id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            total = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(ChildExecutionRecord)
                        .join(
                            ChildThreadRecord,
                            ChildExecutionRecord.child_thread_id == ChildThreadRecord.child_thread_id,
                        )
                        .where(
                            ChildExecutionRecord.session_id == session_id,
                            ChildThreadRecord.parent_thread_id == parent_thread_id,
                        )
                    )
                ).scalar_one()
            )
        return tuple(_child_value(execution, thread) for execution, thread in rows), total

    async def running_owner_instance_ids(self) -> tuple[str, ...]:
        """List App instances that still own a persisted running segment."""

        async with short_session(self._sessions) as session:
            rows = await session.execute(
                select(ChildExecutionRecord.owner_app_instance_id)
                .where(ChildExecutionRecord.status == "running")
                .distinct()
                .order_by(ChildExecutionRecord.owner_app_instance_id)
            )
            return tuple(rows.scalars())

    async def mark_owner_lost(
        self,
        *,
        owner_app_instance_id: str,
        updated_at: datetime | None = None,
    ) -> int:
        """Mark only this App instance's still-running child segments as lost."""

        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            records = (
                await session.execute(
                    select(ChildExecutionRecord).where(
                        ChildExecutionRecord.owner_app_instance_id == owner_app_instance_id,
                        ChildExecutionRecord.status == "running",
                    )
                )
            ).scalars()
            count = 0
            for execution in records:
                execution.status = "lost"
                execution.selected_checkpoint_terminal = False
                execution.resumable = execution.selected_checkpoint_digest is not None
                execution.failure_json = None
                execution.updated_at = now
                execution.completed_at = now
                count += 1
            await session.flush()
            return count

    async def select_checkpoint(
        self,
        *,
        execution_id: str,
        expected: ObjectRef | None,
        checkpoint: ObjectRef,
        child_run_id: str,
        terminal_status: Literal["succeeded", "failed", "cancelled"] | None = None,
        failure: SafeFailure | None = None,
        resumable: bool = False,
        updated_at: datetime | None = None,
    ) -> ChildExecutionHead:
        _require_object_kind(checkpoint, ObjectKind.child_checkpoint)
        if expected is not None:
            _require_object_kind(expected, ObjectKind.child_checkpoint)
        if terminal_status == "succeeded" and failure is not None:
            raise ValueError("successful child execution cannot retain a failure")
        if terminal_status == "failed" and failure is None:
            raise ValueError("failed child execution requires a safe failure")
        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            execution = await session.get(ChildExecutionRecord, execution_id)
            if execution is None:
                raise StoreIntegrityError("Child execution does not exist.", code="child_execution_missing")
            thread = await session.get(ChildThreadRecord, execution.child_thread_id)
            if thread is None:
                raise StoreIntegrityError("Child Thread index is missing.", code="child_thread_missing")
            if execution.status != "running":
                raise StoreIntegrityError("Child execution is already terminal.", code="child_execution_terminal")
            actual = _checkpoint_ref(execution)
            if actual != expected:
                _conflict(
                    "child_checkpoint_conflict",
                    None if expected is None else expected.logical_digest,
                    None if actual is None else actual.logical_digest,
                )
            execution.child_run_id = child_run_id
            execution.selected_checkpoint_schema_version = checkpoint.object_schema_version
            execution.selected_checkpoint_digest = checkpoint.logical_digest
            execution.selected_checkpoint_terminal = terminal_status is not None
            execution.resumable = terminal_status is not None and resumable
            execution.updated_at = now
            if terminal_status is not None:
                execution.status = terminal_status
                execution.failure_json = None if failure is None else _json(failure.model_dump(mode="json"))
                execution.completed_at = now
            await session.flush()
            return _child_value(execution, thread)

    async def finish_without_checkpoint(
        self,
        *,
        execution_id: str,
        status: Literal["failed", "cancelled", "lost"],
        failure: SafeFailure | None = None,
        resumable: bool = False,
        updated_at: datetime | None = None,
    ) -> ChildExecutionHead:
        if status == "failed" and failure is None:
            raise ValueError("failed child execution requires a safe failure")
        if status != "failed" and failure is not None:
            raise ValueError("only failed child execution may retain a failure")
        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            execution = await session.get(ChildExecutionRecord, execution_id)
            if execution is None:
                raise StoreIntegrityError("Child execution does not exist.", code="child_execution_missing")
            thread = await session.get(ChildThreadRecord, execution.child_thread_id)
            if thread is None:
                raise StoreIntegrityError("Child Thread index is missing.", code="child_thread_missing")
            if execution.status != "running":
                return _child_value(execution, thread)
            execution.status = status
            execution.selected_checkpoint_terminal = False
            execution.resumable = resumable and execution.selected_checkpoint_digest is not None
            execution.failure_json = None if failure is None else _json(failure.model_dump(mode="json"))
            execution.updated_at = now
            execution.completed_at = now
            await session.flush()
            return _child_value(execution, thread)

    async def fail_terminal_persistence(
        self,
        *,
        execution_id: str,
        failure: SafeFailure,
        updated_at: datetime | None = None,
    ) -> ChildExecutionHead:
        """Fail closed while retaining any prior progress checkpoint for inspection only."""

        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            execution = await session.get(ChildExecutionRecord, execution_id)
            if execution is None:
                raise StoreIntegrityError("Child execution does not exist.", code="child_execution_missing")
            thread = await session.get(ChildThreadRecord, execution.child_thread_id)
            if thread is None:
                raise StoreIntegrityError("Child Thread index is missing.", code="child_thread_missing")
            if execution.status != "running":
                raise StoreIntegrityError("Child execution is already terminal.", code="child_execution_terminal")
            execution.status = "failed"
            execution.selected_checkpoint_terminal = False
            execution.resumable = False
            execution.failure_json = _json(failure.model_dump(mode="json"))
            execution.updated_at = now
            execution.completed_at = now
            await session.flush()
            return _child_value(execution, thread)


class EnvironmentStateRepository:
    """Compare-and-select Host-authoritative Environment state under the complete key."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ensure(self, key: EnvironmentBindingKey, *, created_at: datetime | None = None) -> EnvironmentBindingHead:
        now = _utc(created_at)
        async with transaction(self._sessions) as session:
            record = await session.get(EnvironmentBindingRecord, _binding_identity(key))
            if record is None:
                root = await session.get(SessionRecord, key.session_id)
                if root is None:
                    raise StoreIntegrityError("Session does not exist.", code="session_missing")
                if root.environment_snapshot_digest != key.profile_digest:
                    raise StoreIntegrityError(
                        "Environment binding profile does not match the Session pin.",
                        code="environment_profile_mismatch",
                    )
                record = EnvironmentBindingRecord(
                    session_id=key.session_id,
                    profile_digest=key.profile_digest,
                    binder_key=key.binder_key,
                    normalized_folder=key.normalized_folder,
                    state_schema_version=None,
                    state_digest=None,
                    cleanup_status="none",
                    cleanup_failure_json=None,
                    updated_at=now,
                )
                session.add(record)
                await session.flush()
            return _binding_value(record)

    async def get(self, key: EnvironmentBindingKey) -> EnvironmentBindingHead | None:
        async with short_session(self._sessions) as session:
            record = await session.get(EnvironmentBindingRecord, _binding_identity(key))
            return None if record is None else _binding_value(record)

    async def select_state(
        self,
        *,
        key: EnvironmentBindingKey,
        expected: ObjectRef | None,
        replacement: ObjectRef | None,
        updated_at: datetime | None = None,
    ) -> EnvironmentBindingHead:
        if expected is not None:
            _require_object_kind(expected, ObjectKind.environment_state)
        if replacement is not None:
            _require_object_kind(replacement, ObjectKind.environment_state)
        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            record = await session.get(EnvironmentBindingRecord, _binding_identity(key))
            if record is None:
                raise StoreIntegrityError(
                    "Environment binding authority has not been established.",
                    code="environment_binding_missing",
                )
            actual = _environment_state_ref(record)
            if actual != expected:
                _conflict(
                    "environment_state_conflict",
                    None if expected is None else expected.logical_digest,
                    None if actual is None else actual.logical_digest,
                )
            record.state_schema_version = None if replacement is None else replacement.object_schema_version
            record.state_digest = None if replacement is None else replacement.logical_digest
            record.updated_at = now
            await session.flush()
            return _binding_value(record)

    async def set_cleanup(
        self,
        *,
        key: EnvironmentBindingKey,
        status: Literal["none", "required", "in_progress", "failed"],
        failure: EnvironmentProviderSafeError | None = None,
        updated_at: datetime | None = None,
    ) -> EnvironmentBindingHead:
        if status == "failed" and failure is None:
            raise ValueError("failed cleanup requires a safe failure")
        if status != "failed" and failure is not None:
            raise ValueError("only failed cleanup may retain a failure")
        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            record = await session.get(EnvironmentBindingRecord, _binding_identity(key))
            if record is None:
                raise StoreIntegrityError("Environment binding does not exist.", code="environment_binding_missing")
            record.cleanup_status = status
            record.cleanup_failure_json = None if failure is None else _json(failure.model_dump(mode="json"))
            record.updated_at = now
            await session.flush()
            return _binding_value(record)


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("stored timestamps must include a UTC offset")
    return current.astimezone(UTC)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _parse_json(value: str | None) -> JsonValue | None:
    if value is None:
        return None
    try:
        parsed: JsonValue = json.loads(value)
    except ValueError as exc:
        raise StoreIntegrityError("Stored JSON is invalid.", code="stored_json_invalid") from exc
    return parsed


def _failure(value: str | None) -> SafeFailure | None:
    if value is None:
        return None
    try:
        return SafeFailure.model_validate_json(value)
    except ValueError as exc:
        raise StoreIntegrityError("Stored failure is invalid.", code="stored_failure_invalid") from exc


def _require_object_kind(reference: ObjectRef, expected: ObjectKind) -> None:
    if reference.object_kind is not expected:
        raise ValueError(f"expected {expected.value} object reference")


def _require_snapshot_kind(reference: ObjectRef, snapshot_kind: Literal["agent", "environment"]) -> None:
    _require_object_kind(
        reference,
        ObjectKind.agent_snapshot if snapshot_kind == "agent" else ObjectKind.environment_snapshot,
    )


def _require_snapshot_ref(reference: SnapshotRef, expected: Literal["agent", "environment"]) -> None:
    if reference.snapshot_kind != expected:
        raise ValueError(f"expected {expected} snapshot")
    _require_snapshot_kind(reference.object, expected)


async def _require_snapshot_row(session: AsyncSession, reference: SnapshotRef) -> None:
    row = await session.get(CompositionSnapshotRecord, reference.object.logical_digest)
    if row is None:
        raise StoreIntegrityError("Selected snapshot is not indexed.", code="snapshot_missing")
    if (
        row.snapshot_kind != reference.snapshot_kind
        or row.object_schema_version != reference.object.object_schema_version
    ):
        raise StoreIntegrityError("Selected snapshot metadata is incompatible.", code="snapshot_incompatible")


def _snapshot_literal(value: str) -> Literal["agent", "environment"]:
    if value == "agent":
        return "agent"
    if value == "environment":
        return "environment"
    raise StoreIntegrityError("Stored snapshot kind is invalid.", code="snapshot_kind_invalid")


def _snapshot_object_ref(row: CompositionSnapshotRecord) -> ObjectRef:
    kind = _snapshot_literal(row.snapshot_kind)
    return ObjectRef(
        object_kind=ObjectKind.agent_snapshot if kind == "agent" else ObjectKind.environment_snapshot,
        object_schema_version=row.object_schema_version,
        logical_digest=row.logical_digest,
    )


async def _snapshot_ref_for(session: AsyncSession, digest: str) -> SnapshotRef:
    row = await session.get(CompositionSnapshotRecord, digest)
    if row is None:
        raise StoreIntegrityError("Session snapshot index is missing.", code="snapshot_missing")
    return SnapshotRef(snapshot_kind=_snapshot_literal(row.snapshot_kind), object=_snapshot_object_ref(row))


def _continuation_ref(record: SessionRecord) -> ObjectRef:
    return ObjectRef(
        object_kind=ObjectKind.session_continuation,
        object_schema_version=record.continuation_schema_version,
        logical_digest=record.continuation_digest,
    )


def _session_value(
    record: SessionRecord,
    *,
    agent_snapshot: SnapshotRef,
    environment_snapshot: SnapshotRef,
) -> Session:
    status: Literal["active", "deleting"]
    if record.status == "active":
        status = "active"
    elif record.status == "deleting":
        status = "deleting"
    else:
        raise StoreIntegrityError("Stored Session status is invalid.", code="session_status_invalid")
    return Session(
        session_id=record.session_id,
        root_thread_id=record.root_thread_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        title=record.title,
        archived_at=record.archived_at,
        pinned=record.pinned,
        status=status,
        agent_snapshot=agent_snapshot,
        environment_snapshot=environment_snapshot,
        parent_fork=_parse_json(record.parent_fork_json),
        continuation=_continuation_ref(record),
    )


def _checkpoint_ref(record: ChildExecutionRecord) -> ObjectRef | None:
    if record.selected_checkpoint_digest is None:
        if record.selected_checkpoint_schema_version is not None:
            raise StoreIntegrityError("Stored checkpoint reference is incomplete.", code="checkpoint_ref_invalid")
        return None
    if record.selected_checkpoint_schema_version is None:
        raise StoreIntegrityError("Stored checkpoint reference is incomplete.", code="checkpoint_ref_invalid")
    return ObjectRef(
        object_kind=ObjectKind.child_checkpoint,
        object_schema_version=record.selected_checkpoint_schema_version,
        logical_digest=record.selected_checkpoint_digest,
    )


def _child_value(record: ChildExecutionRecord, thread: ChildThreadRecord) -> ChildExecutionHead:
    if record.session_id != thread.session_id:
        raise StoreIntegrityError("Child execution Session does not match its Thread.", code="child_session_mismatch")
    return ChildExecutionHead(
        execution_id=record.execution_id,
        session_id=record.session_id,
        parent_thread_id=thread.parent_thread_id,
        child_thread_id=record.child_thread_id,
        child_run_id=record.child_run_id,
        segment_index=record.segment_index,
        subagent_name=thread.subagent_name,
        child_definition_id=thread.child_definition_id,
        child_definition_digest=thread.child_definition_digest,
        input=record.input_text,
        status=record.status,  # type: ignore[arg-type]
        selected_checkpoint=_checkpoint_ref(record),
        selected_checkpoint_terminal=record.selected_checkpoint_terminal,
        resumable=record.resumable,
        resumed_from=record.resumed_from,
        failure=_failure(record.failure_json),
        owner_app_instance_id=record.owner_app_instance_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        completed_at=record.completed_at,
    )


def _binding_identity(key: EnvironmentBindingKey) -> dict[str, str]:
    return {
        "session_id": key.session_id,
        "profile_digest": key.profile_digest,
        "binder_key": key.binder_key,
        "normalized_folder": key.normalized_folder,
    }


def _environment_state_ref(record: EnvironmentBindingRecord) -> ObjectRef | None:
    if record.state_digest is None:
        if record.state_schema_version is not None:
            raise StoreIntegrityError(
                "Stored Environment state reference is incomplete.", code="environment_state_ref_invalid"
            )
        return None
    if record.state_schema_version is None:
        raise StoreIntegrityError(
            "Stored Environment state reference is incomplete.", code="environment_state_ref_invalid"
        )
    return ObjectRef(
        object_kind=ObjectKind.environment_state,
        object_schema_version=record.state_schema_version,
        logical_digest=record.state_digest,
    )


def _binding_value(record: EnvironmentBindingRecord) -> EnvironmentBindingHead:
    key = EnvironmentBindingKey(
        session_id=record.session_id,
        profile_digest=record.profile_digest,
        binder_key=record.binder_key,
        normalized_folder=record.normalized_folder,
    )
    status: Literal["none", "required", "in_progress", "failed"]
    if record.cleanup_status in {"none", "required", "in_progress", "failed"}:
        status = record.cleanup_status  # type: ignore[assignment]
    else:
        raise StoreIntegrityError("Stored cleanup status is invalid.", code="cleanup_status_invalid")
    return EnvironmentBindingHead(
        key=key,
        state=_environment_state_ref(record),
        cleanup_status=status,
        cleanup_failure=_provider_failure(record.cleanup_failure_json),
        updated_at=record.updated_at,
    )


def _provider_failure(value: str | None) -> EnvironmentProviderSafeError | None:
    if value is None:
        return None
    try:
        return EnvironmentProviderSafeError.model_validate_json(value)
    except ValueError as exc:
        raise StoreIntegrityError(
            "Stored Environment cleanup failure is invalid.",
            code="stored_failure_invalid",
        ) from exc


def _conflict(code: str, expected: object, actual: object) -> None:
    raise StoreConflictError(
        "Durable head changed after admission; the newer value was preserved.",
        code=code,
        details={"expected": expected, "actual": actual},
    )


__all__ = [
    "ChildExecutionRepository",
    "ConfigurationRepository",
    "EnvironmentStateRepository",
    "SessionRepository",
]
