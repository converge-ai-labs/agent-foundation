from __future__ import annotations

import asyncio
import math
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from importlib.metadata import PackageNotFoundError, version
from types import TracebackType

from a13n_envd_client.eip.v1 import (
    METHODS,
    ComputerRegion,
    DeviceDescribeParams,
    DeviceDescriptor,
    DeviceMetricsParams,
    DeviceMetricsResult,
    DirectoryListParams,
    DirectoryListResult,
    DisabledSandbox,
    EgressMode,
    EIPCallContext,
    EIPClient,
    EIPClientInfo,
    EIPPath,
    EnvironmentDescribeParams,
    EnvironmentReadinessParams,
    EnvironmentReadinessResult,
    ErrorType,
    FileByteRange,
    FileWriteMode,
    InitializeParams,
    OutputInfo,
    OutputReference,
    SessionAttachParams,
    SessionCloseParams,
    SessionConfiguration,
    SessionDescriptor,
    SessionDetachParams,
    SessionKeepaliveParams,
    SessionOpenParams,
    VmIsolation,
)
from a13n_envd_client.errors import EIPClientError, EIPMethodError, EIPProtocolError, EIPSessionStateError
from a13n_envd_client.file_transfer import (
    EIPComputerObservationReader,
    EIPComputerZoomReader,
    EIPFileReader,
    EIPFileWriter,
)
from a13n_envd_client.output import EIPOutputReader
from a13n_envd_client.requester import RequestCoordinator, SessionRequester
from a13n_envd_client.transport import EIPTransport


