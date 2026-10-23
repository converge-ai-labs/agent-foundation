"""Bounded, process-owned Modal transports for fixed targets; never execution authority."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING

import anyio
from a13n_logging import get_logger

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.native.errors import failure

if TYPE_CHECKING:
    import modal

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TransportKey:
    account: tuple[str, int, str | None]
    backend: str
    server: str
    environment_id: str
    fingerprint: str
    sandbox_id: str


@dataclass(slots=True, eq=False, repr=False)
class Transport:
    key: TransportKey
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client: modal.Client | None = None
    sandbox: modal.Sandbox | None = None
    borrowers: int = 0
    retired: bool = False
    closing: bool = False
    created: float = field(default_factory=monotonic)
    used: float = field(default_factory=monotonic)


class TransportPool:
    """One exclusive SDK lease per target; EIP sessions never hold this lease."""

    def __init__(self, *, capacity: int = 32, idle_seconds: float = 120, max_age_seconds: float = 600):
        self.capacity = capacity
        self.idle_seconds = idle_seconds
        self.max_age_seconds = max_age_seconds
        self._entries: dict[TransportKey, Transport] = {}
        self._acquisition_lock = asyncio.Lock()
        self._retired: set[Transport] = set()
        self._closed = False
        self._stop = asyncio.Event()
        self._reaper: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._closed or self._reaper is not None:
            raise RuntimeError("Modal transport pool is already started or closed")
        self._reaper = asyncio.create_task(self._reap(), name="modal-transport-expiry")

    def _expired(self, entry: Transport) -> bool:
        now = monotonic()
        return now - entry.used >= self.idle_seconds or now - entry.created >= self.max_age_seconds

    def _retire(self, entry: Transport) -> None:
        entry.retired = True
        if self._entries.get(entry.key) is entry:
            del self._entries[entry.key]
        self._retired.add(entry)

    async def acquire(self, key: TransportKey) -> Transport:
        async with self._acquisition_lock:
            if self._closed:
                raise failure("modal", "provider_closed", Category.UNAVAILABLE)
            await self.expire()
            if self._closed:
                raise failure("modal", "provider_closed", Category.UNAVAILABLE)
            entry = self._entries.get(key)
            if entry is None:
                while len(self._entries) + len(self._retired) >= self.capacity:
                    idle = [item for item in self._entries.values() if not item.borrowers]
                    if not idle:
                        raise failure("modal", "provider_transport_capacity", Category.UNAVAILABLE)
                    self._retire(min(idle, key=lambda item: item.used))
                    await self._cleanup_retired()
                    if self._closed:
                        raise failure("modal", "provider_closed", Category.UNAVAILABLE)
                entry = Transport(key)
                self._entries[key] = entry
            # Count waiters before yielding so expiry/eviction cannot detach a borrowed handle.
            entry.borrowers += 1
        try:
            await entry.lock.acquire()
        except BaseException:
            entry.borrowers -= 1
            with anyio.CancelScope(shield=True):
                await self._cleanup_retired()
            raise
        if entry.retired or self._closed:
            entry.lock.release()
            entry.borrowers -= 1
            await self._cleanup_retired()
            raise failure("modal", "provider_transport_retired", Category.UNAVAILABLE)
        return entry

    async def release(self, entry: Transport, *, discard: bool) -> None:
        if discard or self._closed or self._expired(entry):
            self._retire(entry)
        entry.used = monotonic()
        entry.lock.release()
        entry.borrowers -= 1
        await self._cleanup_retired()

    async def invalidate(self, key: TransportKey) -> None:
        if entry := self._entries.get(key):
            self._retire(entry)
        await self._cleanup_retired()

    async def expire(self) -> None:
        for entry in tuple(self._entries.values()):
            if self._expired(entry):
                self._retire(entry)
        await self._cleanup_retired()

    async def _cleanup_retired(self) -> None:
        primary: Exception | None = None
        for entry in tuple(self._retired):
            if not entry.borrowers and not entry.closing:
                entry.closing = True
                try:
                    await self._dispose(entry)
                except Exception as error:
                    primary = primary or error
                finally:
                    self._retired.remove(entry)
        if primary is not None:
            raise primary

    async def _dispose(self, entry: Transport) -> None:
        # Closing local handles never terminates the remote Sandbox. Detach and Client
        # close each get a cleanup budget, including when cancellation caused retirement.
        sandbox, client = entry.sandbox, entry.client
        entry.sandbox = entry.client = None
        primary: BaseException | None = None
        for stage, close in (
            ("sandbox_detach", sandbox.detach.aio if sandbox is not None else None),
            ("client_close", (lambda: client.__aexit__(None, None, None)) if client is not None else None),
        ):
            if close is None:
                continue
            try:
                with anyio.CancelScope(shield=True), anyio.fail_after(5):
                    await close()
            except BaseException as error:
                # Never log SDK error text, URLs or credentials.
                logger.warning(
                    "Modal transport cleanup failed", extra={"stage": stage, "error_type": type(error).__name__}
                )
                primary = primary or error
        if primary is not None:
            raise primary

    async def _reap(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
                return
            except TimeoutError:
                pass
            try:
                await self.expire()
            except Exception as error:
                logger.warning("Modal transport expiry failed", extra={"error_type": type(error).__name__})

    async def close(self) -> None:
        self._closed = True
        self._stop.set()
        if self._reaper is not None:
            # Let in-progress cleanup finish instead of cancelling a detach midway.
            await self._reaper
            self._reaper = None
        for entry in tuple(self._entries.values()):
            self._retire(entry)
        # Application owners close their borrowers before the pool. If shutdown races
        # an in-flight request, its eventual release still owns that handle's cleanup.
        await self._cleanup_retired()
