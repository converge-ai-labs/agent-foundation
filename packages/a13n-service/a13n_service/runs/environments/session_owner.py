"""One worker-owned Session with versioned borrowers and durable retirement."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from a13n_envd_client import EIPDeviceConnection, EIPSession
from a13n_envd_client.eip.v1 import AllowlistDestinations, EgressMode, EgressPolicy, ErrorType, SessionDescriptor
from a13n_envd_client.errors import EIPMethodError
from a13n_harness.environment.envd_policy import EnvdBoundaryRequirement
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState, SessionConfigurationSource
from a13n_logging import get_logger
from anyio import CancelScope
from sqlalchemy import select

from a13n_service.infra.db import lock, transaction
from a13n_service.runs.attempts import LeaseLost
from a13n_service.runs.environments.bindings import SessionHead
from a13n_service.runs.environments.egress import storage as credentials
from a13n_service.runs.environments.egress.state import Snapshot, SyncState
from a13n_service.runs.environments.tables import ThreadExecutionContextRow, ThreadSessionBindingRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.tenancy.authorize import WorkspaceScope

logger = get_logger(__name__)


class SessionOwner:
    def __init__(
        self,
        runtime: Runtime,
        thread_id: str,
        worker_id: str,
        generation: int,
        key: tuple[str, str],
        scope: WorkspaceScope,
    ):
        self.runtime, self.thread_id, self.worker_id, self.generation, self.key = (
            runtime,
            thread_id,
            worker_id,
            generation,
            key,
        )
        self._lock = asyncio.Lock()
        self.scope = scope
        self._head: SessionHead | None = None
        self._users = 0
        self._session: EIPSession | None = None
        self._mount: EnvironmentMount | None = None
        self.recovery = EnvdSessionRecovery(checkpoint=self._checkpoint)

    async def _read(self, mount: EnvironmentMount) -> SessionHead:
        from a13n_service.runs.environments.contexts import live_owner

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

    async def _save(self, replacement: SessionHead, *, applied: Snapshot | None = None) -> None:
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
                previous = self._head.session
                current = replacement.session
                version = replacement.session_version
                if current is not None and version is not None:
                    if current.status == "retired":
                        await credentials.retire(session, self.thread_id, self.key, version)
                    elif self._head.configuration.credential_references and (
                        previous is None
                        or (previous.descriptor.session_id, previous.descriptor.generation)
                        != (current.descriptor.session_id, current.descriptor.generation)
                    ):
                        await credentials.reset(session, self.thread_id, self.key, version)
                    if applied is not None:
                        await credentials.applied(
                            session, self.thread_id, self.key, version, applied, current.descriptor
                        )
                binding.head = replacement.model_dump(mode="json")
            self._head = replacement

    async def _checkpoint(self, state: EnvdSessionState) -> None:
        assert self._head is not None
        if state.descriptor.egress is not None:
            state = state.model_copy(
                update={
                    "descriptor": state.descriptor.model_copy(
                        update={
                            "egress": state.descriptor.egress.model_copy(update={"secrets": ()}),
                        }
                    )
                }
            )
        sync = self._head.egress_sync
        if sync is not None and (
            state.status != "active"
            or (sync.session_id, sync.device_generation)
            != (
                state.descriptor.session_id,
                state.descriptor.generation,
            )
        ):
            sync = None
        await self._save(self._head.model_copy(update={"session": state, "egress_sync": sync}))

    @property
    def active(self) -> bool:
        return self._users > 0 and self._session is not None and not self._session.is_closed

    @property
    def sync_key(self) -> tuple[str, str, str, int] | None:
        if self._mount is None or not self._mount.session_configuration.credential_references:
            return None
        return self.thread_id, *self.key, self._mount.session_version

    @property
    def sync_failed(self) -> bool:
        return self._head is not None and self._head.egress_sync is not None and self._head.egress_sync.failures > 0

    async def _sync(self, session: EIPSession, mount: EnvironmentMount) -> Snapshot:
        """The caller holds the owner lock; native I/O never holds a database transaction."""
        for attempt in range(3):
            await self._read(mount)
            snapshot = await credentials.load(
                self.runtime.storage, self.runtime.keys, self.scope, mount.session_configuration
            )
            descriptor = await session.describe()
            if descriptor.egress is None:
                raise EnvironmentError("Session does not support controlled egress", code="egress_unsupported")
            try:
                result = await session.client.egress_update(snapshot.update(descriptor.egress))
            except EIPMethodError as error:
                if error.error.data.error_type == ErrorType.CONFLICT and attempt < 2:
                    continue
                raise
            assert self._head is not None
            await self._save(
                self._head.model_copy(
                    update={
                        "egress_sync": SyncState(
                            session_id=descriptor.session_id,
                            device_generation=descriptor.generation,
                            revision=result.egress.revision,
                        )
                    }
                ),
                applied=snapshot,
            )
            logger.info(
                "Session credentials synchronized",
                extra={
                    "thread_id": self.thread_id,
                    "environment_id": self.key[0],
                    "session_id": descriptor.session_id,
                    "egress_revision": result.egress.revision,
                    "credential_versions": {key: value.version for key, value in snapshot.credentials.items()},
                },
            )
            return snapshot
        raise AssertionError("unreachable")

    async def synchronize(self, *, force: bool = False) -> bool:
        """Only refresh an already borrowed Session; never acquire, attach or extend an idle one."""
        async with self._lock:
            if not self.active or self._mount is None or not self._mount.session_configuration.credential_references:
                return False
            assert self._head is not None and self._session is not None
            sync = self._head.egress_sync
            if not force and sync is not None and sync.retry_at is not None and sync.retry_at > datetime.now(UTC):
                return False
            try:
                await self._refresh(self._session, self._mount)
            except LeaseLost:
                return False
            return True

    async def _refresh(self, session: EIPSession, mount: EnvironmentMount) -> Snapshot:
        try:
            async with asyncio.timeout(self.runtime.settings.environments.credential_sync_timeout_seconds):
                return await self._sync(session, mount)
        except LeaseLost:
            raise
        except Exception as error:
            await self._failure(session.descriptor, error)
            raise

    async def _failure(self, descriptor: SessionDescriptor, error: Exception) -> None:
        assert self._head is not None
        previous = self._head.egress_sync
        failures = (previous.failures if previous is not None else 0) + 1
        await self._save(
            self._head.model_copy(
                update={
                    "egress_sync": SyncState(
                        session_id=descriptor.session_id,
                        device_generation=descriptor.generation,
                        revision=previous.revision if previous is not None else None,
                        failures=failures,
                        retry_at=datetime.now(UTC) + timedelta(seconds=min(300, 10 * 2 ** min(failures - 1, 5))),
                        error_code="credential_sync_timeout"
                        if isinstance(error, TimeoutError)
                        else "credential_sync_failed",
                    )
                }
            )
        )

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
            snapshot = await credentials.load(
                self.runtime.storage, self.runtime.keys, self.scope, mount.session_configuration
            )
            if snapshot.revoked:
                if self._head is None or self._head.session is None or self._head.session.status != "active":
                    raise EnvironmentError("A selected credential has been revoked", code="credential_revoked")
                # Attach only the original incarnation to remove bindings; never create a revoked selection.
                original_session_id = self._head.session.descriptor.session_id
            configuration = snapshot.native_configuration()
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
                        configuration_identity=(
                            mount.session_configuration.model_dump_json()
                            if mount.session_configuration.credential_references
                            else None
                        ),
                    )
                )
                if mount.session_configuration.credential_references:
                    snapshot = await self._refresh(session, mount)
                    if snapshot.revoked:
                        raise EnvironmentError("A selected credential has been revoked", code="credential_revoked")
            except BaseException:
                with CancelScope(shield=True):
                    await lifetime.aclose()
                raise
            self._users += 1
            self._session, self._mount = session, mount
            del snapshot, configuration
        try:
            yield session
        finally:
            with CancelScope(shield=True):
                async with self._lock:
                    try:
                        await lifetime.aclose()
                    finally:
                        self._users -= 1
                        if self._users == 0:
                            self._session, self._mount = None, None


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
        configuration: SessionConfigurationSource,
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
