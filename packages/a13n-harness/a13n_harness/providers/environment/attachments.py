from __future__ import annotations

import asyncio
import ssl
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from a13n_envd_client import (
    AcceptedWebSocketTransport,
    EIPDeviceConnection,
    EIPSession,
    EIPSessionStateError,
    EIPTransport,
    HttpTransport,
    StdioTransport,
    WebSocketConnection,
)


class EIPSessionSource(ABC):
    """Single-use source for one independent, readiness-confirmed Session."""

    def __init__(self) -> None:
        self._claimed = False

    @abstractmethod
    def open_session(
        self,
        *,
        expected_device_id: str,
        required_methods: frozenset[str],
        working_directory: str | None = None,
    ) -> AbstractAsyncContextManager[EIPSession]: ...

    @abstractmethod
    async def discard(self) -> None:
        """Release an unentered source, not a borrowed Device connection."""

    def _claim(self) -> None:
        if self._claimed:
            raise RuntimeError("EIP Session source is single-use")
        self._claimed = True


class DeviceEIPSessionSource(EIPSessionSource):
    """Borrow a Host-owned Device connection; own only the newly opened Session."""

    def __init__(self, device: EIPDeviceConnection) -> None:
        super().__init__()
        self._device = device

    @asynccontextmanager
    async def open_session(
        self,
        *,
        expected_device_id: str,
        required_methods: frozenset[str],
        working_directory: str | None = None,
    ) -> AsyncGenerator[EIPSession]:
        self._claim()
        if self._device.descriptor.device_id != expected_device_id:
            raise EIPSessionStateError("Session source belongs to another Device")
        session = await self._device.open_session(
            working_directory=working_directory,
            required_methods=tuple(sorted(required_methods)),
        )
        async with session:
            yield session

    async def discard(self) -> None:
        self._claimed = True


class _OwnedTransportSessionSource(EIPSessionSource):
    """Standalone source: explicitly owns both its Device connection and Session."""

    def __init__(
        self,
        transport: EIPTransport,
        *,
        initialization_timeout: float,
        request_timeout: float | None,
        max_in_flight: int,
    ) -> None:
        super().__init__()
        self._transport = transport
        self._initialization_timeout = initialization_timeout
        self._request_timeout = request_timeout
        self._max_in_flight = max_in_flight

    @asynccontextmanager
    async def open_session(
        self,
        *,
        expected_device_id: str,
        required_methods: frozenset[str],
        working_directory: str | None = None,
    ) -> AsyncGenerator[EIPSession]:
        self._claim()
        device = await EIPDeviceConnection.initialize(
            self._transport,
            expected_device_id=expected_device_id,
            initialization_timeout=self._initialization_timeout,
            request_timeout=self._request_timeout,
            max_in_flight=self._max_in_flight,
        )
        async with device:
            async with DeviceEIPSessionSource(device).open_session(
                expected_device_id=expected_device_id,
                required_methods=required_methods,
                working_directory=working_directory,
            ) as session:
                yield session

    async def discard(self) -> None:
        if not self._claimed:
            self._claimed = True
            await self._transport.close()


class StdioEIPSessionSource(_OwnedTransportSessionSource):
    """Standalone process-pipe source; shared runtimes use DeviceEIPSessionSource."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        initialization_timeout: float = 10.0,
        request_timeout: float | None = None,
        max_in_flight: int = 32,
    ) -> None:
        super().__init__(
            StdioTransport.from_process(process),
            initialization_timeout=initialization_timeout,
            request_timeout=request_timeout,
            max_in_flight=max_in_flight,
        )


class HttpEIPSessionSource(_OwnedTransportSessionSource):
    def __init__(
        self,
        endpoint: str,
        credential: str,
        *,
        verify: ssl.SSLContext | str | bool = True,
        initialization_timeout: float = 10.0,
        request_timeout: float | None = 30.0,
        max_in_flight: int = 32,
        allow_plaintext_private_link: bool = False,
    ) -> None:
        super().__init__(
            HttpTransport(
                endpoint,
                credential,
                verify=verify,
                request_timeout=request_timeout or 30.0,
                allow_plaintext_private_link=allow_plaintext_private_link,
            ),
            initialization_timeout=initialization_timeout,
            request_timeout=request_timeout,
            max_in_flight=max_in_flight,
        )


class AcceptedWebSocketEIPSessionSource(_OwnedTransportSessionSource):
    """Standalone accepted connection; shared listeners use DeviceEIPSessionSource."""

    def __init__(
        self,
        connection: WebSocketConnection,
        *,
        initialization_timeout: float = 10.0,
        request_timeout: float | None = None,
        max_in_flight: int = 32,
    ) -> None:
        super().__init__(
            AcceptedWebSocketTransport(connection),
            initialization_timeout=initialization_timeout,
            request_timeout=request_timeout,
            max_in_flight=max_in_flight,
        )
