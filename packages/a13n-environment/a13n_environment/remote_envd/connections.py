"""Host-owned reverse WebSocket rendezvous. No listener, authentication, or global state."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a13n_envd_client import AcceptedWebSocketTransport, EIPDeviceConnection, EIPSession
from a13n_envd_client.eip.v1 import DeviceDescriptor, DirectoryListParams, DirectoryListResult
from a13n_envd_client.websocket import WebSocketConnection

from ..attachments import DeviceEIPSessionSource
from ..envd_policy import (
    EnvdBoundaryRequirement,
    EnvdCredentialResolver,
    EnvdEgressConfiguration,
    EnvironmentEnvdCredentialResolver,
    resolve_egress,
)
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import provider_error
from .configuration import RemoteEnvdConnectionConfiguration, RemoteEnvdStateData

WEBSOCKET_PROVIDER_KEY = "websocket_envd"


@dataclass(slots=True)
class _Attachment:
    connection: WebSocketConnection
    task: asyncio.Task[None] | None = None
    device: EIPDeviceConnection | None = None
    retiring: bool = False


class WebSocketEnvdConnections:
    """Bounded process-local connections for one Host-selected backend.

    A Host authenticates the upgrade and resolves its native daemon identity, then
    awaits ``attach(identity, connection)`` in its own connection handler. This SDK
    initializes immediately, even without a Run. Independent adapters open fresh
    Sessions on that Device. Hosts own cross-process routing; this is not a pool
    of tenant authorities or an automatic session takeover mechanism.
    """

    def __init__(
        self,
        *,
        configuration: RemoteEnvdConnectionConfiguration | None = None,
        max_connections: int = 128,
        credential_resolver: EnvdCredentialResolver | None = None,
    ) -> None:
        if not isinstance(max_connections, int) or isinstance(max_connections, bool) or max_connections < 1:
            raise ValueError("max_connections must be a positive integer")
        if configuration is not None and not isinstance(configuration, RemoteEnvdConnectionConfiguration):
            raise TypeError("WebSocket SDK requires RemoteEnvdConnectionConfiguration")
        self.credential_resolver = credential_resolver or EnvironmentEnvdCredentialResolver()
        self._configuration = configuration or RemoteEnvdConnectionConfiguration()
        self._max_connections = max_connections
        self._attachments: dict[str, _Attachment] = {}
        self._changed = asyncio.Event()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> WebSocketEnvdConnections:
        if self._closed:
            raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_connections_closed", Category.UNAVAILABLE)
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def attach(self, device_id: str, connection: WebSocketConnection) -> None:
        """Adopt an authenticated connection until disconnect or Host shutdown.

        Rejected connections are also closed. Returning from the Host handler must
        not close an admitted connection early: await this method, do not detach it.
        """
        try:
            identity = RemoteEnvdStateData(device_id=device_id).device_id
            if self._closed:
                raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_connections_closed", Category.UNAVAILABLE)
            if identity in self._attachments:
                raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_connection_busy", Category.CONFLICT)
            if len(self._attachments) >= self._max_connections:
                raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_connection_capacity", Category.CONFLICT)
            transport = AcceptedWebSocketTransport(connection)
        except BaseException:
            try:
                async with asyncio.timeout(1):
                    await connection.close()
            except (Exception, asyncio.CancelledError):
                pass
            raise
        entry = _Attachment(connection)
        self._attachments[identity] = entry
        entry.task = asyncio.create_task(self._serve(identity, entry, transport), name="envd-websocket-attachment")
        try:
            await entry.task
        finally:
            # Cancellation can land before the SDK task gets its first turn.
            # In that case _serve's finally block has never been entered.
            if self._attachments.get(identity) is entry:
                try:
                    await transport.close()
                finally:
                    del self._attachments[identity]
                    self._changed.set()

    async def _serve(self, identity: str, entry: _Attachment, transport: AcceptedWebSocketTransport) -> None:
        configuration = self._configuration
        try:
            entry.device = await EIPDeviceConnection.initialize(
                transport,
                expected_device_id=identity,
                initialization_timeout=configuration.initialization_timeout,
                request_timeout=configuration.request_timeout,
                max_in_flight=configuration.max_in_flight,
            )
            self._changed.set()
            await entry.connection.wait_closed()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_connection_failed", Category.UNAVAILABLE) from None
        finally:
            entry.retiring = True
            try:
                if entry.device is not None:
                    await entry.device.close()
                else:
                    await transport.close()
            finally:
                if self._attachments.get(identity) is entry:
                    del self._attachments[identity]
                self._changed.set()

    async def acquire_device(self, *, expected_device_id: str, timeout: float = 10) -> EIPDeviceConnection:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Connection acquisition timeout must be finite and positive")
        RemoteEnvdStateData(device_id=expected_device_id)
        try:
            async with asyncio.timeout(timeout):
                while True:
                    self._changed.clear()
                    if self._closed:
                        raise provider_error(
                            WEBSOCKET_PROVIDER_KEY, "provider_connections_closed", Category.UNAVAILABLE
                        )
                    entry = self._attachments.get(expected_device_id)
                    if entry is not None and not entry.retiring and entry.device is not None:
                        return entry.device
                    await self._changed.wait()
        except TimeoutError:
            raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_connection_timeout", Category.TIMEOUT) from None

    async def describe(self, *, expected_device_id: str, timeout: float = 10) -> DeviceDescriptor:
        return await (await self.acquire_device(expected_device_id=expected_device_id, timeout=timeout)).describe()

    async def list_directories(self, params: DirectoryListParams, *, timeout: float = 10) -> DirectoryListResult:
        device = await self.acquire_device(expected_device_id=params.expected_device_id, timeout=timeout)
        return await device.list_directories(params)

    @asynccontextmanager
    async def open_session(
        self,
        *,
        expected_device_id: str,
        required_methods: frozenset[str],
        working_directory: str | None = None,
        timeout: float = 10,
        egress: EnvdEgressConfiguration | None = None,
        expected_boundary: EnvdBoundaryRequirement | None = None,
    ) -> AsyncIterator[EIPSession]:
        device = await self.acquire_device(expected_device_id=expected_device_id, timeout=timeout)
        async with DeviceEIPSessionSource(device).open_session(
            expected_device_id=expected_device_id,
            required_methods=required_methods,
            working_directory=working_directory,
            egress=await resolve_egress(egress, self.credential_resolver),
            expected_boundary=expected_boundary,
        ) as session:
            yield session

    async def close(self) -> None:
        """Fence admission and release all SDK-owned attachment tasks and sessions."""
        if self._close_task is None:
            self._closed = True
            self._changed.set()
            self._close_task = asyncio.create_task(self._finish_close(), name="envd-websocket-shutdown")
        await asyncio.shield(self._close_task)

    async def _finish_close(self) -> None:
        tasks = [entry.task for entry in self._attachments.values() if entry.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
