"""Host-owned Envd Session continuity; acquisition never replays operations."""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from a13n_envd_client import EIPDeviceConnection, EIPSession, EIPSessionStateError
from a13n_envd_client.eip.v1 import EgressPolicy, ErrorType, SessionDescriptor
from a13n_envd_client.errors import EIPMethodError
from anyio import CancelScope
from pydantic import BaseModel, ConfigDict, Field

from a13n_harness.environment.envd_policy import EnvdBoundaryRequirement


class EnvdSessionState(BaseModel):
    """Host-private restoration metadata, separate from credentials and portable state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str
    descriptor: SessionDescriptor = Field(repr=False)
    working_directory: str | None = None
    policy_digest: str | None = None


@dataclass(slots=True, weakref_slot=True)
class EnvdSessionRecovery:
    """One logical context shared by compatible Runs and their retained work.

    The Host keys contexts by Thread and binding, and persists state before exposing
    an acquisition. Live borrowers share one client; the final release detaches it.
    """

    state: EnvdSessionState | None = field(default=None, repr=False)
    checkpoint: Callable[[EnvdSessionState], Awaitable[None]] | None = field(default=None, repr=False)
    _session: EIPSession | None = field(default=None, init=False, repr=False)
    _users: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @asynccontextmanager
    async def borrow(
        self,
        device: EIPDeviceConnection,
        *,
        target: str,
        expected_device_id: str,
        working_directory: str | None,
        required_methods: frozenset[str],
        expected_boundary: EnvdBoundaryRequirement | None,
        egress: EgressPolicy | None = None,
    ) -> AsyncIterator[EIPSession]:
        policy_digest = None if egress is None else hashlib.sha256(egress.model_dump_json().encode()).hexdigest()
        async with self._lock:
            if device.descriptor.device_id != expected_device_id:
                raise EIPSessionStateError("Session context belongs to another Device")
            if expected_boundary is not None:
                expected_boundary.check(device.descriptor.boundary)
            compatible = self._compatible(device, target, working_directory, required_methods, policy_digest)
            if self._session is not None and self._session.is_closed:
                self._session = None
                self._users = 0
            if self._session is not None:
                if not compatible:
                    raise EIPSessionStateError("Active Session context has an incompatible binding")
                session = self._session
            else:
                session = None
                previous = self.state
                if compatible and previous is not None:
                    # Reserve the next epoch durably before takeover. An uncertain
                    # response retains this reservation; a later attempt advances it.
                    epoch = previous.descriptor.owner_epoch + 1
                    await self._save(
                        previous.model_copy(
                            update={"descriptor": previous.descriptor.model_copy(update={"owner_epoch": epoch})}
                        )
                    )
                    try:
                        session = await device.attach_session(previous.descriptor, owner_epoch=epoch)
                    except EIPMethodError as error:
                        if error.error.data.error_type != ErrorType.NOT_INITIALIZED:
                            raise
                if session is None:
                    session = await device.open_session(
                        working_directory=working_directory,
                        required_methods=tuple(sorted(required_methods)),
                        egress=egress,
                    )
                try:
                    await self._save(
                        EnvdSessionState(
                            target=target,
                            descriptor=session.descriptor,
                            working_directory=working_directory,
                            policy_digest=policy_digest,
                        )
                    )
                except BaseException:
                    with CancelScope(shield=True):
                        await session.detach()
                    raise
                self._session = session
            self._users += 1
        try:
            async with session.use():
                yield session
        finally:
            with CancelScope(shield=True):
                async with self._lock:
                    # A dead carrier may have been replaced while this borrower
                    # was still running. Its release cannot detach the new owner.
                    if self._session is session:
                        self._users -= 1
                        if self._users == 0:
                            self._session = None
                            await session.detach()

    def _compatible(
        self,
        device: EIPDeviceConnection,
        target: str,
        working_directory: str | None,
        required_methods: frozenset[str],
        policy_digest: str | None,
    ) -> bool:
        previous = self.state
        if previous is None:
            return False
        descriptor = previous.descriptor
        if (
            previous.target != target
            or descriptor.device_id != device.descriptor.device_id
            or descriptor.generation != device.descriptor.generation
            or previous.working_directory != working_directory
            or previous.policy_digest != policy_digest
        ):
            return False
        if descriptor.boundary != device.descriptor.boundary:
            raise EIPSessionStateError("Recovered Session execution boundary changed")
        return not (required_methods - set(descriptor.available_methods))

    async def _save(self, state: EnvdSessionState) -> None:
        if self.checkpoint is not None:
            await self.checkpoint(state)
        self.state = state