class EIPDeviceConnection:
    """Initialized Device carrier. Only this owner closes the physical transport."""

    def __init__(self, requester: RequestCoordinator, descriptor: DeviceDescriptor, *, max_in_flight: int) -> None:
        self._requester = requester
        self._client = EIPClient(requester)
        self._descriptor = descriptor
        self._max_in_flight = max_in_flight
        self._sessions: dict[str, EIPSession] = {}
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    async def initialize(
        cls,
        transport: EIPTransport,
        *,
        expected_device_id: str | None,
        client_name: str = "a13n-envd-client",
        client_version: str | None = None,
        initialization_timeout: float = 10.0,
        request_timeout: float | None = None,
        max_in_flight: int = 32,
    ) -> EIPDeviceConnection:
        """Use ``None`` only for explicit first-contact Device registration."""
        _finite_timeout_ms(initialization_timeout, name="initialization_timeout")
        if not isinstance(max_in_flight, int) or isinstance(max_in_flight, bool) or max_in_flight < 1:
            raise ValueError("max_in_flight must be a positive integer")
        requester = RequestCoordinator(transport, request_timeout=request_timeout)
        try:
            async with asyncio.timeout(initialization_timeout):
                result = await EIPClient(requester).initialize(
                    InitializeParams(
                        client=EIPClientInfo(name=client_name, version=client_version or _distribution_version()),
                        expected_device_id=expected_device_id,
                    )
                )
                descriptor = result.descriptor
                _validate_methods(descriptor.available_methods)
                if expected_device_id is not None and descriptor.device_id != expected_device_id:
                    raise EIPProtocolError("server returned a different Device identity")
                requester.configure_limits(descriptor.limits)
                return cls(requester, descriptor, max_in_flight=max_in_flight)
        except BaseException:
            await requester.close()
            raise

    @property
    def descriptor(self) -> DeviceDescriptor:
        return self._descriptor

    @property
    def is_closed(self) -> bool:
        """Whether this connection is terminal, not a network liveness probe."""
        return self._closed or self._requester.is_closed

    async def describe(self) -> DeviceDescriptor:
        self._ensure_open()
        result = await self._client.device_describe(DeviceDescribeParams())
        observed = result.descriptor
        if (observed.device_id, observed.generation) != (self._descriptor.device_id, self._descriptor.generation):
            await self.close()
            raise EIPProtocolError("Device identity or generation changed on its connection")
        if observed.boundary != self._descriptor.boundary:
            await self.close()
            raise EIPProtocolError("Device execution boundary changed on its connection")
        _validate_methods(observed.available_methods)
        self._descriptor = observed
        return observed

    async def metrics(self) -> DeviceMetricsResult:
        """Read the latest Device sample without opening a Session or triggering collection."""
        self._ensure_open()
        if "device.metrics" not in self._descriptor.available_methods:
            raise EIPSessionStateError("Device does not advertise device.metrics")
        result = await self._client.device_metrics(
            DeviceMetricsParams(
                expected_device_id=self._descriptor.device_id,
                expected_generation=self._descriptor.generation,
            )
        )
        if (result.device_id, result.generation) != (self._descriptor.device_id, self._descriptor.generation):
            await self.close()
            raise EIPProtocolError("Device identity or generation changed on its connection")
        return result

    async def list_directories(self, params: DirectoryListParams) -> DirectoryListResult:
        self._ensure_open()
        return await self._client.directory_list(params)

    async def open_session(
        self,
        *,
        configuration: SessionConfiguration | None = None,
        required_methods: tuple[str, ...] = (),
        readiness_timeout: float = 10.0,
    ) -> EIPSession:
        self._ensure_open()
        _finite_timeout_ms(readiness_timeout, name="readiness_timeout")
        configuration = configuration or SessionConfiguration()
        mode = configuration.egress_mode or self._descriptor.boundary.egress
        controlled = mode == EgressMode.CONTROLLED
        if (configuration.egress is not None) != controlled:
            raise ValueError("Session egress policy is required exactly for controlled Sessions")
        required = tuple(
            dict.fromkeys((*required_methods, "environment.readiness", *(("egress.update",) if controlled else ())))
        )
        try:
            session = await self._requester.open_session(
                SessionOpenParams(
                    expected_device_id=self._descriptor.device_id,
                    expected_generation=self._descriptor.generation,
                    configuration=configuration,
                    required_methods=required,
                ),
                self._bind,
            )
        except EIPMethodError as error:
            if error.error.data.error_type in {ErrorType.STALE_GENERATION, ErrorType.PROTOCOL_INCOMPATIBLE}:
                # The observed Device contract no longer matches. End its
                # Sessions, but never retry this open or migrate operations.
                await self.close()
            raise
        try:
            self._validate_session_boundary(session.descriptor)
            if session.descriptor.boundary.egress != mode:
                raise EIPProtocolError("Session network mode differs from its configuration")
            vm = configuration.isolation if isinstance(configuration.isolation, VmIsolation) else None
            if session.descriptor.boundary.vm != vm:
                raise EIPProtocolError("Session VM isolation differs from its configuration")
            sandbox = configuration.sandbox or (
                DisabledSandbox(mode="disabled") if vm else self._descriptor.boundary.sandbox
            )
            if session.descriptor.boundary.sandbox.mode != sandbox.mode:
                raise EIPProtocolError("Session sandbox mode differs from its configuration")
            if configuration.lifecycle is not None and session.descriptor.lifecycle != configuration.lifecycle:
                raise EIPProtocolError("Session lifecycle differs from its configuration")
            if configuration.commands is False and "process.start" in session.descriptor.available_methods:
                raise EIPProtocolError("Session enabled commands disabled by its configuration")
            missing = set(required) - set(session.descriptor.available_methods)
            if missing:
                raise EIPProtocolError(f"Session omitted required method: {min(missing)}")
            await session._become_ready(readiness_timeout)
            return session
        except BaseException:
            await session.abort()
            raise

    async def attach_session(
        self, descriptor: SessionDescriptor, *, owner_epoch: int | None = None, readiness_timeout: float = 10.0
    ) -> EIPSession:
        """Explicit same-Session/generation attachment; never creates or replays work."""
        self._ensure_open()
        epoch = descriptor.owner_epoch + 1 if owner_epoch is None else owner_epoch
        if epoch <= descriptor.owner_epoch:
            raise ValueError("attachment requires a newer Session owner epoch")
        _finite_timeout_ms(readiness_timeout, name="readiness_timeout")
        await self._requester.wait_session_released(descriptor.session_id, owner_epoch=epoch, timeout=readiness_timeout)
        session = self._bind(descriptor.model_copy(update={"owner_epoch": epoch}))
        try:
            result = await session._client.session_attach(SessionAttachParams())
            _validate_descriptor_refresh(session.descriptor, result.descriptor)
            self._validate_session_boundary(result.descriptor)
            session._descriptor = result.descriptor
            await session._become_ready(readiness_timeout)
            return session
        except BaseException:
            session._finish(EIPSessionStateError("Session attachment failed"))
            raise

    async def bind_open_session(
        self,
        descriptor: SessionDescriptor,
        *,
        readiness_timeout: float = 10,
        owner_current: Callable[[], Awaitable[bool]] | None = None,
    ) -> EIPSession:
        """Adopt a Session already opened by the authenticated Host on this logical carrier.

        A Host that routes execution across processes supplies the confirmed Device
        and Session descriptors. Check their identity, boundary and live readiness
        before exposing operations; never open or attach a second native Session.
        """
        _finite_timeout_ms(readiness_timeout, name="readiness_timeout")
        self._validate_session_boundary(descriptor)
        session = self._bind(descriptor)
        session._host_binding = True
        session._owner_current = owner_current
        try:
            # The authenticated owner already maintains native Session liveness.
            await session._become_ready(readiness_timeout)
            return session
        except BaseException:
            await session.abort()
            raise

    def _validate_session_boundary(self, descriptor: SessionDescriptor) -> None:
        controlled = descriptor.boundary.egress == EgressMode.CONTROLLED
        if (descriptor.egress is not None) != controlled:
            raise EIPProtocolError("Session egress status differs from its network mode")

    def _bind(self, descriptor: SessionDescriptor) -> EIPSession:
        self._ensure_open()
        if (descriptor.device_id, descriptor.generation) != (self._descriptor.device_id, self._descriptor.generation):
            raise EIPProtocolError("Session does not belong to this Device generation")
        _validate_descriptor_structure(descriptor)
        requester = self._requester.session(
            descriptor.session_id,
            limits=descriptor.limits,
            max_in_flight=self._max_in_flight,
            owner_epoch=descriptor.owner_epoch,
        )
        session = EIPSession(self, requester, descriptor)
        self._sessions[descriptor.session_id] = session
        return session

    def _ensure_open(self) -> None:
        if self._closed:
            raise EIPSessionStateError("Device connection is closed")

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close(), name="eip-device-connection-close")
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        sessions = tuple(self._sessions.values())
        await asyncio.gather(*(session.abort() for session in sessions))
        await self._requester.close()

    async def __aenter__(self) -> EIPDeviceConnection:
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()


