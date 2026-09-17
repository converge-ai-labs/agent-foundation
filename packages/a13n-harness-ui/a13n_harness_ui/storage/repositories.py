"""Short-transaction repositories for Harness UI durable heads."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import and_, case, func, or_, select, true, update

from a13n_harness_ui.conversation import ConversationExcerpt
from a13n_harness_ui.errors import StoreConflictError, StoreIntegrityError

from .contracts import (
    AgentResourceSource,
    ChildExecutionHead,
    EnvironmentBindingHead,
    EnvironmentBindingKey,
    ExecutionStatus,
    MarkdownSubagentSource,
    ResourceIndexEntry,
    SafeFailure,
    Thread,
    ThreadCompletion,
    ThreadConfiguration,
    ThreadReadModel,
)
from .database import DatabaseSessions, short_session, transaction
from .models import (
    AcceptedConfigurationRecord,
    ChildExecutionRecord,
    ConfigurationSourceRecord,
    CurrentConfigurationRecord,
    EnvironmentBindingRecord,
    ProjectModelPreferenceRecord,
    ResourceIndexRecord,
    ThreadConfigurationRecord,
    ThreadRecord,
)
from .objects import ObjectKind, ObjectRef


class ProjectModelPreferenceRepository:
    """Last explicit Model choice per Project, independent of resource definitions."""

    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def get(self, project_id: str) -> str | None:
        async with short_session(self._sessions) as session:
            record = await session.get(ProjectModelPreferenceRecord, project_id)
            return None if record is None else record.model_id

    async def set(self, project_id: str, model_id: str | None) -> None:
        async with transaction(self._sessions) as session:
            record = await session.get(ProjectModelPreferenceRecord, project_id)
            if model_id is None:
                if record is not None:
                    await session.delete(record)
            elif record is None:
                session.add(ProjectModelPreferenceRecord(project_id=project_id, model_id=model_id))
            else:
                record.model_id = model_id


class ConfigurationRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def accept(
        self,
        *,
        generation_digest: str,
        generation: ObjectRef,
        sources: Iterable[tuple[str, str, str, str | None]],
        resources: Iterable[ResourceIndexEntry],
        expected_current_digest: str | None,
        accepted_at: datetime | None = None,
    ) -> None:
        _require_kind(generation, ObjectKind.configuration_generation)
        now = _utc(accepted_at)
        async with transaction(self._sessions) as session:
            current = await session.get(CurrentConfigurationRecord, 1)
            actual = None if current is None else current.generation_digest
            if actual not in {expected_current_digest, generation_digest}:
                _conflict("configuration_selection_conflict", expected_current_digest, actual)
            record = await session.get(AcceptedConfigurationRecord, generation_digest)
            if record is None:
                session.add(
                    AcceptedConfigurationRecord(
                        generation_digest=generation_digest,
                        object_schema_version=generation.object_schema_version,
                        object_digest=generation.logical_digest,
                        accepted_at=now,
                    )
                )
                for relative_path, source_digest, resource_kind, resource_id in sources:
                    session.add(
                        ConfigurationSourceRecord(
                            generation_digest=generation_digest,
                            relative_path=relative_path,
                            source_digest=source_digest,
                            resource_kind=resource_kind,
                            resource_id=resource_id,
                        )
                    )
                for item in resources:
                    session.add(
                        ResourceIndexRecord(
                            generation_digest=generation_digest,
                            relative_path=item.relative_path,
                            resource_kind=item.resource_kind,
                            resource_id=item.resource_id,
                            name=item.name,
                            source_digest=item.source_digest,
                            normalized_digest=item.normalized_digest,
                        )
                    )
            elif (
                record.object_schema_version != generation.object_schema_version
                or record.object_digest != generation.logical_digest
            ):
                raise StoreIntegrityError(
                    "Accepted generation digest maps to different immutable content.",
                    code="configuration_digest_collision",
                )
            if current is None:
                session.add(CurrentConfigurationRecord(singleton_id=1, generation_digest=generation_digest))
            else:
                current.generation_digest = generation_digest

    async def current_digest(self) -> str | None:
        async with short_session(self._sessions) as session:
            current = await session.get(CurrentConfigurationRecord, 1)
            return None if current is None else current.generation_digest

    async def current_reference(self) -> ObjectRef | None:
        async with short_session(self._sessions) as session:
            current = await session.get(CurrentConfigurationRecord, 1)
            if current is None:
                return None
            record = await session.get(AcceptedConfigurationRecord, current.generation_digest)
            if record is None:
                raise StoreIntegrityError("Current configuration generation is missing.", code="configuration_missing")
            return _configuration_reference(record)

    async def reference(self, generation_digest: str) -> ObjectRef | None:
        async with short_session(self._sessions) as session:
            record = await session.get(AcceptedConfigurationRecord, generation_digest)
            return None if record is None else _configuration_reference(record)

    async def resources(self, generation_digest: str) -> tuple[ResourceIndexEntry, ...]:
        async with short_session(self._sessions) as session:
            rows = (
                await session.execute(
                    select(ResourceIndexRecord)
                    .where(ResourceIndexRecord.generation_digest == generation_digest)
                    .order_by(ResourceIndexRecord.resource_kind, ResourceIndexRecord.resource_id)
                )
            ).scalars()
            return tuple(
                ResourceIndexEntry(
                    generation_digest=row.generation_digest,
                    resource_kind=row.resource_kind,
                    resource_id=row.resource_id,
                    name=row.name,
                    relative_path=row.relative_path,
                    source_digest=row.source_digest,
                    normalized_digest=row.normalized_digest,
                )
                for row in rows
            )


class ThreadRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        thread_id: str,
        configuration: ThreadConfiguration,
        initial_state: ObjectRef,
        parent_thread_id: str | None = None,
        title: str | None = None,
        created_at: datetime | None = None,
    ) -> Thread:
        _require_kind(initial_state, ObjectKind.thread_initial_state)
        now = _utc(created_at)
        async with transaction(self._sessions) as session:
            if await session.get(ThreadRecord, thread_id) is not None:
                raise StoreIntegrityError(
                    "Thread identity already exists; read it before retrying.", code="thread_exists"
                )
            if parent_thread_id is not None and await session.get(ThreadRecord, parent_thread_id) is None:
                raise StoreIntegrityError("Parent Thread does not exist.", code="thread_parent_missing")
            record = ThreadRecord(
                thread_id=thread_id,
                parent_thread_id=parent_thread_id,
                metadata_version=1,
                title=title,
                search_text=f"{thread_id}\n{title or ''}".casefold(),
                archived=False,
                created_at=now,
                updated_at=now,
                touched_at=now,
                initial_state_schema_version=initial_state.object_schema_version,
                initial_state_digest=initial_state.logical_digest,
                continuation_schema_version=None,
                continuation_digest=None,
            )
            session.add(record)
            await session.flush()
            session.add(_configuration_record(thread_id, configuration))
            await session.flush()
            return _thread_value(record, configuration)

    async def touch(self, thread_id: str, *, touched_at: datetime | None = None) -> None:
        """Advance navigation recency without changing content or metadata versions."""

        now = _utc(touched_at)
        current = func.coalesce(ThreadRecord.touched_at, ThreadRecord.created_at)
        async with transaction(self._sessions) as session:
            touched = await session.scalar(
                update(ThreadRecord)
                .where(ThreadRecord.thread_id == thread_id)
                .values(touched_at=case((current < now, now), else_=current))
                .returning(ThreadRecord.thread_id)
            )
            if touched is None:
                raise StoreIntegrityError("Thread does not exist.", code="thread_missing")

    async def get(self, thread_id: str) -> Thread | None:
        async with short_session(self._sessions) as session:
            record = await session.get(ThreadRecord, thread_id)
            if record is None:
                return None
            configuration = await session.get(ThreadConfigurationRecord, thread_id)
            if configuration is None:
                raise StoreIntegrityError("Thread configuration is missing.", code="thread_configuration_missing")
            return _thread_value(record, _configuration_value(configuration))

    async def list(
        self,
        *,
        query: str | None = None,
        project_id: str | None = None,
        include_children: bool = False,
        projectless: bool = False,
        include_archived: bool = False,
        archived_only: bool = False,
        project_ids: tuple[str, ...] | None = None,
        sort: Literal["updated", "activity", "touched"] = "updated",
        thread_ids: tuple[str, ...] | None = None,
        exclude_thread_ids: tuple[str, ...] = (),
        before: tuple[datetime, str] | None = None,
        limit: int = 20,
    ) -> tuple[tuple[Thread, ...], int]:
        if not 1 <= limit <= 101:
            raise ValueError("Thread page is outside supported bounds")
        if before is not None and (not before[1] or before[0].tzinfo is None or before[0].utcoffset() is None):
            raise ValueError("Thread page cursor is invalid")
        if sum((project_id is not None, project_ids is not None, projectless)) > 1:
            raise ValueError("Choose only one Project filter")
        order_time = {
            "updated": ThreadRecord.updated_at,
            "activity": func.coalesce(ThreadRecord.activity_at, ThreadRecord.created_at),
            "touched": func.coalesce(ThreadRecord.touched_at, ThreadRecord.created_at),
        }[sort]
        async with short_session(self._sessions) as session:
            statement = select(ThreadRecord)
            count_statement = select(func.count()).select_from(ThreadRecord)
            if project_id is not None or project_ids is not None or projectless:
                statement = statement.join(
                    ThreadConfigurationRecord,
                    ThreadConfigurationRecord.thread_id == ThreadRecord.thread_id,
                )
                count_statement = count_statement.join(
                    ThreadConfigurationRecord,
                    ThreadConfigurationRecord.thread_id == ThreadRecord.thread_id,
                )
            predicates = []
            if thread_ids is not None:
                predicates.append(ThreadRecord.thread_id.in_(thread_ids))
            if exclude_thread_ids:
                predicates.append(ThreadRecord.thread_id.not_in(exclude_thread_ids))
            if project_id is not None:
                predicates.append(ThreadConfigurationRecord.project_id == project_id)
            if project_ids is not None:
                predicates.append(ThreadConfigurationRecord.project_id.in_(project_ids))
            if projectless:
                predicates.append(ThreadConfigurationRecord.project_id.is_(None))
            if not include_children:
                predicates.append(ThreadRecord.parent_thread_id.is_(None))
            if archived_only:
                predicates.append(ThreadRecord.archived.is_(True))
            elif not include_archived:
                predicates.append(ThreadRecord.archived.is_(False))
            normalized = None if query is None else query.strip().casefold()
            if normalized:
                pattern = f"%{_escape_like(normalized)}%"
                predicates.append(ThreadRecord.search_text.like(pattern, escape="\\"))
            if predicates:
                statement = statement.where(*predicates)
                count_statement = count_statement.where(*predicates)
            if before is not None:
                before_updated_at, before_thread_id = before
                statement = statement.where(
                    or_(
                        order_time < before_updated_at,
                        and_(
                            order_time == before_updated_at,
                            ThreadRecord.thread_id < before_thread_id,
                        ),
                    )
                )
            records = tuple(
                (
                    await session.execute(
                        statement.order_by(order_time.desc(), ThreadRecord.thread_id.desc()).limit(limit)
                    )
                ).scalars()
            )
            configuration_rows = (
                ()
                if not records
                else tuple(
                    (
                        await session.execute(
                            select(ThreadConfigurationRecord).where(
                                ThreadConfigurationRecord.thread_id.in_(tuple(record.thread_id for record in records))
                            )
                        )
                    ).scalars()
                )
            )
            configurations = {row.thread_id: row for row in configuration_rows}
            values: list[Thread] = []
            for record in records:
                configuration = configurations.get(record.thread_id)
                if configuration is None:
                    raise StoreIntegrityError("Thread configuration is missing.", code="thread_configuration_missing")
                values.append(_thread_value(record, _configuration_value(configuration)))
            total = int((await session.execute(count_statement)).scalar_one())
            return tuple(values), total

    async def update_configuration(
        self,
        *,
        thread_id: str,
        expected_version: int,
        replacement: ThreadConfiguration,
        updated_at: datetime | None = None,
    ) -> Thread:
        if replacement.version != expected_version + 1:
            raise ValueError("replacement configuration version must increment by one")
        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            record = await session.get(ThreadRecord, thread_id)
            configuration = await session.get(ThreadConfigurationRecord, thread_id)
            if record is None or configuration is None:
                raise StoreIntegrityError("Thread does not exist.", code="thread_missing")
            if configuration.version != expected_version:
                _conflict("thread_configuration_conflict", expected_version, configuration.version)
            _assign_configuration(configuration, replacement)
            record.updated_at = now
            await session.flush()
            return _thread_value(record, replacement)

    async def update_metadata(
        self,
        *,
        thread_id: str,
        expected_version: int,
        title: str | None,
        archived: bool,
        updated_at: datetime | None = None,
    ) -> Thread:
        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            record = await session.get(ThreadRecord, thread_id)
            configuration = await session.get(ThreadConfigurationRecord, thread_id)
            if record is None or configuration is None:
                raise StoreIntegrityError("Thread does not exist.", code="thread_missing")
            if record.metadata_version != expected_version:
                _conflict("thread_metadata_conflict", expected_version, record.metadata_version)
            if record.title == title and record.archived is archived:
                return _thread_value(record, _configuration_value(configuration))
            record.metadata_version += 1
            record.title = title
            record.search_text = _search_text(record)
            record.archived = archived
            record.updated_at = now
            await session.flush()
            return _thread_value(record, _configuration_value(configuration))

    async def continuation_references(self, thread_ids: tuple[str, ...]) -> dict[str, ObjectRef]:
        if not thread_ids:
            return {}
        async with short_session(self._sessions) as session:
            records = tuple(
                (await session.execute(select(ThreadRecord).where(ThreadRecord.thread_id.in_(thread_ids)))).scalars()
            )
        return {
            record.thread_id: reference for record in records if (reference := _continuation_ref(record)) is not None
        }

    async def read_models(self, thread_ids: tuple[str, ...]) -> dict[str, ThreadReadModel]:
        if not thread_ids:
            return {}
        async with short_session(self._sessions) as session:
            records = (await session.scalars(select(ThreadRecord).where(ThreadRecord.thread_id.in_(thread_ids)))).all()
            return {record.thread_id: value for record in records if (value := _read_model(record)) is not None}

    async def missing_read_models(self, *, after: str = "", limit: int = 16) -> tuple[tuple[str, ObjectRef], ...]:
        """Keyset batch for maintenance, including heads advanced by older writers."""
        async with short_session(self._sessions) as session:
            records = await session.scalars(
                select(ThreadRecord)
                .where(
                    ThreadRecord.thread_id > after,
                    ThreadRecord.continuation_digest.is_not(None),
                    or_(
                        ThreadRecord.read_model_json.is_(None),
                        ThreadRecord.read_model_digest.is_distinct_from(ThreadRecord.continuation_digest),
                        ThreadRecord.read_model_schema_version.is_distinct_from(
                            ThreadRecord.continuation_schema_version
                        ),
                    ),
                )
                .order_by(ThreadRecord.thread_id)
                .limit(limit)
            )
            return tuple(
                (record.thread_id, ref) for record in records if (ref := _continuation_ref(record)) is not None
            )

    async def repair_read_model(self, thread_id: str, reference: ObjectRef, value: ThreadReadModel) -> bool:
        """Select derived data only if the head still matches; never change recency."""
        serialized = value.model_dump_json()
        async with transaction(self._sessions) as session:
            result = await session.execute(
                update(ThreadRecord)
                .where(
                    ThreadRecord.thread_id == thread_id,
                    ThreadRecord.continuation_digest == reference.logical_digest,
                    ThreadRecord.continuation_schema_version == reference.object_schema_version,
                )
                .values(
                    read_model_digest=reference.logical_digest,
                    read_model_schema_version=reference.object_schema_version,
                    read_model_json=serialized,
                )
                .returning(ThreadRecord.thread_id)
            )
            return result.scalar_one_or_none() is not None

    async def project_recency(self, *, include_archived: bool = False) -> dict[str, datetime]:
        async with short_session(self._sessions) as session:
            rows = await session.execute(
                select(
                    ThreadConfigurationRecord.project_id,
                    func.max(ThreadRecord.updated_at),
                )
                .join(ThreadRecord, ThreadRecord.thread_id == ThreadConfigurationRecord.thread_id)
                .where(true() if include_archived else ThreadRecord.archived.is_(False))
                .group_by(ThreadConfigurationRecord.project_id)
            )
            return {
                project_id: updated_at
                for project_id, updated_at in rows
                if project_id is not None and updated_at is not None
            }

    async def select_continuation(
        self,
        *,
        thread_id: str,
        expected: ObjectRef | None,
        replacement: ObjectRef,
        completed_run_id: str | None = None,
        read_model: ThreadReadModel,
        excerpt: ConversationExcerpt | None = None,
        activity_changed: bool = True,
        updated_at: datetime | None = None,
    ) -> Thread:
        _require_kind(replacement, ObjectKind.continuation)
        serialized = read_model.model_dump_json()
        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            record = await session.get(ThreadRecord, thread_id)
            configuration = await session.get(ThreadConfigurationRecord, thread_id)
            if record is None or configuration is None:
                raise StoreIntegrityError("Thread does not exist.", code="thread_missing")
            actual = _continuation_ref(record)
            if actual != expected:
                _conflict(
                    "thread_continuation_conflict",
                    None if expected is None else expected.logical_digest,
                    None if actual is None else actual.logical_digest,
                )
            record.continuation_schema_version = replacement.object_schema_version
            record.continuation_digest = replacement.logical_digest
            record.read_model_digest = replacement.logical_digest
            record.read_model_schema_version = replacement.object_schema_version
            record.read_model_json = serialized
            if completed_run_id is not None:
                if record.parent_thread_id is not None:
                    raise ValueError("Only root Threads have completion markers")
                if record.completion_run_id != completed_run_id:
                    record.completion_version += 1
                    record.completion_run_id = completed_run_id
                    record.completion_digest = replacement.logical_digest
                    record.completed_at = now
            if excerpt is not None:
                record.first_input = excerpt.first_input
                record.latest_input = excerpt.latest_input
                record.latest_reply = excerpt.latest_reply
                record.reply_kind = excerpt.reply_kind
            record.search_text = _search_text(record)
            if activity_changed:
                record.activity_at = now
            record.updated_at = now
            await session.flush()
            return _thread_value(record, _configuration_value(configuration))


class ChildExecutionRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        execution_id: str,
        parent_thread_id: str,
        child_thread_id: str,
        child_run_id: str,
        run_composition: ObjectRef,
        created_at: datetime | None = None,
    ) -> ChildExecutionHead:
        _require_kind(run_composition, ObjectKind.run_composition)
        now = _utc(created_at)
        async with transaction(self._sessions) as session:
            child = await session.get(ThreadRecord, child_thread_id)
            if child is None or child.parent_thread_id != parent_thread_id:
                raise StoreIntegrityError("Child Thread relationship is invalid.", code="child_parent_scope_mismatch")
            record = ChildExecutionRecord(
                execution_id=execution_id,
                parent_thread_id=parent_thread_id,
                child_thread_id=child_thread_id,
                child_run_id=child_run_id,
                segment_index=0,
                run_composition_schema_version=run_composition.object_schema_version,
                run_composition_digest=run_composition.logical_digest,
                status="running",
                selected_checkpoint_schema_version=None,
                selected_checkpoint_digest=None,
                resumed_from=None,
                failure_json=None,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            session.add(record)
            await session.flush()
            return _child_value(record)

    async def resume(
        self,
        *,
        previous_execution_id: str,
        execution_id: str,
        child_run_id: str,
        run_composition: ObjectRef,
        created_at: datetime | None = None,
    ) -> ChildExecutionHead:
        _require_kind(run_composition, ObjectKind.run_composition)
        now = _utc(created_at)
        async with transaction(self._sessions) as session:
            previous = await session.get(ChildExecutionRecord, previous_execution_id)
            if previous is None:
                raise StoreIntegrityError("Previous child execution does not exist.", code="child_execution_missing")
            if previous.status != "succeeded" or previous.selected_checkpoint_digest is None:
                raise StoreIntegrityError(
                    "Previous child execution is not resumable.", code="child_execution_not_resumable"
                )
            record = ChildExecutionRecord(
                execution_id=execution_id,
                parent_thread_id=previous.parent_thread_id,
                child_thread_id=previous.child_thread_id,
                child_run_id=child_run_id,
                segment_index=previous.segment_index + 1,
                run_composition_schema_version=run_composition.object_schema_version,
                run_composition_digest=run_composition.logical_digest,
                status="running",
                selected_checkpoint_schema_version=None,
                selected_checkpoint_digest=None,
                resumed_from=previous.execution_id,
                failure_json=None,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            session.add(record)
            await session.flush()
            return _child_value(record)

    async def get(self, execution_id: str) -> ChildExecutionHead | None:
        async with short_session(self._sessions) as session:
            record = await session.get(ChildExecutionRecord, execution_id)
            return None if record is None else _child_value(record)

    async def list_for_parent(
        self,
        parent_thread_id: str,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[tuple[ChildExecutionHead, ...], int]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("child execution page is outside supported bounds")
        predicate = ChildExecutionRecord.parent_thread_id == parent_thread_id
        async with short_session(self._sessions) as session:
            rows = (
                await session.execute(
                    select(ChildExecutionRecord)
                    .where(predicate)
                    .order_by(ChildExecutionRecord.created_at, ChildExecutionRecord.execution_id)
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars()
            total = int(
                (
                    await session.execute(select(func.count()).select_from(ChildExecutionRecord).where(predicate))
                ).scalar_one()
            )
            return tuple(_child_value(row) for row in rows), total

    async def page_for_parent(
        self,
        parent_thread_id: str,
        *,
        after: tuple[datetime, str] | None = None,
        limit: int = 20,
    ) -> tuple[tuple[ChildExecutionHead, ...], int]:
        if not 1 <= limit <= 101:
            raise ValueError("child execution page is outside supported bounds")
        predicate = ChildExecutionRecord.parent_thread_id == parent_thread_id
        statement = select(ChildExecutionRecord).where(predicate)
        if after is not None:
            after_created_at, after_execution_id = after
            if not after_execution_id or after_created_at.tzinfo is None or after_created_at.utcoffset() is None:
                raise ValueError("child execution cursor is invalid")
            statement = statement.where(
                or_(
                    ChildExecutionRecord.created_at > after_created_at,
                    and_(
                        ChildExecutionRecord.created_at == after_created_at,
                        ChildExecutionRecord.execution_id > after_execution_id,
                    ),
                )
            )
        async with short_session(self._sessions) as session:
            rows = tuple(
                (
                    await session.execute(
                        statement.order_by(
                            ChildExecutionRecord.created_at,
                            ChildExecutionRecord.execution_id,
                        ).limit(limit)
                    )
                ).scalars()
            )
            total = int(
                (
                    await session.execute(select(func.count()).select_from(ChildExecutionRecord).where(predicate))
                ).scalar_one()
            )
            return tuple(_child_value(row) for row in rows), total

    async def status_counts_for_roots(
        self,
        root_thread_ids: tuple[str, ...],
    ) -> tuple[dict[str, dict[ExecutionStatus, int]], dict[str, tuple[str, ...]]]:
        if not root_thread_ids:
            return {}, {}
        roots = (
            select(
                ThreadRecord.thread_id.label("thread_id"),
                ThreadRecord.thread_id.label("root_thread_id"),
            )
            .where(
                ThreadRecord.thread_id.in_(root_thread_ids),
                ThreadRecord.parent_thread_id.is_(None),
            )
            .cte("thread_lineage", recursive=True)
        )
        descendants = select(
            ThreadRecord.thread_id,
            roots.c.root_thread_id,
        ).join(roots, ThreadRecord.parent_thread_id == roots.c.thread_id)
        lineage = roots.union_all(descendants)
        async with short_session(self._sessions) as session:
            count_rows = await session.execute(
                select(
                    lineage.c.root_thread_id,
                    ChildExecutionRecord.status,
                    func.count(),
                )
                .select_from(lineage)
                .join(
                    ChildExecutionRecord,
                    ChildExecutionRecord.parent_thread_id == lineage.c.thread_id,
                )
                .group_by(lineage.c.root_thread_id, ChildExecutionRecord.status)
            )
            running_rows = await session.execute(
                select(lineage.c.root_thread_id, ChildExecutionRecord.execution_id)
                .select_from(lineage)
                .join(
                    ChildExecutionRecord,
                    ChildExecutionRecord.parent_thread_id == lineage.c.thread_id,
                )
                .where(ChildExecutionRecord.status == "running")
                .order_by(lineage.c.root_thread_id, ChildExecutionRecord.execution_id)
            )
        counts: dict[str, dict[ExecutionStatus, int]] = {thread_id: {} for thread_id in root_thread_ids}
        for root_thread_id, status, count in count_rows:
            counts[root_thread_id][cast(ExecutionStatus, status)] = int(count)
        running: dict[str, list[str]] = {thread_id: [] for thread_id in root_thread_ids}
        for root_thread_id, execution_id in running_rows:
            running[root_thread_id].append(execution_id)
        return counts, {key: tuple(value) for key, value in running.items()}

    async def first_for_child(self, child_thread_id: str) -> ChildExecutionHead | None:
        async with short_session(self._sessions) as session:
            record = (
                await session.execute(
                    select(ChildExecutionRecord).where(
                        ChildExecutionRecord.child_thread_id == child_thread_id,
                        ChildExecutionRecord.segment_index == 0,
                    )
                )
            ).scalar_one_or_none()
            return None if record is None else _child_value(record)

    async def select_checkpoint(
        self,
        *,
        execution_id: str,
        expected: ObjectRef | None,
        checkpoint: ObjectRef,
        child_run_id: str,
        updated_at: datetime | None = None,
    ) -> ChildExecutionHead:
        _require_kind(checkpoint, ObjectKind.child_checkpoint)
        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            record = await session.get(ChildExecutionRecord, execution_id)
            if record is None:
                raise StoreIntegrityError("Child execution does not exist.", code="child_execution_missing")
            if record.status != "running":
                raise StoreConflictError("Child execution is already terminal.", code="child_execution_terminal")
            actual = _child_checkpoint_ref(record)
            if actual != expected:
                _conflict("child_checkpoint_conflict", _digest(expected), _digest(actual))
            record.child_run_id = child_run_id
            record.selected_checkpoint_schema_version = checkpoint.object_schema_version
            record.selected_checkpoint_digest = checkpoint.logical_digest
            record.updated_at = now
            await session.flush()
            return _child_value(record)

    async def finish(
        self,
        *,
        execution_id: str,
        status: ExecutionStatus,
        expected_checkpoint: ObjectRef | None,
        checkpoint: ObjectRef | None,
        child_run_id: str | None = None,
        failure: SafeFailure | None = None,
        completed_at: datetime | None = None,
    ) -> ChildExecutionHead:
        if status not in {"succeeded", "failed", "cancelled", "lost"}:
            raise ValueError("child terminal status is invalid")
        if status == "succeeded" and checkpoint is None:
            raise ValueError("successful child execution requires a checkpoint")
        if checkpoint is not None:
            _require_kind(checkpoint, ObjectKind.child_checkpoint)
        now = _utc(completed_at)
        async with transaction(self._sessions) as session:
            record = await session.get(ChildExecutionRecord, execution_id)
            if record is None:
                raise StoreIntegrityError("Child execution does not exist.", code="child_execution_missing")
            if record.status != "running":
                raise StoreConflictError("Child execution is already terminal.", code="child_execution_terminal")
            actual = _child_checkpoint_ref(record)
            if actual != expected_checkpoint:
                _conflict("child_checkpoint_conflict", _digest(expected_checkpoint), _digest(actual))
            record.status = status
            if child_run_id is not None:
                record.child_run_id = child_run_id
            if checkpoint is not None:
                record.selected_checkpoint_schema_version = checkpoint.object_schema_version
                record.selected_checkpoint_digest = checkpoint.logical_digest
            record.failure_json = (
                None if failure is None else json.dumps(failure.model_dump(mode="json"), separators=(",", ":"))
            )
            record.updated_at = now
            record.completed_at = now
            await session.flush()
            return _child_value(record)


class EnvironmentStateRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def get(self, key: EnvironmentBindingKey) -> EnvironmentBindingHead | None:
        async with short_session(self._sessions) as session:
            record = await session.get(EnvironmentBindingRecord, _binding_pk(key))
            return None if record is None else _binding_value(record)

    async def select(
        self,
        *,
        key: EnvironmentBindingKey,
        expected: ObjectRef | None,
        replacement: ObjectRef | None,
        updated_at: datetime | None = None,
    ) -> EnvironmentBindingHead:
        if replacement is not None:
            _require_kind(replacement, ObjectKind.environment_state)
        now = _utc(updated_at)
        async with transaction(self._sessions) as session:
            record = await session.get(EnvironmentBindingRecord, _binding_pk(key))
            actual = None if record is None else _state_ref(record)
            if actual != expected:
                _conflict("environment_state_conflict", _digest(expected), _digest(actual))
            if record is None:
                record = EnvironmentBindingRecord(
                    **_binding_pk(key),
                    state_schema_version=None if replacement is None else replacement.object_schema_version,
                    state_digest=None if replacement is None else replacement.logical_digest,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.state_schema_version = None if replacement is None else replacement.object_schema_version
                record.state_digest = None if replacement is None else replacement.logical_digest
                record.updated_at = now
            await session.flush()
            return _binding_value(record)


def _configuration_record(thread_id: str, value: ThreadConfiguration) -> ThreadConfigurationRecord:
    return ThreadConfigurationRecord(
        thread_id=thread_id,
        version=value.version,
        project_id=value.project_id,
        agent_source_kind=value.agent_source.kind,
        agent_source_id=value.agent_source.id,
        environment_profile_id=value.environment_profile_id,
        harness_plugin_ids_json=_json_list(value.harness_plugin_ids),
        environment_run_extension_ids_json=_json_list(value.environment_run_extension_ids),
        mcp_server_ids_json=_json_list(value.mcp_server_ids),
    )


def _assign_configuration(record: ThreadConfigurationRecord, value: ThreadConfiguration) -> None:
    record.version = value.version
    record.project_id = value.project_id
    record.agent_source_kind = value.agent_source.kind
    record.agent_source_id = value.agent_source.id
    record.environment_profile_id = value.environment_profile_id
    record.harness_plugin_ids_json = _json_list(value.harness_plugin_ids)
    record.environment_run_extension_ids_json = _json_list(value.environment_run_extension_ids)
    record.mcp_server_ids_json = _json_list(value.mcp_server_ids)


def _configuration_value(record: ThreadConfigurationRecord) -> ThreadConfiguration:
    source = (
        AgentResourceSource(id=record.agent_source_id)
        if record.agent_source_kind == "agent"
        else MarkdownSubagentSource(id=record.agent_source_id)
    )
    return ThreadConfiguration(
        version=record.version,
        project_id=record.project_id,
        agent_source=source,
        environment_profile_id=record.environment_profile_id,
        harness_plugin_ids=_parse_list(record.harness_plugin_ids_json),
        environment_run_extension_ids=_parse_list(record.environment_run_extension_ids_json),
        mcp_server_ids=_parse_list(record.mcp_server_ids_json),
    )


def _search_text(record: ThreadRecord) -> str:
    return "\n".join(
        (record.thread_id, record.title or "", record.first_input, record.latest_input, record.latest_reply)
    ).casefold()


def _thread_value(record: ThreadRecord, configuration: ThreadConfiguration) -> Thread:
    completion = None
    if record.completion_version:
        assert record.completion_run_id is not None
        assert record.completion_digest is not None
        assert record.completed_at is not None
        completion = ThreadCompletion(
            version=record.completion_version,
            run_id=record.completion_run_id,
            continuation_id=record.completion_digest,
            completed_at=record.completed_at,
        )
    return Thread(
        read_model=_read_model(record),
        completion=completion,
        thread_id=record.thread_id,
        parent_thread_id=record.parent_thread_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        metadata_version=record.metadata_version,
        title=record.title,
        excerpt=ConversationExcerpt.model_validate(
            {
                "first_input": record.first_input,
                "latest_input": record.latest_input,
                "latest_reply": record.latest_reply,
                "reply_kind": record.reply_kind,
            }
        ),
        activity_at=record.activity_at,
        touched_at=record.touched_at,
        archived=record.archived,
        configuration=configuration,
        initial_state=ObjectRef(
            object_kind=ObjectKind.thread_initial_state,
            object_schema_version=record.initial_state_schema_version,
            logical_digest=record.initial_state_digest,
        ),
        continuation=_continuation_ref(record),
    )


def _read_model(record: ThreadRecord) -> ThreadReadModel | None:
    if (
        record.read_model_json is None
        or record.read_model_digest != record.continuation_digest
        or record.read_model_schema_version != record.continuation_schema_version
    ):
        return None
    return ThreadReadModel.model_validate_json(record.read_model_json)


def _continuation_ref(record: ThreadRecord) -> ObjectRef | None:
    if record.continuation_digest is None or record.continuation_schema_version is None:
        return None
    return ObjectRef(
        object_kind=ObjectKind.continuation,
        object_schema_version=record.continuation_schema_version,
        logical_digest=record.continuation_digest,
    )


def _configuration_reference(record: AcceptedConfigurationRecord) -> ObjectRef:
    return ObjectRef(
        object_kind=ObjectKind.configuration_generation,
        object_schema_version=record.object_schema_version,
        logical_digest=record.object_digest,
    )


def _child_checkpoint_ref(record: ChildExecutionRecord) -> ObjectRef | None:
    if record.selected_checkpoint_digest is None or record.selected_checkpoint_schema_version is None:
        return None
    return ObjectRef(
        object_kind=ObjectKind.child_checkpoint,
        object_schema_version=record.selected_checkpoint_schema_version,
        logical_digest=record.selected_checkpoint_digest,
    )


def _child_value(record: ChildExecutionRecord) -> ChildExecutionHead:
    return ChildExecutionHead(
        execution_id=record.execution_id,
        parent_thread_id=record.parent_thread_id,
        child_thread_id=record.child_thread_id,
        child_run_id=record.child_run_id,
        segment_index=record.segment_index,
        run_composition=ObjectRef(
            object_kind=ObjectKind.run_composition,
            object_schema_version=record.run_composition_schema_version,
            logical_digest=record.run_composition_digest,
        ),
        status=cast(ExecutionStatus, record.status),
        selected_checkpoint=(
            None
            if record.selected_checkpoint_digest is None or record.selected_checkpoint_schema_version is None
            else ObjectRef(
                object_kind=ObjectKind.child_checkpoint,
                object_schema_version=record.selected_checkpoint_schema_version,
                logical_digest=record.selected_checkpoint_digest,
            )
        ),
        resumed_from=record.resumed_from,
        failure=(None if record.failure_json is None else SafeFailure.model_validate(json.loads(record.failure_json))),
        created_at=record.created_at,
        updated_at=record.updated_at,
        completed_at=record.completed_at,
    )


def _binding_pk(key: EnvironmentBindingKey) -> dict[str, str]:
    return {
        "thread_id": key.thread_id,
        "environment_profile_id": key.environment_profile_id,
        "profile_digest": key.profile_digest,
        "adapter_key": key.adapter_key,
        "normalized_root": key.normalized_root,
    }


def _binding_value(record: EnvironmentBindingRecord) -> EnvironmentBindingHead:
    key = EnvironmentBindingKey(
        thread_id=record.thread_id,
        environment_profile_id=record.environment_profile_id,
        profile_digest=record.profile_digest,
        adapter_key=record.adapter_key,
        normalized_root=record.normalized_root,
    )
    return EnvironmentBindingHead(key=key, state=_state_ref(record), updated_at=record.updated_at)


def _state_ref(record: EnvironmentBindingRecord) -> ObjectRef | None:
    if record.state_digest is None or record.state_schema_version is None:
        return None
    return ObjectRef(
        object_kind=ObjectKind.environment_state,
        object_schema_version=record.state_schema_version,
        logical_digest=record.state_digest,
    )


def _require_kind(reference: ObjectRef, kind: ObjectKind) -> None:
    if reference.object_kind is not kind:
        raise ValueError(f"object reference must have kind {kind.value}")


def _json_list(values: tuple[str, ...]) -> str:
    return json.dumps(values, separators=(",", ":"))


def _parse_list(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise StoreIntegrityError("Stored resource list is invalid.", code="stored_configuration_invalid")
    return tuple(parsed)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("persistence timestamps must include a UTC offset")
    return value.astimezone(UTC)


def _digest(value: ObjectRef | None) -> str | None:
    return None if value is None else value.logical_digest


def _conflict(code: str, expected: object, actual: object) -> None:
    raise StoreConflictError(
        "A mutable Harness UI head changed concurrently.",
        code=code,
        details={"expected": expected, "actual": actual},
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


__all__ = [
    "ChildExecutionRepository",
    "ConfigurationRepository",
    "EnvironmentStateRepository",
    "ThreadRepository",
]
