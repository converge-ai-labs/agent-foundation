from __future__ import annotations

import asyncio
import secrets
from importlib.metadata import PackageNotFoundError, version
from types import TracebackType
from typing import Never

from a13n_envd_client.eip.v1 import (
    EIP_PROTOCOL_VERSION,
    METHODS,
    EIPCallContext,
    EIPClient,
    EIPClientInfo,
    EIPPath,
    EnvironmentDescribeParams,
    EnvironmentDescriptor,
    FileByteRange,
    FileWriteMode,
    InitializeParams,
    OutputInfo,
    OutputReference,
    SessionCloseParams,
)
from a13n_envd_client.errors import EIPProtocolError, EIPSessionStateError
from a13n_envd_client.file_transfer import EIPFileReader, EIPFileWriter
from a13n_envd_client.output import EIPOutputReader
from a13n_envd_client.requester import RequestCoordinator
from a13n_envd_client.transport import EIPTransport


class EIPSession:
    """One initialized EIP session over a low-level transport."""

    def __init__(
        self,
        requester: RequestCoordinator,
        descriptor: EnvironmentDescriptor,
    ) -> None:
        self._requester = requester
        self._client = EIPClient(requester)
        self._descriptor = descriptor
        self._describe_lock = asyncio.Lock()
        self._closed = False

    @classmethod
    async def initialize(
        cls,
        transport: EIPTransport,
        *,
        expected_environment_id: str,
        required_methods: tuple[str, ...] = (),
        client_name: str = "a13n-envd-client",
        client_version: str | None = None,
        initialization_timeout: float = 10.0,
        request_timeout: float | None = None,
        max_in_flight: int = 32,
    ) -> EIPSession:
        if initialization_timeout <= 0:
            raise ValueError("initialization_timeout must be positive")
        if not isinstance(max_in_flight, int) or isinstance(max_in_flight, bool) or max_in_flight < 1:
            raise ValueError("max_in_flight must be a positive integer")
        requester = RequestCoordinator(
            transport,
            max_in_flight=1,
            request_timeout=request_timeout,
        )
        client = EIPClient(requester)
        params = InitializeParams(
            supported_protocol_versions=(EIP_PROTOCOL_VERSION,),
            client=EIPClientInfo(
                name=client_name,
                version=client_version or _distribution_version(),
            ),
            expected_environment_id=expected_environment_id,
            required_methods=required_methods,
        )
        try:
            async with asyncio.timeout(initialization_timeout):
                result = await client.initialize(params)
            if result.protocol_version != EIP_PROTOCOL_VERSION:
                raise EIPProtocolError("server selected an unoffered EIP protocol version")
            descriptor = result.descriptor
            _validate_descriptor_structure(descriptor)
            if descriptor.environment_id != expected_environment_id:
                raise EIPProtocolError("server returned a different Environment identity")
            missing = sorted(set(required_methods) - set(descriptor.available_methods))
            if missing:
                raise EIPProtocolError(f"server omitted required method: {missing[0]}")
            requester.configure_limits(
                max_in_flight=min(max_in_flight, descriptor.limits.max_concurrent_operations),
                max_request_bytes=descriptor.limits.max_request_bytes,
                max_response_bytes=descriptor.limits.max_response_bytes,
                max_transfer_frame_bytes=descriptor.limits.max_transfer_frame_bytes,
                max_concurrent_file_transfers=descriptor.limits.max_concurrent_file_transfers,
            )
            return cls(requester, descriptor)
        except BaseException:
            await requester.close()
            raise

    @property
    def client(self) -> EIPClient:
        """The generated typed client bound to this initialized session."""
        self._ensure_open()
        return self._client

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._descriptor

    @property
    def generation(self) -> int:
        return self._descriptor.generation

    def open_reader(
        self,
        path: EIPPath,
        *,
        byte_range: FileByteRange | None = None,
        transfer_timeout_ms: int | None = None,
    ) -> EIPFileReader:
        self._ensure_open()
        self._require_method("file.open_reader")
        return EIPFileReader(
            self._requester,
            self._client,
            path,
            byte_range=byte_range,
            transfer_timeout_ms=transfer_timeout_ms,
        )

    def open_writer(
        self,
        path: EIPPath,
        *,
        mode: FileWriteMode | str,
        executable: bool | None = None,
        transfer_timeout_ms: int | None = None,
    ) -> EIPFileWriter:
        self._ensure_open()
        self._require_method("file.open_writer")
        resolved_mode = mode if isinstance(mode, FileWriteMode) else FileWriteMode(mode)
        return EIPFileWriter(
            self._requester,
            self._client,
            path,
            resolved_mode,
            executable=executable,
            transfer_timeout_ms=transfer_timeout_ms,
            max_transfer_frame_bytes=self._descriptor.limits.max_transfer_frame_bytes,
        )

    def open_output(
        self,
        reference: OutputReference | str,
        *,
        start_offset: int = 0,
        observed: OutputInfo | None = None,
    ) -> EIPOutputReader:
        self._ensure_open()
        self._require_method("output.read")
        resolved_reference = reference if isinstance(reference, OutputReference) else OutputReference(reference)
        return EIPOutputReader(
            self._requester,
            self._client,
            resolved_reference,
            start_offset=start_offset,
            observed=observed,
        )

    async def describe(self) -> EnvironmentDescriptor:
        self._ensure_open()
        async with self._describe_lock:
            self._ensure_open()
            result = await self._client.environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id=_operation_id()))
            )
            descriptor = result.descriptor
            try:
                _validate_descriptor_refresh(self._descriptor, descriptor)
            except EIPProtocolError as error:
                await self._terminate_protocol_error(error)
            self._requester.narrow_limits(
                max_in_flight=descriptor.limits.max_concurrent_operations,
                max_request_bytes=descriptor.limits.max_request_bytes,
                max_response_bytes=descriptor.limits.max_response_bytes,
                max_transfer_frame_bytes=descriptor.limits.max_transfer_frame_bytes,
                max_concurrent_file_transfers=descriptor.limits.max_concurrent_file_transfers,
            )
            self._descriptor = descriptor
            return descriptor

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._client.session_close(SessionCloseParams(context=EIPCallContext(operation_id=_operation_id())))
        finally:
            await self._requester.close()

    async def abort(self) -> None:
        """Close the carrier without claiming an in-flight operation outcome."""
        if self._closed:
            return
        self._closed = True
        await self._requester.close()

    async def __aenter__(self) -> EIPSession:
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exception_type is None:
            await self.close()
            return
        try:
            await self.close()
        except BaseException:
            pass

    async def _terminate_protocol_error(self, error: EIPProtocolError) -> Never:
        self._closed = True
        await self._requester.close()
        raise error

    def _ensure_open(self) -> None:
        if self._closed:
            raise EIPSessionStateError("EIP session is closed")

    def _require_method(self, method: str) -> None:
        if method not in self._descriptor.available_methods:
            raise EIPSessionStateError(f"EIP method is not available: {method}")