class EIPSession:
    """Independent, fixed-cwd scope with its own keepalive and native cleanup."""

    def __init__(self, device: EIPDeviceConnection, requester: SessionRequester, descriptor: SessionDescriptor) -> None:
        self._device = device
        self._requester = requester
        self._client = EIPClient(requester)
        self._descriptor = descriptor
        self._ready = False
        self._host_binding = False
        self._owner_current: Callable[[], Awaitable[bool]] | None = None
        self._detached = False
        self._closed = False
        self._error: BaseException | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._users = 0

    @property
    def is_closed(self) -> bool:
        """Whether this client incarnation or its carrier has finished."""
        return self._closed or self._device.is_closed

    @property
    def client(self) -> EIPClient:
        self._ensure_open()
        return self._client

    @property
    def requester(self) -> SessionRequester:
        """Session-scoped requests and bounded transfer channels for trusted Host bridges.

        Host bridges authorize each operation before using this interface. The
        requester retains the normal correlation, admission and Session fencing;
        it never permits Device-scoped methods or selecting another Session.
        The EIPSession remains responsible for keepalive and final close.
        """
        self._ensure_open()
        return self._requester

    @property
    def descriptor(self) -> SessionDescriptor:
        return self._descriptor

    @property
    def detached(self) -> bool:
        return self._detached

    def detach_host_binding(self) -> None:
        """Release only this logical binding after the Host confirms loss of its carrier.

        Native processes and transfers remain subject to daemon disconnect/idle
        policy. This is neither a successful remote close nor permission to replay.
        """
        if not self._host_binding:
            raise EIPSessionStateError("Only a Host-owned logical binding can detach locally")
        self._detached = True
        self._finish()

    async def prepare_close(self) -> bool:
        """Whether native cleanup still belongs to this carrier."""
        if (
            self._close_task is None
            and not self._detached
            and self._owner_current is not None
            and not await self._owner_current()
        ):
            self.detach_host_binding()
        return not self._detached

    async def prepare_cleanup(self) -> bool:
        """Retire local observations when their original carrier is confirmed lost.

        This explicit teardown path does not certify a remote Session close.
        Ordinary close() still reports an unexpectedly disconnected Device.
        """
        if not await self.prepare_close():
            return False
        if self._close_task is None and self._device.is_closed:
            # Preserve failures of an attempted close; a caller must not convert
            # that failure into successful cleanup by calling this afterwards.
            self._detached = True
            self._finish()
        return not self._detached

    @property
    def device_descriptor(self) -> DeviceDescriptor:
        """The connected target's metadata, without issuing another request."""
        return self._device.descriptor

    @property
    def generation(self) -> int:
        return self._descriptor.generation

    @property
    def session_id(self) -> str:
        return self._descriptor.session_id

    async def _become_ready(self, timeout: float) -> None:
        result = await self.readiness(timeout=timeout)
        if not result.ready:
            raise EIPSessionStateError("Session is not ready")
        self._ready = True

    @asynccontextmanager
    async def use(self) -> AsyncIterator[EIPSession]:
        """Keep the Session live only while a Run or retained execution uses it."""
        self._acquire()
        try:
            yield self
        finally:
            self._users -= 1
            if self._users == 0:
                self._stop_keepalive()
                if not self.is_closed and not self._host_binding:
                    # A final touch only extends the idle window; transport failure
                    # must not replace the caller's result or exception.
                    with suppress(EIPClientError, TimeoutError):
                        async with asyncio.timeout(5.0):
                            await self._client.session_keepalive(SessionKeepaliveParams())

    def _acquire(self) -> None:
        self._ensure_open()
        if self._users == 0 and not self._host_binding:
            self._keepalive_task = asyncio.create_task(self._keepalive(), name="eip-session-keepalive")
        self._users += 1

    async def _keepalive(self) -> None:
        interval = self._descriptor.lifecycle.idle_timeout_ms / 3000
        try:
            while not self._closed:
                try:
                    async with asyncio.timeout(interval):
                        error = await self._requester.wait_finished()
                except TimeoutError:
                    pass
                else:
                    self._finish(error)
                    return
                async with asyncio.timeout(interval):
                    await self._client.session_keepalive(SessionKeepaliveParams())
        except asyncio.CancelledError:
            return
        except (EIPClientError, TimeoutError) as error:
            self._finish(error)

    async def readiness(self, *, timeout: float = 10.0) -> EnvironmentReadinessResult:
        if self._closed:
            raise EIPSessionStateError("Session is closed")
        timeout_ms = _finite_timeout_ms(timeout, name="timeout")
        async with asyncio.timeout(timeout):
            result = await self._client.environment_readiness(
                EnvironmentReadinessParams(
                    context=EIPCallContext(operation_id=_operation_id(), timeout_ms=timeout_ms),
                )
            )
        if (result.device_id, result.generation, result.session_id) != (
            self._descriptor.device_id,
            self.generation,
            self.session_id,
        ):
            error = EIPProtocolError("readiness returned a different Session identity")
            await self._requester.close_for_protocol_error(error)
            self._finish(error)
            raise error
        if not result.ready:
            await self.abort()
        return result

    async def describe(self) -> SessionDescriptor:
        self._ensure_open()
        result = await self._client.environment_describe(
            EnvironmentDescribeParams(context=EIPCallContext(operation_id=_operation_id()))
        )
        try:
            _validate_descriptor_refresh(self._descriptor, result.descriptor)
        except EIPProtocolError as error:
            await self._requester.close_for_protocol_error(error)
            self._finish(error)
            raise
        self._requester.narrow_limits(result.descriptor.limits)
        self._descriptor = result.descriptor
        return self._descriptor

    def open_reader(
        self, path: EIPPath, *, byte_range: FileByteRange | None = None, transfer_timeout_ms: int | None = None
    ) -> EIPFileReader:
        self._require_method("file.open_reader")
        return EIPFileReader(
            self._requester, self._client, path, byte_range=byte_range, transfer_timeout_ms=transfer_timeout_ms
        )

    def observe_computer(
        self, *, target_id: str | None = None, max_dimension: int | None = None
    ) -> EIPComputerObservationReader:
        self._require_method("computer.observe")
        self._require_method("computer.close_observation")
        return EIPComputerObservationReader(
            self._requester, self._client, target_id=target_id, max_dimension=max_dimension
        )

    def zoom_computer(
        self, *, observation_id: str, region: ComputerRegion, max_dimension: int = 1280
    ) -> EIPComputerZoomReader:
        self._require_method("computer.zoom")
        self._require_method("computer.close_observation")
        return EIPComputerZoomReader(
            self._requester, self._client, observation_id=observation_id, region=region, max_dimension=max_dimension
        )

    def open_writer(
        self,
        path: EIPPath,
        *,
        mode: FileWriteMode | str,
        executable: bool | None = None,
        transfer_timeout_ms: int | None = None,
    ) -> EIPFileWriter:
        self._require_method("file.open_writer")
        return EIPFileWriter(
            self._requester,
            self._client,
            path,
            mode if isinstance(mode, FileWriteMode) else FileWriteMode(mode),
            executable=executable,
            transfer_timeout_ms=transfer_timeout_ms,
            max_transfer_frame_bytes=self._descriptor.limits.max_transfer_frame_bytes,
        )

    def open_output(
        self, reference: OutputReference | str, *, start_offset: int = 0, observed: OutputInfo | None = None
    ) -> EIPOutputReader:
        self._require_method("output.read")
        return EIPOutputReader(
            self._requester,
            self._client,
            reference if isinstance(reference, OutputReference) else OutputReference(reference),
            start_offset=start_offset,
            observed=observed,
        )

    async def detach(self) -> None:
        """Release this client without destroying native Session resources."""
        if not await self.prepare_close():
            return
        if self._close_task is None:
            if self._closed:
                return
            self._closed = True
            self._requester.begin_close()
            self._stop_keepalive()
            self._close_task = asyncio.create_task(self._detach(), name="eip-session-detach")
        await asyncio.shield(self._close_task)

    async def _detach(self) -> None:
        try:
            async with asyncio.timeout(5.0):
                await self._client.session_detach(SessionDetachParams())
        except BaseException as error:
            self._finish(error)
            raise
        self._finish()

    async def close(self) -> None:
        if not await self.prepare_close():
            return
        if self._close_task is None:
            if self._closed:
                if self._error is not None:
                    raise EIPSessionStateError("Session did not close cleanly") from self._error
                return
            self._closed = True
            self._requester.begin_close()
            self._stop_keepalive()
            self._close_task = asyncio.create_task(self._close(), name="eip-session-close")
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        try:
            async with asyncio.timeout(5.0):
                await self._client.session_close(SessionCloseParams())
        except BaseException as error:
            self._finish(error)
            raise
        self._finish()

    async def abort(self) -> None:
        """Best-effort Session cleanup, never close or replay on the shared Device."""
        try:
            await self.close()
        except (EIPClientError, TimeoutError):
            pass

    def _finish(self, error: BaseException | None = None) -> None:
        self._closed = True
        self._error = error
        self._stop_keepalive()
        self._requester.finish(error)
        self._device._sessions.pop(self.session_id, None)

    def _stop_keepalive(self) -> None:
        task = self._keepalive_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _ensure_open(self) -> None:
        if self._closed or not self._ready:
            raise EIPSessionStateError("Session is closed or not ready") from self._error

    def _require_method(self, method: str) -> None:
        self._ensure_open()
        if method not in self._descriptor.available_methods:
            raise EIPSessionStateError(f"EIP method is not available: {method}")

    async def __aenter__(self) -> EIPSession:
        self._acquire()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._users -= 1
        if exception_type is None:
            await self.close()
        else:
            await self.abort()


