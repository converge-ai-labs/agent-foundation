from __future__ import annotations

import asyncio
import ssl
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from converge_agent_envd_client import (
    AcceptedWebSocketTransport,
    EIPSession,
    HttpTransport,
    StdioTransport,
)
from websockets.asyncio.server import ServerConnection


class EIPSessionSource(ABC):
    """Single-use source for one freshly initialized EIP client session."""

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


class StdioEIPSessionSource(EIPSessionSource):
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        initialization_timeout: float = 10.0,
        request_timeout: float | None = None,
        max_in_flight: int = 32,
    ) -> None:
        super().__init__()
        if process.stdin is None or process.stdout is None:
            raise ValueError("stdio EIP process must have stdin and stdout pipes")
        self._process = process
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
        transport = StdioTransport.from_process(self._process)
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
        await self._discard_once(lambda: StdioTransport.from_process(self._process).close())


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


@dataclass(frozen=True, slots=True)
class DirectLocalEnvironmentAttachment:
    attachment_id: str
    environment_id: str
    configuration: object
    _claimed: bool = field(default=False, init=False, repr=False, compare=False)

    def claim(self) -> None:
        if self._claimed:
            raise RuntimeError("Environment attachment is single-use")
        object.__setattr__(self, "_claimed", True)


@dataclass(frozen=True, slots=True)
class EIPEnvironmentAttachment:
    attachment_id: str
    environment_id: str
    session_source: EIPSessionSource
    _claimed: bool = field(default=False, init=False, repr=False, compare=False)

    def claim(self) -> None:
        if self._claimed:
            raise RuntimeError("Environment attachment is single-use")
        object.__setattr__(self, "_claimed", True)


type EnvironmentRuntimeAttachment = DirectLocalEnvironmentAttachment | EIPEnvironmentAttachment


async def _initialize(
    transport,
    *,
    expected_environment_id: str,
    required_methods: frozenset[str],
    initialization_timeout: float,
    request_timeout: float | None,
    max_in_flight: int,
) -> EIPSession:
    try:
        return await EIPSession.initialize(
            transport,
            expected_environment_id=expected_environment_id,
            required_methods=tuple(sorted(required_methods)),
            initialization_timeout=initialization_timeout,
            request_timeout=request_timeout,
            max_in_flight=max_in_flight,
        )
    except BaseException:
        await transport.close()
        raise
