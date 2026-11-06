"""Thread / Device admission and retirement around the shared Harness Session owner."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from a13n_envd_client import EIPDeviceConnection, EIPSession
from a13n_envd_client.eip.v1 import SessionConfiguration
from a13n_harness.environment.envd_policy import EnvdBoundaryRequirement
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState
from anyio import CancelScope, fail_after

from a13n_harness_ui.storage.envd_sessions import EnvdSessionRepository, SessionBindingHead, SessionTarget
from a13n_harness_ui.storage.ownership import OwnershipLocks


class SessionBinding:
    """Serialize selection/acquisition; keep OS ownership until the final borrower exits."""

    def __init__(
        self,
        *,
        thread_id: str,
        device_key: str,
        repository: EnvdSessionRepository,
        owners: OwnershipLocks,
        require_idle: Callable[[], Awaitable[None]],
        retirement_device: Callable[[SessionTarget, EnvdSessionState], Awaitable[EIPDeviceConnection | None]],
    ) -> None:
        self.thread_id = thread_id
        self.device_key = device_key
        self._identity = f"{thread_id}:{device_key}"
        self._repository = repository
        self._owners = owners
        self._require_idle = require_idle
        self._retirement_device = retirement_device
        self._lock = asyncio.Lock()
        self._users = 0
        self._head: SessionBindingHead | None = None
        self._recovery = EnvdSessionRecovery(checkpoint=self._checkpoint)

    @asynccontextmanager
    async def _ownership(self) -> AsyncIterator[None]:
        # Called under _lock. Live borrowers already hold this binding's OS lock.
        acquired = self._users == 0
        if acquired:
            if not await self._owners.acquire(self._identity):
                raise EnvironmentError("Another App owns this Envd binding.", code="environment_busy")
        try:
            if acquired:
                self._head = await self._repository.get(self.thread_id, self.device_key)
                self._recovery.state = None if self._head is None else self._head.session
            yield
        finally:
            if acquired and self._users == 0:
                await self._owners.release(self._identity)

    @asynccontextmanager
    async def selection(
        self, digest: str | None
    ) -> AsyncIterator[tuple[SessionBindingHead | None, SessionBindingHead]]:
        """Hold admission stable until the caller commits every selected Device together."""
        async with self._lock, self._ownership():
            previous = self._head
            replacement = previous
            if previous is None:
                replacement = SessionBindingHead(version=1, configuration_digest=digest)
            elif previous.configuration_digest != digest:
                await self._idle()
                replacement = previous.model_copy(
                    update={"version": previous.version + 1, "configuration_digest": digest}
                )
            assert replacement is not None
            yield previous, replacement
            self._head = replacement

    async def validate(self, version: int) -> None:
        head = await self._repository.get(self.thread_id, self.device_key)
        if head is None or head.version != version or head.configuration_digest is None:
            raise EnvironmentError("The captured Session selection is no longer current.", code="envd_session_conflict")

    def _validate(self, version: int) -> SessionBindingHead:
        if self._head is None or self._head.version != version or self._head.configuration_digest is None:
            raise EnvironmentError("The captured Session selection is no longer current.", code="envd_session_conflict")
        return self._head

    async def _idle(self) -> None:
        if self._users:
            raise EnvironmentError("Session borrowers must finish before replacement.", code="environment_busy")
        await self._require_idle()

    async def _save(self, replacement: SessionBindingHead) -> None:
        with CancelScope(shield=True), fail_after(30):
            await self._repository.select(self.thread_id, self.device_key, expected=self._head, replacement=replacement)
            self._head = replacement

    async def _checkpoint(self, state: EnvdSessionState) -> None:
        assert self._head is not None
        await self._save(self._head.model_copy(update={"session": state}))

    async def _retire(self) -> None:
        assert self._head is not None
        previous = self._head.session
        if previous is None or previous.status == "retired":
            return
        await self._idle()
        old_target = self._head.session_target
        assert old_target is not None
        await self._checkpoint(previous.model_copy(update={"status": "retiring"}))
        device = await self._retirement_device(old_target, previous)
        if device is None:
            # Host-owned stdio daemons die with their owner; no remote endpoint is guessed.
            await self._checkpoint(previous.model_copy(update={"status": "retired"}))
            self._recovery.state = self._head.session
        else:
            await self._recovery.retire(device, target=previous.target)

    @asynccontextmanager
    async def borrow(
        self,
        device: EIPDeviceConnection,
        *,
        version: int,
        recipe: SessionTarget,
        original_session_id: str | None,
        target: str,
        expected_device_id: str,
        configuration: SessionConfiguration,
        required_methods: frozenset[str],
        expected_boundary: EnvdBoundaryRequirement | None,
    ) -> AsyncIterator[EIPSession]:
        scope = AsyncExitStack()
        async with self._lock, self._ownership():
            head = self._validate(version)
            if head.session_version != version:
                if original_session_id is not None:
                    raise EnvironmentError("The original Session is no longer current.", code="environment_not_found")
                await self._retire()
                assert self._head is not None
                await self._save(
                    self._head.model_copy(
                        update={
                            "session_version": version,
                            "session_target": recipe,
                        }
                    )
                )
            try:
                session = await scope.enter_async_context(
                    self._recovery.borrow(
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
                    await scope.aclose()
                raise
            self._users += 1
        try:
            yield session
        finally:
            with CancelScope(shield=True):
                async with self._lock:
                    try:
                        await scope.aclose()
                    finally:
                        self._users -= 1
                        if self._users == 0:
                            await self._owners.release(self._identity)


@dataclass(frozen=True, slots=True)
class AdmittedSession:
    owner: SessionBinding
    version: int
    recipe: SessionTarget
    original_session_id: str | None = None

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
        return self.owner.borrow(
            device,
            version=self.version,
            recipe=self.recipe,
            original_session_id=self.original_session_id,
            target=target,
            expected_device_id=expected_device_id,
            configuration=configuration,
            required_methods=required_methods,
            expected_boundary=expected_boundary,
        )
