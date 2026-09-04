"""Fenced PostgreSQL-backed Environment target keepalive execution."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import anyio
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.domain import RunStatus
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session, transaction

from .domain import (
    EnvironmentTargetRetentionBehavior,
    EnvironmentTargetStatus,
    new_environment_keepalive_operation_id,
)
from .models import EnvironmentRevisionRecord, EnvironmentTargetRecord, RunEnvironmentBindingRecord

logger = logging.getLogger("a13n_service.environments.keepalive")


@dataclass(frozen=True, slots=True)
class KeepaliveSourceBinding:
    target_id: str
    binding_id: str
    organization_id: str
    workspace_id: str
    run_id: str
    thread_id: str
    provider_key: str


@dataclass(frozen=True, slots=True)
class PreparedEnvironmentKeepalive:
    """Freshly authorized, exact-lock Provider call prepared outside a DB transaction."""

    target_id: str
    binding_id: str
    provider_key: str
    ensure_retained_until: Callable[[datetime, str], Awaitable[datetime]]


class EnvironmentKeepaliveSourceResolver(Protocol):
    """Reauthorize Principal, exact package lock, connection, and Secrets for a source."""

    async def prepare(
        self,
        source: KeepaliveSourceBinding,
    ) -> PreparedEnvironmentKeepalive | None: ...


@dataclass(frozen=True, slots=True)
class EnvironmentKeepaliveClaim:
    target_id: str
    binding_id: str
    worker_generation: str
    claim_generation: int
    operation_generation: int
    operation_id: str
    requested_alive_until: datetime
    lease_expires_at: datetime


class EnvironmentKeepaliveStore:
    """Short-transaction scan, claim, fencing, and retirement operations."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        compatible_provider_keys: Sequence[str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._compatible_provider_keys = (
            None if compatible_provider_keys is None else tuple(sorted(set(compatible_provider_keys)))
        )
        self._clock = clock

    async def scan_due(self, *, limit: int = 64) -> tuple[str, ...]:
        if limit < 1 or limit > 1024:
            raise ValueError("Environment keepalive scan limit must be between 1 and 1024")
        if self._compatible_provider_keys == ():
            return ()
        now = _utc(self._clock())
        predicates = [
            EnvironmentTargetRecord.status == EnvironmentTargetStatus.active.value,
            EnvironmentTargetRecord.active_run_count > 0,
            EnvironmentTargetRecord.retention_behavior
            == EnvironmentTargetRetentionBehavior.while_execution_active.value,
            EnvironmentTargetRecord.next_keepalive_at.is_not(None),
            EnvironmentTargetRecord.next_keepalive_at <= now,
            or_(
                EnvironmentTargetRecord.keeper_lease_expires_at.is_(None),
                EnvironmentTargetRecord.keeper_lease_expires_at <= now,
            ),
        ]
        if self._compatible_provider_keys is not None:
            predicates.append(EnvironmentTargetRecord.provider_key.in_(self._compatible_provider_keys))
        async with short_session(self._sessions) as database:
            return tuple(
                (
                    await database.scalars(
                        select(EnvironmentTargetRecord.id)
                        .where(*predicates)
                        .order_by(EnvironmentTargetRecord.next_keepalive_at, EnvironmentTargetRecord.id)
                        .limit(limit)
                    )
                ).all()
            )

    async def source_candidates(self, target_id: str, *, limit: int = 64) -> tuple[KeepaliveSourceBinding, ...]:
        if limit < 1 or limit > 1024:
            raise ValueError("Environment keepalive source limit must be between 1 and 1024")
        async with short_session(self._sessions) as database:
            rows = (
                await database.execute(
                    select(
                        RunEnvironmentBindingRecord,
                        RunRecord.thread_id,
                    )
                    .join(
                        RunRecord,
                        (RunRecord.tenant_id == RunEnvironmentBindingRecord.organization_id)
                        & (RunRecord.id == RunEnvironmentBindingRecord.run_id),
                    )
                    .where(
                        RunEnvironmentBindingRecord.environment_target_id == target_id,
                        RunRecord.status.in_((RunStatus.accepted.value, RunStatus.running.value)),
                    )
                    .order_by(RunRecord.created_at, RunEnvironmentBindingRecord.id)
                    .limit(limit)
                )
            ).all()
        return tuple(
            KeepaliveSourceBinding(
                target_id=record.environment_target_id,
                binding_id=record.id,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                run_id=record.run_id,
                thread_id=thread_id,
                provider_key=record.provider_key,
            )
            for record, thread_id in rows
        )

    async def claim(
        self,
        prepared: PreparedEnvironmentKeepalive,
        *,
        worker_generation: str,
        lease_duration: timedelta,
        retention_window: timedelta,
    ) -> EnvironmentKeepaliveClaim | None:
        if not worker_generation:
            raise ValueError("Environment Keeper worker generation must not be empty")
        if lease_duration <= timedelta(0) or retention_window <= lease_duration:
            raise ValueError("retention window must be longer than the positive Keeper lease")
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            source = await database.scalar(
                select(RunEnvironmentBindingRecord)
                .join(
                    RunRecord,
                    (RunRecord.tenant_id == RunEnvironmentBindingRecord.organization_id)
                    & (RunRecord.id == RunEnvironmentBindingRecord.run_id),
                )
                .where(
                    RunEnvironmentBindingRecord.id == prepared.binding_id,
                    RunEnvironmentBindingRecord.environment_target_id == prepared.target_id,
                    RunEnvironmentBindingRecord.provider_key == prepared.provider_key,
                    RunRecord.status.in_((RunStatus.accepted.value, RunStatus.running.value)),
                )
                .with_for_update(of=RunRecord)
            )
            if source is None:
                return None
            target = await database.scalar(
                select(EnvironmentTargetRecord)
                .where(EnvironmentTargetRecord.id == prepared.target_id)
                .with_for_update()
            )
            if not _claimable(target, now):
                return None
            assert target is not None
            outstanding = (
                target.operation_id is not None
                and target.requested_alive_until is not None
                and (
                    target.last_error is None
                    or target.last_error.get("code") == "environment_keepalive_outcome_unknown"
                )
                and (
                    target.acknowledged_alive_until is None
                    or _utc(target.acknowledged_alive_until) < _utc(target.requested_alive_until)
                )
            )
            if outstanding:
                assert target.operation_id is not None
                assert target.requested_alive_until is not None
                operation_id = target.operation_id
                requested_alive_until = _utc(target.requested_alive_until)
            else:
                target.operation_generation += 1
                operation_id = new_environment_keepalive_operation_id()
                requested_alive_until = now + retention_window
                target.operation_id = operation_id
                target.requested_alive_until = requested_alive_until
            target.keeper_claim_generation += 1
            target.keeper_owner_worker_generation = worker_generation
            target.keeper_lease_expires_at = now + lease_duration
            target.keeper_source_binding_id = prepared.binding_id
            target.last_error = None
            target.updated_at = now
            return EnvironmentKeepaliveClaim(
                target_id=target.id,
                binding_id=prepared.binding_id,
                worker_generation=worker_generation,
                claim_generation=target.keeper_claim_generation,
                operation_generation=target.operation_generation,
                operation_id=operation_id,
                requested_alive_until=requested_alive_until,
                lease_expires_at=now + lease_duration,
            )

    async def acknowledge(
        self,
        claim: EnvironmentKeepaliveClaim,
        *,
        acknowledged_alive_until: datetime,
        refresh_margin: timedelta,
    ) -> bool:
        acknowledged = _utc(acknowledged_alive_until)
        if acknowledged < claim.requested_alive_until:
            raise ValueError("Provider acknowledgement does not cover the requested retention deadline")
        if refresh_margin <= timedelta(0):
            raise ValueError("Environment Keeper refresh margin must be positive")
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            target = await _lock_current_claim(database, claim)
            if target is None:
                return False
            prior = target.acknowledged_alive_until
            target.acknowledged_alive_until = acknowledged if prior is None else max(_utc(prior), acknowledged)
            _clear_claim(target)
            target.last_error = None
            if target.status == EnvironmentTargetStatus.active.value and target.active_run_count > 0:
                target.next_keepalive_at = max(now, acknowledged - refresh_margin)
            else:
                target.next_keepalive_at = None
            target.updated_at = now
            return True

    async def fail(
        self,
        claim: EnvironmentKeepaliveClaim,
        *,
        code: str,
        message: str,
        retry_backoff: timedelta,
    ) -> bool:
        if retry_backoff <= timedelta(0):
            raise ValueError("Environment Keeper retry backoff must be positive")
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            target = await _lock_current_claim(database, claim)
            if target is None:
                return False
            _clear_claim(target)
            target.last_error = {"code": code[:128], "message": message[:1024]}
            if target.status == EnvironmentTargetStatus.active.value and target.active_run_count > 0:
                target.next_keepalive_at = now + retry_backoff
            else:
                target.next_keepalive_at = None
            target.updated_at = now
            return True

    async def record_source_unavailable(
        self,
        target_id: str,
        *,
        retry_backoff: timedelta,
    ) -> bool:
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            target = await database.scalar(
                select(EnvironmentTargetRecord).where(EnvironmentTargetRecord.id == target_id).with_for_update()
            )
            if not _claimable(target, now):
                return False
            assert target is not None
            target.last_error = {
                "code": "environment_keepalive_source_unavailable",
                "message": "No active Environment binding supplied usable current authority.",
            }
            target.next_keepalive_at = now + retry_backoff
            target.updated_at = now
            return True

    async def retire_due(self, *, limit: int = 64) -> int:
        """Mark due idle rows retired; physical audit-aware cleanup is separate."""

        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            targets = tuple(
                (
                    await database.scalars(
                        select(EnvironmentTargetRecord)
                        .where(
                            EnvironmentTargetRecord.status == EnvironmentTargetStatus.idle.value,
                            EnvironmentTargetRecord.active_run_count == 0,
                            EnvironmentTargetRecord.retire_after.is_not(None),
                            EnvironmentTargetRecord.retire_after <= now,
                            or_(
                                EnvironmentTargetRecord.keeper_lease_expires_at.is_(None),
                                EnvironmentTargetRecord.keeper_lease_expires_at <= now,
                            ),
                        )
                        .order_by(EnvironmentTargetRecord.retire_after, EnvironmentTargetRecord.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for target in targets:
                target.status = EnvironmentTargetStatus.retired.value
                target.updated_at = now
            return len(targets)

    async def delete_unreferenced_retired(
        self,
        *,
        audit_retention: timedelta,
        limit: int = 64,
    ) -> int:
        """Physically remove only audit-aged tombstones with no retained references."""

        if audit_retention <= timedelta(0):
            raise ValueError("Environment target audit retention must be positive")
        now = _utc(self._clock())
        revision_exists = (
            select(EnvironmentRevisionRecord.id)
            .where(EnvironmentRevisionRecord.environment_target_id == EnvironmentTargetRecord.id)
            .exists()
        )
        binding_exists = (
            select(RunEnvironmentBindingRecord.id)
            .where(RunEnvironmentBindingRecord.environment_target_id == EnvironmentTargetRecord.id)
            .exists()
        )
        async with transaction(self._sessions) as database:
            targets = tuple(
                (
                    await database.scalars(
                        select(EnvironmentTargetRecord)
                        .where(
                            EnvironmentTargetRecord.status == EnvironmentTargetStatus.retired.value,
                            EnvironmentTargetRecord.active_run_count == 0,
                            EnvironmentTargetRecord.updated_at <= now - audit_retention,
                            or_(
                                EnvironmentTargetRecord.keeper_lease_expires_at.is_(None),
                                EnvironmentTargetRecord.keeper_lease_expires_at <= now,
                            ),
                            ~revision_exists,
                            ~binding_exists,
                        )
                        .order_by(EnvironmentTargetRecord.updated_at, EnvironmentTargetRecord.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for target in targets:
                await database.delete(target)
            return len(targets)


class EnvironmentKeepaliveLoop:
    """Dedicated Worker loop whose capacity is independent of Agent execution slots."""

    def __init__(
        self,
        store: EnvironmentKeepaliveStore,
        sources: EnvironmentKeepaliveSourceResolver,
        *,
        worker_generation: str,
        poll_interval_seconds: float = 5,
        lease_seconds: float = 30,
        retention_window_seconds: float = 300,
        refresh_margin_seconds: float = 120,
        call_timeout_seconds: float = 15,
        retry_backoff_seconds: float = 15,
        tombstone_retention_seconds: float = 30 * 24 * 60 * 60,
        max_concurrency: int = 4,
    ) -> None:
        if not worker_generation:
            raise ValueError("Environment Keeper worker generation must not be empty")
        if (
            min(
                poll_interval_seconds,
                lease_seconds,
                retention_window_seconds,
                refresh_margin_seconds,
                call_timeout_seconds,
                retry_backoff_seconds,
                tombstone_retention_seconds,
            )
            <= 0
        ):
            raise ValueError("Environment Keeper durations must be positive")
        if retention_window_seconds <= lease_seconds or retention_window_seconds <= refresh_margin_seconds:
            raise ValueError("Environment Keeper window must exceed lease and refresh margin")
        if max_concurrency < 1 or max_concurrency > 128:
            raise ValueError("Environment Keeper concurrency must be between 1 and 128")
        self._store = store
        self._sources = sources
        self._worker_generation = worker_generation
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_duration = timedelta(seconds=lease_seconds)
        self._retention_window = timedelta(seconds=retention_window_seconds)
        self._refresh_margin = timedelta(seconds=refresh_margin_seconds)
        self._call_timeout_seconds = call_timeout_seconds
        self._retry_backoff = timedelta(seconds=retry_backoff_seconds)
        self._tombstone_retention = timedelta(seconds=tombstone_retention_seconds)
        self._max_concurrency = max_concurrency
        self._draining = False
        self._drain_event = anyio.Event()
        self._stopped_event = anyio.Event()

    def drain(self) -> None:
        self._draining = True
        self._drain_event.set()

    def is_draining(self) -> bool:
        return self._draining

    async def wait_stopped(self) -> None:
        await self._stopped_event.wait()

    async def run(self) -> None:
        try:
            while not self._draining:
                await self.reconcile_once()
                with anyio.move_on_after(self._poll_interval_seconds):
                    await self._drain_event.wait()
        finally:
            self._stopped_event.set()

    async def reconcile_once(self, *, limit: int = 64) -> int:
        if self._draining:
            return 0
        target_ids = await self._store.scan_due(limit=limit)
        limiter = anyio.CapacityLimiter(self._max_concurrency)

        async def reconcile(target_id: str) -> None:
            async with limiter:
                await self._reconcile_target(target_id)

        async with anyio.create_task_group() as tasks:
            for target_id in target_ids:
                tasks.start_soon(reconcile, target_id)
        await self._store.retire_due(limit=limit)
        await self._store.delete_unreferenced_retired(
            audit_retention=self._tombstone_retention,
            limit=limit,
        )
        return len(target_ids)

    async def _reconcile_target(self, target_id: str) -> None:
        prepared: PreparedEnvironmentKeepalive | None = None
        for source in await self._store.source_candidates(target_id):
            try:
                candidate = await self._sources.prepare(source)
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                logger.warning(
                    "environment_keepalive_source_prepare_failed",
                    extra={"event": "environment_keepalive_source_prepare_failed", "target_id": target_id},
                    exc_info=True,
                )
                continue
            if candidate is not None and (
                candidate.target_id,
                candidate.binding_id,
                candidate.provider_key,
            ) == (source.target_id, source.binding_id, source.provider_key):
                prepared = candidate
                break
        if prepared is None:
            await self._store.record_source_unavailable(target_id, retry_backoff=self._retry_backoff)
            return
        claim = await self._store.claim(
            prepared,
            worker_generation=self._worker_generation,
            lease_duration=self._lease_duration,
            retention_window=self._retention_window,
        )
        if claim is None:
            return
        try:
            with anyio.fail_after(self._call_timeout_seconds):
                acknowledged = await prepared.ensure_retained_until(
                    claim.requested_alive_until,
                    claim.operation_id,
                )
            await self._store.acknowledge(
                claim,
                acknowledged_alive_until=acknowledged,
                refresh_margin=self._refresh_margin,
            )
        except anyio.get_cancelled_exc_class():
            raise
        except TimeoutError:
            await self._store.fail(
                claim,
                code="environment_keepalive_outcome_unknown",
                message="The Provider retention outcome is unknown and will be retried safely.",
                retry_backoff=self._retry_backoff,
            )
        except Exception:
            logger.warning(
                "environment_keepalive_provider_failed",
                extra={"event": "environment_keepalive_provider_failed", "target_id": target_id},
                exc_info=True,
            )
            await self._store.fail(
                claim,
                code="environment_keepalive_failed",
                message="The Provider could not extend target retention.",
                retry_backoff=self._retry_backoff,
            )


def _claimable(target: EnvironmentTargetRecord | None, now: datetime) -> bool:
    if target is None:
        return False
    return (
        target.status == EnvironmentTargetStatus.active.value
        and target.active_run_count > 0
        and target.retention_behavior == EnvironmentTargetRetentionBehavior.while_execution_active.value
        and target.next_keepalive_at is not None
        and _utc(target.next_keepalive_at) <= now
        and (target.keeper_lease_expires_at is None or _utc(target.keeper_lease_expires_at) <= now)
    )


async def _lock_current_claim(
    database: AsyncSession,
    claim: EnvironmentKeepaliveClaim,
) -> EnvironmentTargetRecord | None:
    target = await database.scalar(
        select(EnvironmentTargetRecord).where(EnvironmentTargetRecord.id == claim.target_id).with_for_update()
    )
    if target is None or (
        target.keeper_claim_generation,
        target.operation_generation,
        target.keeper_owner_worker_generation,
        target.keeper_source_binding_id,
        target.operation_id,
    ) != (
        claim.claim_generation,
        claim.operation_generation,
        claim.worker_generation,
        claim.binding_id,
        claim.operation_id,
    ):
        return None
    return target


def _clear_claim(target: EnvironmentTargetRecord) -> None:
    target.keeper_owner_worker_generation = None
    target.keeper_lease_expires_at = None
    target.keeper_source_binding_id = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__: Sequence[str] = (
    "EnvironmentKeepaliveClaim",
    "EnvironmentKeepaliveLoop",
    "EnvironmentKeepaliveSourceResolver",
    "EnvironmentKeepaliveStore",
    "KeepaliveSourceBinding",
    "PreparedEnvironmentKeepalive",
)
