"""One worker-owned Session with versioned borrowers and durable retirement."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from a13n_envd_client import EIPDeviceConnection, EIPSession
from a13n_envd_client.eip.v1 import AllowlistDestinations, EgressMode, EgressPolicy, SessionConfiguration
from a13n_harness.environment.envd_policy import EnvdBoundaryRequirement, EnvironmentEnvdCredentialResolver
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState
from anyio import CancelScope
from sqlalchemy import select

from a13n_service.infra.db import lock, transaction
from a13n_service.runs.attempts import LeaseLost
from a13n_service.runs.environments.bindings import SessionHead
from a13n_service.runs.environments.tables import ThreadExecutionContextRow, ThreadSessionBindingRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount


class SessionOwner:
    def __init__(self, runtime: Runtime, thread_id: str, worker_id: str, generation: int, key: tuple[str, str]):
        self.runtime, self.thread_id, self.worker_id, self.generation, self.key = (
            runtime,
            thread_id,
            worker_id,
            generation,
            key,
        )
        self._lock = asyncio.Lock()
        self._head: SessionHead | None = None
        self._users = 0
        self.recovery = EnvdSessionRecovery(checkpoint=self._checkpoint)

    async def _read(self, mount: EnvironmentMount) -> SessionHead:
        async with transaction(self.runtime.storage) as session:
            row = await lock(session, ThreadExecutionContextRow, self.thread_id)
            if row is None or row.worker_id != self.worker_id or row.generation != self.generation:
                raise LeaseLost()
            binding = await session.get(ThreadSessionBindingRow, (self.thread_id, *self.key))
            head = (
                SessionHead.model_validate(binding.head) if binding is not None and binding.head is not None else None
            )
            if (
                head is None
                or head.version != mount.session_version
                or head.configuration != mount.session_configuration
            ):
                raise EnvironmentError(
                    "The captured Session binding is no longer current", code="envd_session_conflict"
                )
            return head

    async def _save(self, replacement: SessionHead) -> None:
        from a13n_service.runs.environments.contexts import live_owner

        assert self._head is not None
        with CancelScope(shield=True):
            async with transaction(self.runtime.storage) as session:
                row = await lock(session, ThreadExecutionContextRow, self.thread_id)
                if (
                    row is None
                    or row.worker_id != self.worker_id
                    or row.generation != self.generation
                    or not await session.scalar(select(live_owner(self.thread_id, self.worker_id)))
                ):
                    raise LeaseLost()
                binding = await session.get(ThreadSessionBindingRow, (self.thread_id, *self.key))
                if binding is None or binding.head != self._head.model_dump(mode="json"):
                    raise LeaseLost()
                binding.head = replacement.model_dump(mode="json")
            self._head = replacement

    async def _checkpoint(self, state: EnvdSessionState) -> None:
        assert self._head is not None
        await self._save(self._head.model_copy(update={"session": state}))

    @asynccontextmanager
    async def borrow(
        self,
        device: EIPDeviceConnection,
        *,
        mount: EnvironmentMount,
        original_session_id: str | None,
        target: str,
        expected_device_id: str,
        required_methods: frozenset[str],
        expected_boundary: EnvdBoundaryRequirement | None,
    ) -> AsyncIterator[EIPSession]:
        lifetime = AsyncExitStack()
        async with self._lock:
            head = await self._read(mount)
            if self._users == 0:
                self._head = head
                self.recovery.state = head.session
            if head.session_version != mount.session_version:
                if original_session_id is not None:
                    raise EnvironmentError("The original Session is no longer current", code="environment_not_found")
                if self._users:
                    raise EnvironmentError("The Session still has active borrowers", code="environment_busy")
                # Environment instances keep their Device identity across endpoint/credential edits.
                # retire() checks that identity and checkpoints the exact old descriptor before native I/O.
                await self.recovery.retire(device, target=target)
                assert self._head is not None
                await self._save(self._head.model_copy(update={"session_version": mount.session_version}))
            configuration = await mount.session_configuration.resolve(EnvironmentEnvdCredentialResolver())
            if configuration.egress is None and (
                configuration.egress_mode == EgressMode.CONTROLLED
                or (configuration.egress_mode is None and device.descriptor.boundary.egress == EgressMode.CONTROLLED)
            ):
                configuration = configuration.model_copy(
                    update={"egress": EgressPolicy(destinations=AllowlistDestinations(mode="allowlist"))}
                )
            try:
                session = await lifetime.enter_async_context(
                    self.recovery.borrow(
                        device,
                        target=target,
                        expected_device_id=expected_device_id,
                        configuration=configuration,
                        required_methods=required_methods,
                        expected_boundary=expected_boundary,
                        original_session_id=original_session_id,
                    )
                )
            except BaseException:
                with CancelScope(shield=True):
                    await lifetime.aclose()
                raise
            self._users += 1
        try:
            yield session
        finally:
            with CancelScope(shield=True):
                async with self._lock:
                    try:
                        await lifetime.aclose()
                    finally:
                        self._users -= 1


@dataclass(frozen=True, slots=True)
class AdmittedSession:
    owner: SessionOwner
    mount: EnvironmentMount
    original_session_id: str | None = None

    def exact(self, session_id: str) -> "AdmittedSession":
        return AdmittedSession(self.owner, self.mount, session_id)

    def borrow(
        self,
        device: EIPDeviceConnection,
        *,
        target: str,
        expected_device_id: str,
        configuration: SessionConfiguration,
        required_methods: frozenset[str],
        expected_boundary: EnvdBoundaryRequirement | None,
    ):
        # Providers own connection setup; the admitted binding alone owns Session startup policy.
        return self.owner.borrow(
            device,
            mount=self.mount,
            original_session_id=self.original_session_id,
            # The registered instance is the stable target; endpoints are replaceable routes to it.
            # Managed recreation selects a new environment_id, never a new Device under this binding.
            target=self.mount.environment_id,
            expected_device_id=expected_device_id,
            required_methods=required_methods,
            expected_boundary=expected_boundary,
        )
