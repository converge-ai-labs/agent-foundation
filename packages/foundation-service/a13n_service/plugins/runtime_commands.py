"""Durable runner Plugin Runtime command coordination."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from a13n_harness import SafeFailure
from anyio import Event, create_task_group, move_on_after, sleep
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.idempotency import IdempotencyConflict, is_evidence_unique_race
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    PrincipalRef,
    PrincipalType,
    authorize_workspace,
)
from a13n_service.iam.audit import SystemAuditActor, security_audit_record
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .commands import (
    PluginRuntimeCandidateResolver,
    PluginRuntimeCatalogSnapshot,
    PluginRuntimeCommand,
    PluginRuntimeCommandFailure,
    PluginRuntimeStagingAuthority,
    PluginRuntimeVersionSpec,
)
from .domain import Plugin, PluginTaskReceipt, PluginTaskStatus, PluginVersion
from .errors import PluginError, plugin_not_found, plugin_state_conflict, plugin_version_not_found
from .idempotency import (
    load_plugin_evidence,
    new_plugin_evidence,
    plugin_key_digest,
    plugin_request_digest,
)
from .models import (
    PluginRecord,
    PluginRuntimeStateRecord,
    PluginRuntimeTaskRecord,
    PluginVersionRecord,
)
from .runtime import PluginRuntimeLock, PluginRuntimeLockError

logger = logging.getLogger("a13n_service.plugins.runtime_commands")


@dataclass(frozen=True, slots=True)
class _TaskClaim:
    operation_id: str
    generation: int


class _RuntimeTaskLeaseLost(Exception):
    pass


class PluginRuntimeCommandCoordinator:
    """Persist commands and reconcile them through one fenced Runtime executor."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        candidate_resolver: PluginRuntimeCandidateResolver,
        staging_authority: PluginRuntimeStagingAuthority,
        *,
        poll_interval_seconds: float = 1,
        lease_seconds: float = 300,
        clock: Clock | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if lease_seconds <= 3:
            raise ValueError("lease_seconds must exceed three seconds")
        self._sessions = sessions
        self._candidate_resolver = candidate_resolver
        self._staging_authority = staging_authority
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._clock = clock or utc_now

    async def activate(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        plugin: Plugin,
        plugin_version: PluginVersion,
        idempotency_key: str,
    ) -> PluginTaskReceipt:
        return await self._accept(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            command="activate",
            plugin_id=plugin.id,
            plugin_version_id=plugin_version.id,
            idempotency_key=idempotency_key,
        )

    async def deactivate(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        plugin: Plugin,
        idempotency_key: str,
    ) -> PluginTaskReceipt:
        return await self._accept(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            command="deactivate",
            plugin_id=plugin.id,
            plugin_version_id=None,
            idempotency_key=idempotency_key,
        )

    async def get_receipt(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        operation_id: str,
    ) -> PluginTaskReceipt:
        async with transaction(self._sessions) as session:
            workspace = await self._authorize(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
            task = await session.scalar(
                select(PluginRuntimeTaskRecord).where(
                    PluginRuntimeTaskRecord.id == operation_id,
                    PluginRuntimeTaskRecord.organization_id == workspace.organization_id,
                    PluginRuntimeTaskRecord.workspace_id == workspace.workspace_id,
                    PluginRuntimeTaskRecord.actor_type == actor.principal.principal_type.value,
                    PluginRuntimeTaskRecord.actor_id == actor.principal.principal_id,
                )
            )
            if task is None:
                raise PluginError(
                    "plugin_operation_not_found",
                    "The Plugin operation was not found.",
                    status_code=404,
                )
            return _receipt(task)

    async def run(self) -> None:
        """Reconcile durable tasks until the owning process lifespan ends."""

        while True:
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("plugin_runtime_command_reconcile_failed")
            await sleep(self._poll_interval_seconds)

    async def reconcile_once(self) -> bool:
        claim = await self._claim()
        if claim is None:
            return False
        try:
            await self._process(claim)
        except PluginRuntimeCommandFailure as error:
            task = await self._load_claimed_task(claim)
            if task is None:
                return True
            if error.retryable or task.phase == "committed":
                await self._release(claim)
                return True
            try:
                await self._abort_candidate(claim, task)
            except Exception:
                await self._release(claim)
                raise
            await self._fail(claim, error.failure)
        except (PluginRuntimeLockError, ValueError):
            task = await self._load_claimed_task(claim)
            if task is None:
                return True
            failure = SafeFailure(
                code="plugin_runtime_incompatible",
                message="The candidate Plugin Runtime is invalid.",
            )
            if task.phase in {"staged", "committed"}:
                await self._release(claim)
                logger.warning(
                    "plugin_runtime_staged_candidate_unavailable",
                    extra={
                        "event": "plugin_runtime_staged_candidate_unavailable",
                        "operation_id": claim.operation_id,
                        "phase": task.phase,
                    },
                )
                return True
            await self._fail(claim, failure)
            logger.info(
                "plugin_runtime_candidate_rejected",
                extra={"event": "plugin_runtime_candidate_rejected", "operation_id": claim.operation_id},
            )
        except Exception:
            await self._release(claim)
            raise
        return True

    async def _accept(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        command: PluginRuntimeCommand,
        plugin_id: str,
        plugin_version_id: str | None,
        idempotency_key: str,
    ) -> PluginTaskReceipt:
        key_digest = plugin_key_digest(idempotency_key)
        request_digest = plugin_request_digest(
            {"command": command, "plugin_id": plugin_id, "plugin_version_id": plugin_version_id}
        )
        operation = f"plugin_runtime.{command}"
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await self._authorize(
                    session,
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                )
                replay = await _load_replay(
                    session,
                    actor=actor,
                    operation=operation,
                    scope_id=plugin_id,
                    key_digest=key_digest,
                    request_digest=request_digest,
                    now=now,
                )
                if replay is not None:
                    task = await session.get(PluginRuntimeTaskRecord, replay.result_ref)
                    if task is None:
                        raise _idempotency_conflict()
                    return _receipt(task)
                state = await session.get(PluginRuntimeStateRecord, "runtime", with_for_update=True)
                if state is None or state.mode != "runner":
                    raise PluginError(
                        "plugin_runtime_mode_unsupported",
                        "Plugin Runtime commands are unavailable in the configured mode.",
                        status_code=409,
                    )
                plugin = await session.get(PluginRecord, plugin_id)
                if plugin is None:
                    raise plugin_not_found()
                if plugin.archived_at is not None:
                    raise plugin_state_conflict()
                if command == "activate":
                    version = await session.scalar(
                        select(PluginVersionRecord).where(
                            PluginVersionRecord.id == plugin_version_id,
                            PluginVersionRecord.plugin_id == plugin_id,
                        )
                    )
                    if version is None:
                        raise plugin_version_not_found()
                elif plugin.required:
                    raise plugin_state_conflict()
                operation_id = new_object_id("op")
                task = PluginRuntimeTaskRecord(
                    id=operation_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    actor_type=actor.principal.principal_type.value,
                    actor_id=actor.principal.principal_id,
                    command=command,
                    plugin_id=plugin_id,
                    plugin_version_id=plugin_version_id,
                    status=PluginTaskStatus.running.value,
                    phase="accepted",
                    expected_runtime_generation=None,
                    candidate_lock_digest=None,
                    staging_token=None,
                    committed_runtime_generation=None,
                    result_refs=[],
                    error=None,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
                session.add(task)
                session.add(
                    new_plugin_evidence(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        operation=operation,
                        scope_id=plugin_id,
                        key_digest=key_digest,
                        request_digest=request_digest,
                        result_kind="plugin_task",
                        result_ref=operation_id,
                        now=now,
                    )
                )
                session.add(
                    security_audit_record(
                        audit_id=new_object_id("audit"),
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        action=f"plugin_runtime.{command}.accepted",
                        resource_type="plugin",
                        resource_id=plugin_id,
                        outcome="success",
                        occurred_at=now,
                        details={"operation_id": operation_id},
                    )
                )
                await session.flush()
                return _receipt(task)
        except AuthorizationError as error:
            raise PluginError("forbidden", "The operation is not allowed.", status_code=403) from error
        except IntegrityError as error:
            if not is_evidence_unique_race(error):
                raise
            replay = await self._replay_after_race(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                operation=operation,
                scope_id=plugin_id,
                key_digest=key_digest,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            raise

    async def _replay_after_race(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        operation: str,
        scope_id: str,
        key_digest: str,
        request_digest: str,
    ) -> PluginTaskReceipt | None:
        async with transaction(self._sessions) as session:
            await self._authorize(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
            replay = await _load_replay(
                session,
                actor=actor,
                operation=operation,
                scope_id=scope_id,
                key_digest=key_digest,
                request_digest=request_digest,
                now=self._clock(),
            )
            if replay is None:
                return None
            task = await session.get(PluginRuntimeTaskRecord, replay.result_ref)
            return _receipt(task) if task is not None else None

    async def _claim(self) -> _TaskClaim | None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            state = await session.get(PluginRuntimeStateRecord, "runtime", with_for_update=True)
            if state is None or state.mode != "runner":
                return None
            task: PluginRuntimeTaskRecord | None = None
            if state.command_operation_id is not None:
                task = await session.get(PluginRuntimeTaskRecord, state.command_operation_id, with_for_update=True)
                if task is not None and task.status == PluginTaskStatus.running.value:
                    if state.command_lease_expires_at is not None and assume_utc(state.command_lease_expires_at) > now:
                        return None
                else:
                    state.command_operation_id = None
                    state.command_lease_expires_at = None
                    task = None
            if task is None:
                task = await session.scalar(
                    select(PluginRuntimeTaskRecord)
                    .where(PluginRuntimeTaskRecord.status == PluginTaskStatus.running.value)
                    .order_by(PluginRuntimeTaskRecord.created_at, PluginRuntimeTaskRecord.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if task is None:
                    return None
                state.command_operation_id = task.id
            state.command_claim_generation += 1
            state.command_lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            state.updated_at = now
            await session.flush()
            return _TaskClaim(operation_id=task.id, generation=state.command_claim_generation)

    async def _process(self, claim: _TaskClaim) -> None:
        while True:
            task = await self._load_claimed_task(claim)
            if task is None or task.status != PluginTaskStatus.running.value:
                return
            if task.phase == "accepted":
                operation_id = task.id
                command = cast(PluginRuntimeCommand, task.command)
                catalog = await self._catalog_snapshot(claim, task)
                candidate = await self._with_lease(
                    claim,
                    lambda operation_id=operation_id, command=command, catalog=catalog: (
                        self._candidate_resolver.resolve_candidate(
                            operation_id=operation_id,
                            command=command,
                            catalog=catalog,
                        )
                    ),
                )
                await self._record_candidate(claim, task, catalog, candidate)
                if candidate.digest == catalog.active_lock_digest:
                    await self._succeed(claim, committed_runtime_generation=catalog.runtime_generation)
                    return
                continue
            candidate = await self._candidate_resolver.require_candidate(
                runtime_lock_digest=cast(str, task.candidate_lock_digest)
            )
            if task.phase == "candidate_ready":
                operation_id = task.id
                staging_token = await self._with_lease(
                    claim,
                    lambda operation_id=operation_id, candidate=candidate: self._staging_authority.stage_candidate(
                        operation_id=operation_id,
                        runtime_lock=candidate,
                    ),
                )
                if not staging_token or len(staging_token) > 512:
                    raise PluginRuntimeCommandFailure(
                        SafeFailure(
                            code="plugin_runtime_staging_invalid",
                            message="Worker staging returned invalid evidence.",
                        )
                    )
                await self._record_staged(claim, staging_token)
                continue
            if task.phase == "staged":
                await self._commit_catalog(claim, task, candidate)
                continue
            if task.phase == "committed":
                operation_id = task.id
                staging_token = cast(str, task.staging_token)
                runtime_generation = cast(int, task.committed_runtime_generation)
                await self._with_lease(
                    claim,
                    lambda operation_id=operation_id, candidate=candidate, staging_token=staging_token, runtime_generation=runtime_generation: (
                        self._staging_authority.activate_candidate(
                            operation_id=operation_id,
                            runtime_lock=candidate,
                            staging_token=staging_token,
                            runtime_generation=runtime_generation,
                        )
                    ),
                )
                await self._succeed(claim)
                return
            return

    async def _catalog_snapshot(
        self,
        claim: _TaskClaim,
        task: PluginRuntimeTaskRecord,
    ) -> PluginRuntimeCatalogSnapshot:
        async with transaction(self._sessions) as session:
            state, current = await self._locked_claim(session, claim)
            actor = _task_actor(current)
            try:
                await self._authorize(
                    session,
                    actor=actor,
                    organization_id=current.organization_id,
                    workspace_id=current.workspace_id,
                )
            except AuthorizationError as error:
                raise PluginRuntimeCommandFailure(
                    SafeFailure(code="forbidden", message="Plugin Runtime authority is no longer available.")
                ) from error
            target_plugin = await session.get(PluginRecord, current.plugin_id)
            if target_plugin is None:
                raise PluginRuntimeCommandFailure(
                    SafeFailure(code="plugin_not_found", message="The Plugin was not found.")
                )
            if target_plugin.archived_at is not None:
                raise PluginRuntimeCommandFailure(
                    SafeFailure(code="plugin_state_conflict", message="The Plugin is not available.")
                )
            target_version = None
            if current.plugin_version_id is not None:
                version = await session.scalar(
                    select(PluginVersionRecord).where(
                        PluginVersionRecord.id == current.plugin_version_id,
                        PluginVersionRecord.plugin_id == current.plugin_id,
                    )
                )
                if version is None:
                    raise PluginRuntimeCommandFailure(
                        SafeFailure(code="plugin_version_not_found", message="The PluginVersion was not found.")
                    )
                target_version = _version_spec(target_plugin, version)
            active_rows = tuple(
                (
                    await session.execute(
                        select(PluginRecord, PluginVersionRecord)
                        .join(PluginVersionRecord, PluginVersionRecord.id == PluginRecord.active_version_id)
                        .where(PluginRecord.active_version_id.is_not(None))
                    )
                ).all()
            )
            return PluginRuntimeCatalogSnapshot(
                runtime_generation=state.runtime_generation,
                active_lock_digest=state.active_lock_digest,
                active_versions=tuple(_version_spec(plugin, version) for plugin, version in active_rows),
                target_plugin=target_plugin.to_resource(),
                target_version=target_version,
            )

    async def _record_candidate(
        self,
        claim: _TaskClaim,
        task: PluginRuntimeTaskRecord,
        catalog: PluginRuntimeCatalogSnapshot,
        candidate: PluginRuntimeLock,
    ) -> None:
        _validate_candidate(task.command, catalog, candidate)
        async with transaction(self._sessions) as session:
            state, current = await self._locked_claim(session, claim)
            if (
                state.runtime_generation != catalog.runtime_generation
                or state.active_lock_digest != catalog.active_lock_digest
            ):
                raise PluginRuntimeCommandFailure(
                    SafeFailure(code="plugin_runtime_changed", message="The active Plugin Runtime changed."),
                    retryable=True,
                )
            current.expected_runtime_generation = catalog.runtime_generation
            current.candidate_lock_digest = candidate.digest
            current.phase = "candidate_ready"
            current.updated_at = self._clock()

    async def _record_staged(self, claim: _TaskClaim, staging_token: str) -> None:
        async with transaction(self._sessions) as session:
            _state, task = await self._locked_claim(session, claim)
            task.staging_token = staging_token
            task.phase = "staged"
            task.updated_at = self._clock()

    async def _commit_catalog(
        self,
        claim: _TaskClaim,
        task: PluginRuntimeTaskRecord,
        candidate: PluginRuntimeLock,
    ) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            state, current = await self._locked_claim(session, claim)
            if state.runtime_generation != current.expected_runtime_generation:
                raise PluginRuntimeCommandFailure(
                    SafeFailure(code="plugin_runtime_changed", message="The active Plugin Runtime changed."),
                    retryable=True,
                )
            target = await session.get(PluginRecord, current.plugin_id, with_for_update=True)
            if target is None or target.archived_at is not None:
                raise PluginRuntimeCommandFailure(
                    SafeFailure(code="plugin_state_conflict", message="The Plugin is not available.")
                )
            if current.command == "deactivate" and target.required:
                raise PluginRuntimeCommandFailure(
                    SafeFailure(code="plugin_state_conflict", message="The required Plugin cannot be deactivated.")
                )
            active = tuple(
                (
                    await session.scalars(
                        select(PluginRecord).where(PluginRecord.active_version_id.is_not(None)).with_for_update()
                    )
                ).all()
            )
            desired = {item.plugin_id: item.plugin_version_id for item in candidate.plugins}
            expected = {item.id: cast(str, item.active_version_id) for item in active}
            if current.command == "activate":
                expected[current.plugin_id] = cast(str, current.plugin_version_id)
            else:
                expected.pop(current.plugin_id, None)
            if desired != expected:
                raise PluginRuntimeCommandFailure(
                    SafeFailure(
                        code="plugin_runtime_candidate_mismatch",
                        message="The candidate Runtime does not match the requested Plugin catalog.",
                    )
                )
            records = {item.id: item for item in active}
            records[target.id] = target
            missing_ids = set(desired) - set(records)
            if missing_ids:
                missing = tuple(
                    (
                        await session.scalars(
                            select(PluginRecord).where(PluginRecord.id.in_(missing_ids)).with_for_update()
                        )
                    ).all()
                )
                records.update((item.id, item) for item in missing)
            if set(desired) - set(records):
                raise PluginRuntimeCommandFailure(
                    SafeFailure(code="plugin_not_found", message="A candidate Plugin was not found.")
                )
            for plugin_id, record in records.items():
                record.active_version_id = desired.get(plugin_id)
                record.updated_at = now
            state.active_lock_digest = candidate.digest
            state.runtime_generation += 1
            state.updated_at = now
            current.phase = "committed"
            current.committed_runtime_generation = state.runtime_generation
            current.updated_at = now

    async def _succeed(
        self,
        claim: _TaskClaim,
        *,
        committed_runtime_generation: int | None = None,
    ) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            state, task = await self._locked_claim(session, claim)
            result_refs = [
                _resource_ref(task, resource_type="plugin", resource_id=task.plugin_id),
            ]
            if task.plugin_version_id is not None:
                result_refs.append(
                    _resource_ref(task, resource_type="plugin_version", resource_id=task.plugin_version_id)
                )
            task.status = PluginTaskStatus.succeeded.value
            task.phase = "succeeded"
            task.result_refs = result_refs
            task.error = None
            if committed_runtime_generation is not None:
                task.committed_runtime_generation = committed_runtime_generation
            task.updated_at = now
            task.completed_at = now
            session.add(
                _terminal_audit(
                    task,
                    status="succeeded",
                    outcome="success",
                    now=now,
                    details={"runtime_generation": task.committed_runtime_generation},
                )
            )
            _clear_claim(state, now=now)

    async def _fail(self, claim: _TaskClaim, failure: SafeFailure) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            state, task = await self._locked_claim(session, claim)
            if task.phase == "committed":
                raise PluginRuntimeCommandFailure(failure, retryable=True)
            task.status = PluginTaskStatus.failed.value
            task.phase = "failed"
            task.result_refs = []
            task.error = failure.model_dump(mode="json", by_alias=True)
            task.updated_at = now
            task.completed_at = now
            session.add(
                _terminal_audit(
                    task,
                    status="failed",
                    outcome="failure",
                    now=now,
                    details={"failure_code": failure.code},
                )
            )
            _clear_claim(state, now=now)

    async def _abort_candidate(self, claim: _TaskClaim, task: PluginRuntimeTaskRecord) -> None:
        if task.candidate_lock_digest is None:
            return
        candidate = await self._candidate_resolver.require_candidate(runtime_lock_digest=task.candidate_lock_digest)
        await self._with_lease(
            claim,
            lambda: self._staging_authority.abort_candidate(
                operation_id=task.id,
                runtime_lock=candidate,
                staging_token=task.staging_token,
            ),
        )

    async def _load_claimed_task(self, claim: _TaskClaim) -> PluginRuntimeTaskRecord | None:
        async with transaction(self._sessions) as session:
            state = await session.get(PluginRuntimeStateRecord, "runtime")
            if (
                state is None
                or state.command_operation_id != claim.operation_id
                or state.command_claim_generation != claim.generation
            ):
                return None
            return await session.get(PluginRuntimeTaskRecord, claim.operation_id)

    async def _locked_claim(
        self,
        session: AsyncSession,
        claim: _TaskClaim,
    ) -> tuple[PluginRuntimeStateRecord, PluginRuntimeTaskRecord]:
        state = await session.get(PluginRuntimeStateRecord, "runtime", with_for_update=True)
        if (
            state is None
            or state.command_operation_id != claim.operation_id
            or state.command_claim_generation != claim.generation
            or state.command_lease_expires_at is None
            or assume_utc(state.command_lease_expires_at) <= assume_utc(self._clock())
        ):
            raise _RuntimeTaskLeaseLost
        task = await session.get(PluginRuntimeTaskRecord, claim.operation_id, with_for_update=True)
        if task is None or task.status != PluginTaskStatus.running.value:
            raise _RuntimeTaskLeaseLost
        return state, task

    async def _renew(self, claim: _TaskClaim) -> bool:
        now = self._clock()
        async with transaction(self._sessions) as session:
            state = await session.get(PluginRuntimeStateRecord, "runtime", with_for_update=True)
            if (
                state is None
                or state.command_operation_id != claim.operation_id
                or state.command_claim_generation != claim.generation
            ):
                return False
            task = await session.get(PluginRuntimeTaskRecord, claim.operation_id)
            if task is None or task.status != PluginTaskStatus.running.value:
                return False
            state.command_lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            state.updated_at = now
            return True

    async def _with_lease[T](self, claim: _TaskClaim, operation: Callable[[], Awaitable[T]]) -> T:
        done = Event()
        results: list[T] = []
        errors: list[BaseException] = []
        lease_lost = False

        async def invoke() -> None:
            try:
                results.append(await operation())
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()

        async with create_task_group() as tasks:
            tasks.start_soon(invoke)
            while not done.is_set():
                with move_on_after(self._lease_seconds / 3):
                    await done.wait()
                if done.is_set():
                    break
                if not await self._renew(claim):
                    lease_lost = True
                    tasks.cancel_scope.cancel()
                    break
        if lease_lost:
            raise _RuntimeTaskLeaseLost
        if errors:
            raise errors[0]
        return results[0]

    async def _release(self, claim: _TaskClaim) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            state = await session.get(PluginRuntimeStateRecord, "runtime", with_for_update=True)
            if (
                state is not None
                and state.command_operation_id == claim.operation_id
                and state.command_claim_generation == claim.generation
            ):
                _clear_claim(state, now=now)

    async def _authorize(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
    ):
        workspace = await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.plugin_runtime_manage,
        )
        if workspace.organization_id != organization_id:
            raise AuthorizationError("organization boundary mismatch")
        return workspace


def _version_spec(plugin: PluginRecord, version: PluginVersionRecord) -> PluginRuntimeVersionSpec:
    return PluginRuntimeVersionSpec(
        plugin=plugin.to_resource(),
        version=version.to_resource(),
        requires_python=version.requires_python,
        wheel_tags=tuple(version.wheel_tags),
        root_is_purelib=version.root_is_purelib,
        entry_point_target=version.entry_point_target,
    )


def _validate_candidate(
    command: str,
    catalog: PluginRuntimeCatalogSnapshot,
    candidate: PluginRuntimeLock,
) -> None:
    if candidate.mode != "runner" or candidate.computed_digest() != candidate.digest:
        raise PluginRuntimeCommandFailure(
            SafeFailure(code="plugin_runtime_incompatible", message="The candidate Plugin Runtime is invalid.")
        )
    expected = {item.plugin.id: item.version.id for item in catalog.active_versions}
    if command == "activate":
        if catalog.target_version is None:
            raise PluginRuntimeCommandFailure(
                SafeFailure(code="plugin_version_not_found", message="The PluginVersion was not found.")
            )
        expected[catalog.target_plugin.id] = catalog.target_version.version.id
    else:
        expected.pop(catalog.target_plugin.id, None)
    actual = {item.plugin_id: item.plugin_version_id for item in candidate.plugins}
    if actual != expected:
        raise PluginRuntimeCommandFailure(
            SafeFailure(
                code="plugin_runtime_candidate_mismatch",
                message="The candidate Runtime does not match the requested Plugin catalog.",
            )
        )


def _receipt(task: PluginRuntimeTaskRecord) -> PluginTaskReceipt:
    return PluginTaskReceipt.model_validate(
        {
            "operation_id": task.id,
            "status": task.status,
            "result_refs": task.result_refs,
            "error": task.error,
        }
    )


def _resource_ref(task: PluginRuntimeTaskRecord, *, resource_type: str, resource_id: str) -> dict[str, object]:
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "organization_id": task.organization_id,
        "workspace_id": task.workspace_id,
    }


def _terminal_audit(
    task: PluginRuntimeTaskRecord,
    *,
    status: str,
    outcome: str,
    now: datetime,
    details: dict[str, object],
) -> SecurityAuditRecord:
    return security_audit_record(
        audit_id=new_object_id("audit"),
        actor=SystemAuditActor(request_id=task.id),
        organization_id=task.organization_id,
        workspace_id=task.workspace_id,
        action=f"plugin_runtime.{task.command}.{status}",
        resource_type="plugin",
        resource_id=task.plugin_id,
        outcome=outcome,
        occurred_at=now,
        details={"operation_id": task.id, **details},
    )


def _task_actor(task: PluginRuntimeTaskRecord) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type=PrincipalType(task.actor_type), principal_id=task.actor_id),
        auth_method="plugin_runtime_task",
        credential_id=f"task_{task.id}",
        boundary_workspace_id=task.workspace_id,
        request_id=task.id,
    )


def _clear_claim(state: PluginRuntimeStateRecord, *, now: datetime) -> None:
    state.command_operation_id = None
    state.command_lease_expires_at = None
    state.updated_at = now


async def _load_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    key_digest: str,
    request_digest: str,
    now: datetime,
) -> IdempotencyEvidenceRecord | None:
    try:
        return await load_plugin_evidence(
            session,
            actor=actor,
            operation=operation,
            scope_id=scope_id,
            key_digest=key_digest,
            request_digest=request_digest,
            now=now,
        )
    except IdempotencyConflict as error:
        raise _idempotency_conflict() from error


def _idempotency_conflict() -> PluginError:
    return PluginError(
        "plugin_idempotency_conflict",
        "The Idempotency-Key was already used for a different Plugin Runtime command.",
        status_code=409,
    )