def _validate_descriptor_structure(descriptor: EnvironmentDescriptor) -> None:
    methods = set(descriptor.available_methods)
    if len(methods) != len(descriptor.available_methods):
        raise EIPProtocolError("descriptor contains duplicate available methods")
    unknown_methods = methods - METHODS.keys()
    if unknown_methods:
        raise EIPProtocolError(f"descriptor contains unknown method: {min(unknown_methods)}")
    mount_ids = {mount.mount_id for mount in descriptor.mounts}
    if len(mount_ids) != len(descriptor.mounts):
        raise EIPProtocolError("descriptor contains duplicate mount IDs")
    if descriptor.root_mount_id is not None and descriptor.root_mount_id not in mount_ids:
        raise EIPProtocolError("server returned an unknown root mount")
    profile_ids = {profile.profile_id for profile in descriptor.shell_profiles}
    if len(profile_ids) != len(descriptor.shell_profiles):
        raise EIPProtocolError("descriptor contains duplicate shell profile IDs")
    features = descriptor.execution_features
    signals_available = features.signal_interrupt or features.signal_terminate
    if signals_available != ("process.signal" in methods):
        raise EIPProtocolError("descriptor process signal method and features disagree")
    command_features = (
        features.process_count_limit
        or features.memory_bytes_limit
        or features.cpu_time_limit
        or features.per_command_network_deny
    )
    if command_features and not ({"shell.exec", "process.start"} & methods):
        raise EIPProtocolError("descriptor advertises command features without a command method")


def _validate_descriptor_refresh(
    previous: EnvironmentDescriptor,
    observed: EnvironmentDescriptor,
) -> None:
    _validate_descriptor_structure(observed)
    if observed.environment_id != previous.environment_id:
        raise EIPProtocolError("Environment identity changed within an EIP session")
    if observed.generation != previous.generation:
        raise EIPProtocolError("Environment generation changed within an EIP session")
    if observed.root_mount_id != previous.root_mount_id or observed.mounts != previous.mounts:
        raise EIPProtocolError("mount topology changed within an EIP session")
    if observed.shell_profiles != previous.shell_profiles:
        raise EIPProtocolError("shell profiles changed within an EIP session")
    if observed.isolation != previous.isolation:
        raise EIPProtocolError("isolation posture changed within an EIP session")
    if observed.execution_features != previous.execution_features:
        raise EIPProtocolError("execution features changed within an EIP session")
    if not set(observed.available_methods).issubset(previous.available_methods):
        raise EIPProtocolError("available methods widened within an EIP session")
    current = previous.limits
    refreshed = observed.limits
    if (
        refreshed.max_request_bytes > current.max_request_bytes
        or refreshed.max_response_bytes > current.max_response_bytes
        or refreshed.max_concurrent_operations > current.max_concurrent_operations
        or refreshed.max_processes > current.max_processes
        or refreshed.max_operation_duration_ms > current.max_operation_duration_ms
        or refreshed.max_output_preview_bytes > current.max_output_preview_bytes
        or refreshed.max_output_bytes_per_stream > current.max_output_bytes_per_stream
        or refreshed.max_transfer_frame_bytes > current.max_transfer_frame_bytes
        or refreshed.max_concurrent_file_transfers > current.max_concurrent_file_transfers
        or refreshed.max_file_transfer_bytes > current.max_file_transfer_bytes
    ):
        raise EIPProtocolError("descriptor limits widened within an EIP session")


def _operation_id() -> str:
    return f"op-{secrets.token_urlsafe(9)}"


def _distribution_version() -> str:
    try:
        return version("a13n-envd-client")
    except PackageNotFoundError:
        return "0.0.0"
