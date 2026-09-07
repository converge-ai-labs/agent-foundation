"""Host-owned reverse WebSocket rendezvous. No listener, authentication, or global state."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a13n_envd_client import AcceptedWebSocketTransport, EIPSession
from a13n_envd_client.websocket import WebSocketConnection

from ..errors import EnvironmentProviderErrorCategory as Category
from .configuration import RemoteEnvdConnectionConfiguration, RemoteEnvdStateData
from .environment import REQUIRED_METHODS, provider_error

WEBSOCKET_PROVIDER_KEY = "a13n.websocket-envd"


@dataclass(slots=True)
class _Attachment:
    connection: WebSocketConnection
    task: asyncio.Task[None] | None = None
    session: EIPSession | None = None
    leased: bool = False
    retiring: bool = False


class WebSocketEnvdConnections:
    """Bounded process-local connections for one Host-selected backend.

    A Host authenticates the upgrade and resolves its native daemon identity, then
    awaits ``attach(identity, connection)`` in its own connection handler. This SDK
    initializes immediately, even without a Run, and admits one Environment lease
    per daemon. Hosts own cross-process routing and scheduling; this is not a pool
    of tenant authorities or an automatic session takeover mechanism.
    """

    def __init__(
        self,
        *,
        configuration: RemoteEnvdConnectionConfiguration | None = None,
        max_connections: int = 128,
    ) -> None:
        if not isinstance(max_connections, int) or isinstance(max_connections, bool) or max_connections < 1:
            raise ValueError("max_connections must be a positive integer")
        if configuration is not None and not isinstance(configuration, RemoteEnvdConnectionConfiguration):
            raise TypeError("WebSocket SDK requires RemoteEnvdConnectionConfiguration")
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

    async def attach(self, daemon_environment_id: str, connection: WebSocketConnection) -> None:
        """Adopt an authenticated connection until disconnect, lease close, or shutdown.

        Rejected connections are also closed. Returning from the Host handler must
        not close an admitted connection early: await this method, do not detach it.
        """
        try:
            identity = RemoteEnvdStateData(daemon_environment_id=daemon_environment_id).daemon_environment_id
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
        carrier_closed = False
        try:
            entry.session = await EIPSession.initialize(
                transport,
                expected_environment_id=identity,
                required_methods=tuple(sorted(REQUIRED_METHODS)),
                initialization_timeout=configuration.initialization_timeout,
                request_timeout=configuration.request_timeout,
                max_in_flight=configuration.max_in_flight,
            )
            self._changed.set()
            await entry.connection.wait_closed()
            carrier_closed = True
        except asyncio.CancelledError:
            raise
        except Exception:
            raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_connection_failed", Category.UNAVAILABLE) from None
        finally:
            lease_is_closing = entry.retiring and entry.leased and carrier_closed
            entry.retiring = True
            try:
                if entry.session is not None:
                    # A clean lease close itself closes the WebSocket. Do not race
                    # it with abort(), which would turn success into terminal failure.
                    if not lease_is_closing:
                        await entry.session.abort()
                else:
                    await transport.close()
            finally:
                if self._attachments.get(identity) is entry:
                    del self._attachments[identity]
                self._changed.set()

    @asynccontextmanager
    async def open_session(
        self,
        *,
        expected_environment_id: str,
        required_methods: frozenset[str],
        timeout: float = 10,
    ) -> AsyncIterator[EIPSession]:
        """Wait for an online daemon and exclusively lend its initialized session."""
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Connection acquisition timeout must be finite and positive")
        RemoteEnvdStateData(daemon_environment_id=expected_environment_id)
        try:
            async with asyncio.timeout(timeout):
                while True:
                    self._changed.clear()
                    if self._closed:
                        raise provider_error(
                            WEBSOCKET_PROVIDER_KEY, "provider_connections_closed", Category.UNAVAILABLE
                        )
                    entry = self._attachments.get(expected_environment_id)
                    if entry is not None and not entry.retiring and entry.session is not None:
                        if entry.leased:
                            raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_connection_busy", Category.CONFLICT)
                        if required_methods - set(entry.session.descriptor.available_methods):
                            raise provider_error(
                                WEBSOCKET_PROVIDER_KEY, "provider_methods_unsupported", Category.UNSUPPORTED
                            )
                        entry.leased = True
                        break
                    await self._changed.wait()
        except TimeoutError:
            raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_connection_timeout", Category.TIMEOUT) from None
        session = entry.session
        primary_error: BaseException | None = None
        try:
            # A ready idle connection is rechecked before publishing operation facets.
            try:
                ready = (await session.readiness()).ready
            except asyncio.CancelledError:
                raise
            except Exception:
                raise provider_error(
                    WEBSOCKET_PROVIDER_KEY, "provider_connection_failed", Category.UNAVAILABLE
                ) from None
            if not ready:
                raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_connection_failed", Category.UNAVAILABLE)
            yield session
        except BaseException as error:
            primary_error = error
            raise
        finally:
            entry.retiring = True
            try:
                if isinstance(primary_error, asyncio.CancelledError):
                    await session.abort()
                else:
                    await session.close()
            except asyncio.CancelledError:
                await session.abort()
                raise
            except Exception:
                if primary_error is None:
                    raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_cleanup_failed", Category.CLEANUP) from None
                primary_error.add_note("Remote Envd session cleanup also failed.")
            finally:
                if entry.task is not None:
                    # The transport close wakes the Host handler. Settle old admission
                    # before allowing a subsequent Run to request another connection.
                    try:
                        async with asyncio.timeout(1):
                            await asyncio.shield(entry.task)
                    except asyncio.CancelledError:
                        if not entry.task.done():
                            entry.task.cancel()
                            await asyncio.gather(entry.task, return_exceptions=True)
                            raise
                        # Shutdown may have cancelled the SDK task, not this caller.
                        current = asyncio.current_task()
                        if current is not None and current.cancelling():
                            raise
                    except Exception:
                        if not entry.task.done():
                            entry.task.cancel()
                        await asyncio.gather(entry.task, return_exceptions=True)

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
