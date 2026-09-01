from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Mapping
from types import TracebackType

from pydantic import BaseModel, JsonValue

from .models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from .operations import EnvironmentOperations


class Environment(ABC):
    """Fresh single-use process-local adapter for one provider target."""

    def __init__(self, state: EnvironmentState | None) -> None:
        self._known_state = state.model_copy(deep=True) if state is not None else None
        self._lifecycle = "constructed"

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
        await self.enter(
            thread_id="thread-lifecycle",
            run_id="run-lifecycle",
            agent_instance_id="agent-lifecycle",
            mount_id="mount-lifecycle",
        )
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

    async def enter(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str] | None = None,
    ) -> None:
        if self._lifecycle != "constructed":
            raise RuntimeError("Environment adapters can be entered exactly once")
        self._lifecycle = "entering"
        try:
            await self._enter(
                thread_id=thread_id,
                run_id=run_id,
                agent_instance_id=agent_instance_id,
                mount_id=mount_id,
                host_refs=dict(host_refs or {}),
            )
        except BaseException:
            self._lifecycle = "failed"
            raise
        self._lifecycle = "entered"

    async def warmup(self) -> None:
        if self._lifecycle != "constructed":
            raise RuntimeError("Environment adapters can perform one lifecycle operation")
        self._lifecycle = "warming"
        try:
            await self._warmup()
        except BaseException:
            self._lifecycle = "failed"
            raise
        self._lifecycle = "warmed"

    def dump_state(self) -> EnvironmentState | None:
        """Return a detached copy of the last validated cached state without external I/O."""
        return self._known_state.model_copy(deep=True) if self._known_state is not None else None

    async def ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if self._lifecycle != "entered":
            raise RuntimeError("Environment operations require an entered adapter")
        await self._ensure_ready(operations)

    async def close(self) -> None:
        if self._lifecycle in {"closed", "closing"}:
            return
        self._lifecycle = "closing"
        try:
            await self._close()
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
    async def _enter(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> None: ...

    async def _warmup(self) -> None:
        await self._enter(
            thread_id="thread-warmup",
            run_id="run-warmup",
            agent_instance_id="agent-warmup",
            mount_id="mount-warmup",
            host_refs={},
        )

    @abstractmethod
    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None: ...

    @abstractmethod
    async def _close(self) -> None: ...

    @abstractmethod
    async def _destroy(self) -> None: ...


class EnvironmentProvider(ABC):
    """Inert trusted plugin that validates configuration and constructs Environments."""

    @property
    @abstractmethod
    def key(self) -> str: ...

    @property
    @abstractmethod
    def configuration_versions(self) -> frozenset[str]: ...

    @abstractmethod
    def validate_configuration(self, *, schema_version: str, value: JsonValue) -> BaseModel: ...

    @abstractmethod
    def create_environment(
        self,
        *,
        configuration: BaseModel,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment: ...
