"""Host-owned Envd Session continuity; acquisition never replays operations."""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal, Protocol

from a13n_envd_client import EIPDeviceConnection, EIPSession, EIPSessionStateError
from a13n_envd_client.eip.v1 import ErrorType, SessionConfiguration, SessionDescriptor
from a13n_envd_client.errors import EIPMethodError
from anyio import CancelScope
from pydantic import BaseModel, ConfigDict, Field

from a13n_harness.environment.envd_policy import EnvdBoundaryRequirement
from a13n_harness.environment.models import EnvironmentError

type SessionReplacementNotifier = Callable[[str], Awaitable[None]]
_SESSION_REPLACEMENT_NOTIFIER: ContextVar[SessionReplacementNotifier | None] = ContextVar(
    "envd_session_replacement_notifier", default=None
)


@contextmanager
def envd_session_notifications(notify: SessionReplacementNotifier) -> Iterator[None]:
    """Bind notices to the originating Run or invocation, including retained tasks."""
    token = _SESSION_REPLACEMENT_NOTIFIER.set(notify)
    try:
        yield
    finally:
        _SESSION_REPLACEMENT_NOTIFIER.reset(token)


class EnvdSessionState(BaseModel):
    """Host-private restoration metadata, separate from credentials and portable state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str
    descriptor: SessionDescriptor = Field(repr=False)
    configuration_digest: str
    status: Literal["active", "retiring", "retired"] = "active"


type SessionConfigurationSource = SessionConfiguration | Callable[[], Awaitable[SessionConfiguration]]


async def resolve_configuration(configuration: SessionConfigurationSource) -> SessionConfiguration:
    """Resolve startup inputs only when the owner needs ordinary acquisition."""
    return configuration if isinstance(configuration, SessionConfiguration) else await configuration()


class EnvdSessionBorrower(Protocol):
    def borrow(
        self,
        device: EIPDeviceConnection,
        *,
        target: str,
        expected_device_id: str,
        configuration: SessionConfigurationSource,
        required_methods: frozenset[str],
        expected_boundary: EnvdBoundaryRequirement | None,
    ) -> AbstractAsyncContextManager[EIPSession]: ...


@dataclass(frozen=True, slots=True)
class _ExactSession:
    owner: "EnvdSessionRecovery"
    session_id: str

    def borrow(
        self,
        device: EIPDeviceConnection,
        *,
        target: str,
        expected_device_id: str,
        configuration: SessionConfigurationSource,
        required_methods: frozenset[str],
        expected_boundary: EnvdBoundaryRequirement | None,
    ) -> AbstractAsyncContextManager[EIPSession]:
        return self.owner.borrow(
            device,
            target=target,
            expected_device_id=expected_device_id,
            configuration=configuration,
            required_methods=required_methods,
            expected_boundary=expected_boundary,
            original_session_id=self.session_id,
        )


@dataclass(slots=True, weakref_slot=True)
class EnvdSessionRecovery:
    """One logical context shared by compatible Runs and their retained work.

    The Host keys contexts by Thread and binding, reloads state under its ownership
    lock before borrowing, and persists checkpoints before acquisition is exposed.
    Live borrowers share one client; the final release detaches it.
    """

    state: EnvdSessionState | None = field(default=None, repr=False)
    checkpoint: Callable[[EnvdSessionState], Awaitable[None]] | None = field(default=None, repr=False)
    _scope: AsyncExitStack | None = field(default=None, init=False, repr=False)
    _session: EIPSession | None = field(default=None, init=False, repr=False)
    _users: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def exact(self, session_id: str) -> EnvdSessionBorrower:
        """Pin retained work without creating another owner, client or lock."""
        return _ExactSession(self, session_id)

    def _check_original(self, session_id: str | None) -> None:
        if session_id is not None and (
            self.state is None or self.state.status != "active" or self.state.descriptor.session_id != session_id
        ):
            raise EnvironmentError("The original Session is no longer current", code="environment_not_found")

    @asynccontextmanager
    async def borrow(
        self,
        device: EIPDeviceConnection,
        *,
        target: str,
        expected_device_id: str,
        configuration: SessionConfigurationSource,
        required_methods: frozenset[str],
        expected_boundary: EnvdBoundaryRequirement | None,
        original_session_id: str | None = None,
        configuration_identity: str | None = None,
    ) -> AsyncIterator[EIPSession]:
        replacement = None
        async with self._lock:
            if device.descriptor.device_id != expected_device_id:
                raise EIPSessionStateError("Session context belongs to another Device")
            self._check_original(original_session_id)
            if original_session_id is None:
                configuration = await resolve_configuration(configuration)
                digest = configuration_digest(configuration, identity=configuration_identity)
            else:
                # Exact recovery uses the original worker's immutable startup inputs.
                # Its current secret sources need not still exist, and cannot reconfigure it.
                assert self.state is not None
                digest = self.state.configuration_digest
            if self._session is not None and self._session.is_closed:
                with CancelScope(shield=True):
                    await self._release()
            if self._session is not None:
                if not self._compatible(device, target, digest):
                    raise EIPSessionStateError("Active Session context has an incompatible binding")
                session = self._session
            else:
                scope = AsyncExitStack()
                try:
                    compatible = self._compatible(device, target, digest)
                    session = None
                    previous = self.state
                    if previous is not None and previous.status == "retiring":
                        raise EIPSessionStateError("Session retirement must finish before acquisition")
                    if previous is not None and previous.status == "active" and previous.configuration_digest != digest:
                        raise EIPSessionStateError("Session configuration changed; retire the current binding first")
                    if compatible and previous is not None:
                        session = await self._attach(device, previous)
                    if session is None:
                        if original_session_id is not None:
                            raise EnvironmentError(
                                "The original Session is no longer available", code="environment_not_found"
                            )
                        assert isinstance(configuration, SessionConfiguration)
                        session = await device.open_session(
                            configuration=configuration,
                            required_methods=tuple(sorted(required_methods)),
                        )
                    scope.push_async_callback(session.detach)
                    await self._save(
                        EnvdSessionState(
                            target=target,
                            descriptor=session.descriptor,
                            configuration_digest=digest,
                        )
                    )
                except BaseException:
                    with CancelScope(shield=True):
                        await scope.aclose()
                    raise
                self._scope = scope
                self._session = session
                if previous is not None and previous.descriptor.session_id != session.session_id:
                    replacement = session.session_id
            try:
                if required_methods - set(session.descriptor.available_methods):
                    raise EIPSessionStateError("Session does not expose the required methods")
                if expected_boundary is not None:
                    expected_boundary.check(session.descriptor.boundary)
            except BaseException:
                if self._users == 0:
                    with CancelScope(shield=True):
                        await self._release()
                raise
            self._users += 1
        # Resolved values are startup inputs, not state retained for the borrower's lifetime.
        del configuration
        async with AsyncExitStack() as use_scope:
            try:
                if replacement is not None and (notify := _SESSION_REPLACEMENT_NOTIFIER.get()) is not None:
                    try:
                        await notify(replacement)
                    except Exception:
                        # A lifecycle hint must not undo an already checkpointed acquisition.
                        pass
                await use_scope.enter_async_context(session.use())
                yield session
            finally:
                with CancelScope(shield=True):
                    async with self._lock:
                        # A dead carrier may have been replaced while this borrower
                        # was still running. Its release cannot detach the new owner.
                        if self._session is session:
                            self._users -= 1
                            if self._users == 0:
                                # Detach refreshes native idle expiry itself. Release before
                                # use exits so it skips a redundant final keepalive round trip.
                                await self._release()

    async def _release(self) -> None:
        self._session = None
        self._users = 0
        scope, self._scope = self._scope, None
        if scope is not None:
            await scope.aclose()

    def _compatible(
        self,
        device: EIPDeviceConnection,
        target: str,
        digest: str,
    ) -> bool:
        previous = self.state
        if previous is None:
            return False
        descriptor = previous.descriptor
        if (
            previous.target != target
            or descriptor.device_id != device.descriptor.device_id
            or descriptor.generation != device.descriptor.generation
            or previous.configuration_digest != digest
            or previous.status != "active"
        ):
            return False
        return True

    async def retire(self, device: EIPDeviceConnection, *, target: str) -> None:
        """Close one incarnation durably before a successor can be opened.

        The Host settles retained work before calling this. A failed or uncertain
        close leaves a retiring reference that the next attempt must reconcile.
        """
        async with self._lock:
            if self._users:
                raise EIPSessionStateError("Session is busy; settle its borrowers before replacement")
            previous = self.state
            if previous is None or previous.status == "retired":
                return
            if previous.target != target or previous.descriptor.device_id != device.descriptor.device_id:
                raise EIPSessionStateError("Session retirement belongs to another Device")
            retiring = previous.model_copy(update={"status": "retiring"})
            await self._save(retiring)
            if previous.descriptor.generation == device.descriptor.generation:
                session = await self._attach(device, retiring)
                if session is not None:
                    try:
                        await session.close()
                    finally:
                        await session.detach()
            assert self.state is not None
            await self._save(self.state.model_copy(update={"status": "retired"}))

    async def _attach(self, device: EIPDeviceConnection, previous: EnvdSessionState) -> EIPSession | None:
        # Reserve the next epoch before takeover. An uncertain response retains
        # this reservation; a later attempt advances it without replaying work.
        epoch = previous.descriptor.owner_epoch + 1
        await self._save(
            previous.model_copy(update={"descriptor": previous.descriptor.model_copy(update={"owner_epoch": epoch})})
        )
        try:
            return await device.attach_session(previous.descriptor, owner_epoch=epoch)
        except EIPMethodError as error:
            if error.error.data.error_type != ErrorType.NOT_INITIALIZED:
                raise
            return None

    async def _save(self, state: EnvdSessionState) -> None:
        if self.checkpoint is not None:
            await self.checkpoint(state)
        self.state = state


def configuration_digest(configuration: SessionConfiguration, *, identity: str | None = None) -> str:
    """Hash the Host's nonsecret selection, or retain legacy resolved-configuration identity.

    A Host supplying identity must include every startup option and credential reference,
    but exclude credential values and their rotation versions. It owns synchronizing live
    credentials before exposing a recovered Session; borrowing alone never changes them.
    """
    value = configuration.model_dump_json() if identity is None else identity
    return hashlib.sha256(value.encode()).hexdigest()
