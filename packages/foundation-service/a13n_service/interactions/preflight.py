"""Exact Plugin Runtime admission before relational Attempt allocation."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_logging import get_logger
from anyio import fail_after
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.plugins.on_demand import OnDemandPluginRuntime, OnDemandPluginRuntimeDeclined
from a13n_service.plugins.runner_bootstrap import BootstrappedPluginRuntime
from a13n_service.plugins.runtime import PluginRuntimeLockError, PluginRuntimeLockStore
from a13n_service.storage import short_session

from .attempt_executor import CapacitySlot
from .models import RunRecord
from .scheduling import ClaimedAttempt, RunCandidate
from .worker import AttemptLauncher, ManagedAttempt

logger = get_logger(__name__)

type RuntimeAttemptFactory = Callable[[ClaimedAttempt, CapacitySlot, HarnessPluginFactoryCatalog], ManagedAttempt]


@dataclass(frozen=True, slots=True)
class _PreparedLauncher:
    candidate: RunCandidate
    catalog: HarnessPluginFactoryCatalog
    factory: RuntimeAttemptFactory

    def create(self, claimed: ClaimedAttempt, capacity_slot: CapacitySlot) -> ManagedAttempt:
        attempt = claimed.attempt
        if (
            attempt.organization_id != self.candidate.organization_id
            or attempt.run_id != self.candidate.run_id
            or attempt.runtime_lock_digest != self.candidate.runtime_lock_digest
        ):
            raise ValueError("The claimed Attempt does not match its preflighted Run and Runtime")
        return self.factory(claimed, capacity_slot, self.catalog)


class OnDemandExecutionPreflight:
    """Load the pinned lock, close its session, then prepare process-local imports."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        locks: PluginRuntimeLockStore,
        runtime: OnDemandPluginRuntime,
        factory: RuntimeAttemptFactory,
        *,
        timeout_seconds: float,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Runtime preflight timeout must be finite and positive")
        self._sessions = sessions
        self._locks = locks
        self._runtime = runtime
        self._factory = factory
        self._timeout = timeout_seconds

    async def prepare(self, candidate: RunCandidate) -> AttemptLauncher | None:
        try:
            with fail_after(self._timeout):
                async with short_session(self._sessions) as database:
                    digest = await database.scalar(
                        select(RunRecord.runtime_lock_digest).where(
                            RunRecord.organization_id == candidate.organization_id, RunRecord.id == candidate.run_id
                        )
                    )
                    if digest is None or digest != candidate.runtime_lock_digest:
                        return None
                    lock = await self._locks.require(database, digest, mode="on_demand")
                prepared = await self._runtime.prepare_for_claim(lock)
        except (PluginRuntimeLockError, OnDemandPluginRuntimeDeclined, ValidationError, TimeoutError) as error:
            reason = (
                error.reason
                if isinstance(error, (PluginRuntimeLockError, OnDemandPluginRuntimeDeclined))
                else (
                    "plugin_runtime_preflight_timeout"
                    if isinstance(error, TimeoutError)
                    else "plugin_runtime_lock_invalid"
                )
            )
            logger.debug(
                "run_runtime_preflight_declined",
                extra={
                    "run_id": candidate.run_id,
                    "runtime_lock_digest": candidate.runtime_lock_digest,
                    "reason": reason,
                },
            )
            return None
        # Fatal post-import failures deliberately escape: the interpreter must exit.
        if prepared.runtime_lock_digest != candidate.runtime_lock_digest:
            raise RuntimeError("Plugin Runtime preparation returned a different lock")
        return _PreparedLauncher(candidate, prepared.factory_catalog, self._factory)


class RunnerExecutionPreflight:
    """Admit only the exact catalog already verified in this Runner interpreter."""

    def __init__(self, runtime: BootstrappedPluginRuntime, factory: RuntimeAttemptFactory) -> None:
        lock = runtime.runtime_lock
        if lock.mode != "runner" or lock.computed_digest() != lock.digest:
            raise ValueError("Runner execution requires an exact bootstrapped runner lock")
        self._runtime = runtime
        self._factory = factory

    async def prepare(self, candidate: RunCandidate) -> AttemptLauncher | None:
        if candidate.runtime_lock_digest != self._runtime.runtime_lock.digest:
            return None
        return _PreparedLauncher(candidate, self._runtime.factory_catalog, self._factory)


__all__ = ["OnDemandExecutionPreflight", "RunnerExecutionPreflight", "RuntimeAttemptFactory"]
