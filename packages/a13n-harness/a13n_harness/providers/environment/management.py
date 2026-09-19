from __future__ import annotations

import asyncio
import socket
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timedelta
from types import TracebackType
from typing import Literal
from uuid import uuid4

from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict, Field

from .errors import EnvironmentProviderErrorCategory, provider_error
from .models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from .operations import EnvironmentOperations


class Environment(ABC):
    """Fresh single-use process-local adapter for one provider target."""

    recover_on_unavailable: bool = False

    def __init__(self, state: EnvironmentState | None) -> None:
        self._known_state = state.model_copy(deep=True) if state is not None else None
        self._lifecycle = "constructed"
        self._prepared = False
        self._preparation_revision = 0
        self._observer: Callable[[str, str, BaseException | None], None] | None = None
        self._prepare_lock = asyncio.Lock()
        self._mount_id = "mount-" + uuid4().hex

    @property
    @abstractmethod
    def provider_key(self) -> str: ...

    @property
    @abstractmethod
    def environment_id(self) -> str: ...

    @property
    @abstractmethod
    def descriptor(self) -> EnvironmentDescriptor: ...

    @property
    @abstractmethod
    def availability(self) -> EnvironmentAvailability: ...

    @property
    @abstractmethod
    def operations(self) -> EnvironmentOperations: ...

    async def __aenter__(self) -> Environment:
        await self.enter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        del exc_type, traceback
        cleanup_error: BaseException | None = None
        try:
            await self.close()
        except BaseException as error:
            cleanup_error = error
        if cleanup_error is None:
            return None
        if isinstance(exc_value, asyncio.CancelledError):
            exc_value.add_note(f"Environment close also failed: {cleanup_error!r}")
            return None
        if isinstance(cleanup_error, asyncio.CancelledError):
            if exc_value is not None:
                cleanup_error.add_note(f"Environment use also failed: {exc_value!r}")
            raise cleanup_error from None
        if exc_value is not None:
            raise BaseExceptionGroup("Environment use and close failed", [exc_value, cleanup_error]) from None
        raise cleanup_error

    async def enter(self, *, mount_id: str | None = None) -> None:
        if self._lifecycle != "constructed":
            raise RuntimeError("Environment adapters can be entered exactly once")
        if mount_id is not None:
            self._mount_id = mount_id
        self._lifecycle = "entered"
        if self._prepared:
            self._bind_mount(self._mount_id)
            self._observe("ready")

    async def prepare(self) -> None:
        async with self._prepare_lock:
            if self._lifecycle in {"closed", "closing", "destroyed"}:
                raise RuntimeError("Environment is closed")
            if self._prepared:
                return
            await self._prepare_locked()

    def observe(self, callback: Callable[[str, str, BaseException | None], None]) -> None:
        """Attach a passive observer; it owns no lifecycle state."""
        self._observer = callback

    def _observe(self, event: str, error: BaseException | None = None) -> None:
        if self._observer is not None:
            try:
                self._observer(event, self._mount_id, error)
            except Exception:
                get_logger(__name__).exception("Environment lifecycle observation failed")

    async def _prepare_locked(self) -> None:
        self._observe("started")
        try:
            await self._prepare(mount_id=self._mount_id)
        except BaseException as error:
            self._observe("failed", error)
            raise
        self._prepared = True
        self._preparation_revision += 1
        self._observe("ready")

    def _bind_mount(self, mount_id: str) -> None:
        """Bind already prepared local operations; implementations perform no target I/O."""
        return None

    def bind_mount(self, mount_id: str) -> None:
        """Rebind prepared operations, so later recovery reuses the current mount."""
        if self._lifecycle != "entered":
            raise RuntimeError("Environment has no operation scope")
        self._mount_id = mount_id
        self._bind_mount(mount_id)

    def _unsupported(self) -> Exception:
        """One typed answer for operations this Provider never declared."""
        return provider_error(
            self.provider_key, "provider_operation_unsupported", EnvironmentProviderErrorCategory.UNSUPPORTED
        )

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        """Observe an abandoned preparation without creating, starting, or replacing a target."""
        raise self._unsupported()

    async def stop(self) -> None:
        await self._stop()

    async def _stop(self) -> None:
        raise self._unsupported()

    @property
    def keepalive_horizon(self) -> timedelta:
        """Desired retention interval, bounded by this target configuration."""
        return timedelta(seconds=300)

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime | None:
        return None

    def dump_state(self) -> EnvironmentState | None:
        """Return a detached copy of the last validated cached state without external I/O."""
        return self._known_state.model_copy(deep=True) if self._known_state is not None else None

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        """Check an entered connection without preparing or recovering its target."""
        if self._lifecycle != "entered":
            raise RuntimeError("Environment readiness requires an entered adapter")
        await self._ensure_ready(operations)
        if self._lifecycle != "entered":
            raise RuntimeError("Environment is closed")

    async def recover(self) -> None:
        """Refresh this scope under explicit Host authority, retaining native observations."""
        async with self._prepare_lock:
            if self._lifecycle != "entered" or not self.recover_on_unavailable:
                raise RuntimeError("Environment does not support in-scope recovery")
            self._prepared = False
            await self._prepare_locked()

    async def ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if self._lifecycle != "entered":
            raise RuntimeError("Environment operations require an entered adapter")
        await self.prepare()
        revision = self._preparation_revision
        try:
            await self.check_ready(operations)
        except EnvironmentError as error:
            async with self._prepare_lock:
                if self._lifecycle != "entered":
                    raise RuntimeError("Environment is closed") from error
                if revision == self._preparation_revision:
                    if not self.recover_on_unavailable or error.code != "environment_unavailable":
                        raise
                    previous_identity = self.descriptor.backing_identity
                    self._prepared = False
                    await self._prepare_locked()
                    changed = self.descriptor.backing_identity != previous_identity
                    raise EnvironmentError(
                        "Environment was rebuilt; previous temporary files and process handles may be gone. Recheck the workspace before continuing."
                        if changed
                        else "Environment connection was refreshed. Recheck existing process handles before continuing.",
                        code="environment_rebuilt" if changed else "environment_connection_refreshed",
                        details={"environment_id": self.environment_id, "generation": self.descriptor.generation},
                    ) from error
            await self.check_ready(operations)
        if self._lifecycle != "entered":
            raise RuntimeError("Environment is closed")

    async def close(self) -> None:
        async with self._prepare_lock:
            if self._lifecycle in {"closed", "closing"}:
                return
            self._lifecycle = "closing"
            try:
                await self._close()
            except BaseException as error:
                self._observe("closed", error)
                raise
            else:
                self._observe("closed")
            finally:
                self._lifecycle = "closed"

    async def destroy(self) -> None:
        if self._lifecycle != "constructed":
            raise RuntimeError("Environment destroy requires a fresh adapter")
        self._lifecycle = "destroying"
        try:
            await self._destroy()
        except BaseException:
            self._lifecycle = "failed"
            raise
        self._known_state = None
        self._lifecycle = "destroyed"

    @property
    def is_entered(self) -> bool:
        return self._lifecycle == "entered"

    def _cache_state(self, state: EnvironmentState | None) -> None:
        self._known_state = state.model_copy(deep=True) if state is not None else None

    @abstractmethod
    async def _prepare(self, *, mount_id: str) -> None: ...

    @abstractmethod
    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None: ...

    @abstractmethod
    async def _close(self) -> None: ...

    async def _destroy(self) -> None:
        raise self._unsupported()


class EmptyProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HostLocalProviderConfiguration(EmptyProviderConfiguration):
    host_id: str = Field(default_factory=socket.gethostname, min_length=1, max_length=256)
