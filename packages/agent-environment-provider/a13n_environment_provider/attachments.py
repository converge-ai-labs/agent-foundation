from __future__ import annotations

import asyncio
import ssl
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from a13n_envd_client import (
    AcceptedWebSocketTransport,
    EIPSession,
    HttpTransport,
    StdioTransport,
)
from websockets.asyncio.server import ServerConnection


class EIPSessionSource(ABC):
    """Single-use source for one freshly initialized and readiness-confirmed EIP Session."""

    def __init__(self) -> None:
        self._claimed = False
        self._discard_task: asyncio.Task[None] | None = None

    @abstractmethod
    def open_session(
        self,
        *,
        expected_environment_id: str,
        required_methods: frozenset[str],
    ) -> AbstractAsyncContextManager[EIPSession]: ...

    @abstractmethod
    async def discard(self) -> None:
        """Release an unentered carrier source exactly once."""

    def _claim(self) -> None:
        if self._claimed:
            raise RuntimeError("EIP session source is single-use")
        self._claimed = True

    async def _discard_once(
        self,
        cleanup: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        if self._discard_task is None:
            if self._claimed:
                return
            self._claimed = True
            self._discard_task = asyncio.create_task(cleanup(), name="eip-session-source-discard")
        await asyncio.shield(self._discard_task)


class StdioEIPCarrier:
    """Adapter-owned trusted stdio carrier that issues one exclusive lease at a time."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        max_request_bytes: int = 1024 * 1024,
        max_response_bytes: int = 1024 * 1024,
        max_transfer_frame_bytes: int = 1024 * 1024,
    ) -> None:
        if not isinstance(process, asyncio.subprocess.Process):
            raise TypeError("stdio EIP carrier requires an asyncio subprocess")
        if process.stdin is None or process.stdout is None:
            raise ValueError("stdio EIP process must have stdin and stdout pipes")
        self._process = process
        self._transport = StdioTransport.from_process(
            process,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
            max_transfer_frame_bytes=max_transfer_frame_bytes,
        )
        self._leased = False
        self._fatal = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def process(self) -> asyncio.subprocess.Process:
        return self._process

    @property
    def is_available(self) -> bool:
        return not self._leased and not self._fatal and self._close_task is None and self._process.returncode is None

    def lease(
        self,
        *,
        initialization_timeout: float = 10.0,
        request_timeout: float | None = None,
        max_in_flight: int = 32,
    ) -> StdioEIPSessionSource:
        if not self.is_available:
            raise RuntimeError("stdio EIP carrier is not available")
        self._leased = True
        return StdioEIPSessionSource(
            self,
            initialization_timeout=initialization_timeout,
            request_timeout=request_timeout,
            max_in_flight=max_in_flight,
        )

    async def close(self) -> None:
        self._fatal = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._transport.close(), name="eip-stdio-carrier-close")
        await asyncio.shield(self._close_task)

    async def _release(self) -> None:
        if not self._leased:
            raise RuntimeError("stdio EIP carrier lease is not active")
        if self._fatal or self._close_task is not None or self._process.returncode is not None:
            await self.close()
            raise RuntimeError("stdio EIP carrier became unavailable")
        self._leased = False

    async def _fence(self) -> None:
        self._fatal = True
        self._leased = False
        await self.close()


class StdioEIPSessionSource(EIPSessionSource):
    def __init__(
        self,
        carrier: asyncio.subprocess.Process | StdioEIPCarrier,
        *,
        initialization_timeout: float = 10.0,
        request_timeout: float | None = None,
        max_in_flight: int = 32,
    ) -> None:
        super().__init__()
        if isinstance(carrier, StdioEIPCarrier):
            if not carrier._leased:
                raise ValueError("stdio EIP carrier must issue its own lease")
            self._carrier = carrier
            self._process = carrier.process
            self._reusable = True
        elif isinstance(carrier, asyncio.subprocess.Process):
            if carrier.stdin is None or carrier.stdout is None:
                raise ValueError("stdio EIP process must have stdin and stdout pipes")
            self._carrier = None
            self._process = carrier
            self._reusable = False
        else:
            raise TypeError("stdio EIP source requires a subprocess or carrier lease")
        self._initialization_timeout = initialization_timeout
        self._request_timeout = request_timeout
        self._max_in_flight = max_in_flight

    @asynccontextmanager
    async def open_session(
        self,
        *,
        expected_environment_id: str,
        required_methods: frozenset[str],
    ) -> AsyncGenerator[EIPSession]:
        self._claim()
        transport = (
            self._carrier._transport if self._carrier is not None else StdioTransport.from_process(self._process)
        )
        try:
            session = await _initialize(
                transport,
                expected_environment_id=expected_environment_id,
                required_methods=required_methods,
                initialization_timeout=self._initialization_timeout,
                request_timeout=self._request_timeout,
                max_in_flight=self._max_in_flight,
                reuse_transport=self._reusable,
            )
        except BaseException:
            if self._carrier is not None:
                await self._carrier._fence()
            raise
        try:
            yield session
        except BaseException:
            try:
                await session.close()
            except BaseException:
                if self._carrier is not None:
                    await self._carrier._fence()
            else:
                if self._carrier is not None:
                    await self._carrier._release()
            raise
        else:
            try:
                await session.close()
            except BaseException:
                if self._carrier is not None:
                    await self._carrier._fence()
                raise
            if self._carrier is not None:
                await self._carrier._release()

    async def discard(self) -> None:
        async def cleanup() -> None:
            if self._carrier is not None:
                await self._carrier._release()
            else:
                await StdioTransport.from_process(self._process).close()

        await self._discard_once(cleanup)


class HttpEIPSessionSource(EIPSessionSource):
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
        super().__init__()
        self._endpoint = endpoint
        self._credential = credential
        self._verify = verify
        self._initialization_timeout = initialization_timeout
        self._request_timeout = request_timeout
        self._max_in_flight = max_in_flight
        self._allow_plaintext_private_link = allow_plaintext_private_link

    @asynccontextmanager
    async def open_session(
        self,
        *,
        expected_environment_id: str,
        required_methods: frozenset[str],
    ) -> AsyncGenerator[EIPSession]:
        self._claim()
        transport = HttpTransport(
            self._endpoint,
            self._credential,
            verify=self._verify,
            request_timeout=self._request_timeout or 30.0,
            allow_plaintext_private_link=self._allow_plaintext_private_link,
        )
        session = await _initialize(
            transport,
            expected_environment_id=expected_environment_id,
            required_methods=required_methods,
            initialization_timeout=self._initialization_timeout,
            request_timeout=self._request_timeout,
            max_in_flight=self._max_in_flight,
        )
        async with session:
            yield session

    async def discard(self) -> None:
        async def clear_credential() -> None:
            self._credential = ""

        await self._discard_once(clear_credential)


class AcceptedWebSocketEIPSessionSource(EIPSessionSource):
    def __init__(
        self,
        connection: ServerConnection,
        *,
        initialization_timeout: float = 10.0,
        request_timeout: float | None = None,
        max_in_flight: int = 32,
    ) -> None:
        super().__init__()
        self._connection = connection
        self._initialization_timeout = initialization_timeout
        self._request_timeout = request_timeout
        self._max_in_flight = max_in_flight

    @asynccontextmanager
    async def open_session(
        self,
        *,
        expected_environment_id: str,
        required_methods: frozenset[str],
    ) -> AsyncGenerator[EIPSession]:
        self._claim()
        transport = AcceptedWebSocketTransport(self._connection)
        session = await _initialize(
            transport,
            expected_environment_id=expected_environment_id,
            required_methods=required_methods,
            initialization_timeout=self._initialization_timeout,
            request_timeout=self._request_timeout,
            max_in_flight=self._max_in_flight,
        )
        async with session:
            yield session

    async def discard(self) -> None:
        await self._discard_once(lambda: AcceptedWebSocketTransport(self._connection).close())


async def _initialize(
    transport,
    *,
    expected_environment_id: str,
    required_methods: frozenset[str],
    initialization_timeout: float,
    request_timeout: float | None,
    max_in_flight: int,
    reuse_transport: bool = False,
) -> EIPSession:
    try:
        return await EIPSession.initialize(
            transport,
            expected_environment_id=expected_environment_id,
            required_methods=tuple(sorted(required_methods)),
            initialization_timeout=initialization_timeout,
            request_timeout=request_timeout,
            max_in_flight=max_in_flight,
            reuse_transport=reuse_transport,
        )
    except BaseException:
        await transport.close()
        raise