def _finite_timeout_ms(timeout: float, *, name: str) -> int:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"{name} must be positive and finite")
    timeout_ms = max(1, math.ceil(timeout * 1000))
    if timeout_ms > 2**64 - 1:
        raise ValueError(f"{name} is too large")
    return timeout_ms


def _validate_methods(methods: tuple[str, ...]) -> None:
    if len(set(methods)) != len(methods):
        raise EIPProtocolError("descriptor contains duplicate available methods")
    unknown = set(methods) - METHODS.keys()
    if unknown:
        raise EIPProtocolError(f"descriptor contains unknown method: {min(unknown)}")


def _validate_descriptor_structure(descriptor: SessionDescriptor) -> None:
    _validate_methods(descriptor.available_methods)
    profiles = {profile.profile_id for profile in descriptor.shell_profiles}
    if len(profiles) != len(descriptor.shell_profiles):
        raise EIPProtocolError("descriptor contains duplicate shell profile IDs")
    features = descriptor.execution_features
    if (features.signal_interrupt or features.signal_terminate) != ("process.signal" in descriptor.available_methods):
        raise EIPProtocolError("descriptor process signal method and features disagree")


def _validate_descriptor_refresh(previous: SessionDescriptor, observed: SessionDescriptor) -> None:
    _validate_descriptor_structure(observed)
    old = previous.model_dump()
    new = observed.model_dump()
    for field in (
        "device_id",
        "generation",
        "session_id",
        "owner_epoch",
        "working_directory",
        "path_style",
        "system_info",
        "boundary",
        "shell_profiles",
        "execution_features",
        "lifecycle",
    ):
        if old[field] != new[field]:
            raise EIPProtocolError(f"Session {field} changed")
    if not set(observed.available_methods).issubset(previous.available_methods):
        raise EIPProtocolError("available methods widened within a Session")
    old_limits = previous.limits.model_dump()
    if any(value > old_limits[name] for name, value in observed.limits.model_dump().items()):
        raise EIPProtocolError("limits widened within a Session")


def _operation_id() -> str:
    return f"op-{secrets.token_urlsafe(9)}"


def _distribution_version() -> str:
    try:
        return version("a13n-envd-client")
    except PackageNotFoundError:
        return "0.0.0"
