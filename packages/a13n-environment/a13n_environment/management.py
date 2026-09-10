from __future__ import annotations

import asyncio
import logging
import socket
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from .operations import EnvironmentOperations


@dataclass(frozen=True, slots=True)
class EnvironmentScope:
    thread_id: str = "thread-prepare"
    run_id: str = "run-prepare"
    agent_instance_id: str = "agent-prepare"
    mount_id: str = "mount-prepare"


class Environment(ABC):
    """Fresh single-use process-local adapter for one provider target."""

    recover_on_unavailable: bool = False

    def __init__(self, state: EnvironmentState | None) -> None:
        self._known_state = state.model_copy(deep=True) if state is not None else None
        self._lifecycle = "constructed"
        self._prepared = False
        self._preparation_revision = 0
        self._observer: Callable[[str, EnvironmentScope, BaseException | None], None] | None = None
        self._prepare_lock = asyncio.Lock()
        self._scope = EnvironmentScope()
        self._host_refs: dict[str, str] = {}

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
        self._scope = EnvironmentScope(thread_id, run_id, agent_instance_id, mount_id)
        self._host_refs = dict(host_refs or {})
        self._lifecycle = "entered"
        if self._prepared:
            self._bind_mount(mount_id)
            self._observe("ready")

    async def prepare(self) -> None:
        async with self._prepare_lock:
            if self._lifecycle in {"closed", "closing", "destroyed"}:
                raise RuntimeError("Environment is closed")
            if self._prepared:
                return
            await self._prepare_locked()

    def observe(self, callback: Callable[[str, EnvironmentScope, BaseException | None], None]) -> None:
        """Attach a passive observer; it owns no lifecycle state."""
        self._observer = callback

    def _observe(self, event: str, error: BaseException | None = None) -> None:
        if self._observer is not None:
            try:
                self._observer(event, self._scope, error)
            except Exception:
                logging.getLogger(__name__).exception("Environment lifecycle observation failed")

    async def _prepare_locked(self) -> None:
        self._observe("started")
        try:
            await self._prepare(
                thread_id=self._scope.thread_id,
                run_id=self._scope.run_id,
                agent_instance_id=self._scope.agent_instance_id,
                mount_id=self._scope.mount_id,
                host_refs=self._host_refs,
            )
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
        if self._lifecycle != "entered":
            raise RuntimeError("Environment has no operation scope")
        self._bind_mount(mount_id)

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        """Observe an abandoned preparation without creating, starting, or replacing a target."""
        raise NotImplementedError("This Provider does not support durable target reconciliation")

    async def stop(self) -> None:
        await self._stop()

    async def _stop(self) -> None:
        raise NotImplementedError("This Provider does not support resumable stop")

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
    async def _prepare(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> None: ...

    @abstractmethod
    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None: ...

    @abstractmethod
    async def _close(self) -> None: ...

    @abstractmethod
    async def _destroy(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderRuntimeContext:
    environment_id: str
    operation_id: str
    storage_root: Path
    managed: bool = True


class EmptyProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HostLocalProviderConfiguration(EmptyProviderConfiguration):
    host_id: str = Field(default_factory=socket.gethostname, min_length=1, max_length=256)


class EnvironmentProvider(ABC):
    """Inert trusted plugin that validates configuration and constructs Environments."""

    provider_configuration_model: type[BaseModel] = EmptyProviderConfiguration
    credential_model: type[BaseModel] | None = None
    supports_managed: bool = True
    supports_stop: bool = False
    supports_destroy: bool = False
    requires_keepalive: bool = False

    async def create_runtime(
        self, *, configuration: BaseModel, credential: BaseModel | None, context: ProviderRuntimeContext
    ) -> object | None:
        return None

    @abstractmethod
    def describe_configuration(self, configuration: BaseModel) -> EnvironmentDescriptor: ...

    def target_identity(self, *, configuration: BaseModel, state: EnvironmentState | None) -> str | None:
        """Canonical backend target identity; exclude credentials and session state.

        Stateless adapters return None. Durable providers must override this method.
        The Host namespaces this identity through backend_identity().
        """
        del configuration, state
        return None

    def backend_identity(self, configuration: BaseModel) -> JsonValue:
        """Return the backend namespace; overrides exclude connection tuning fields."""
        return configuration.model_dump(mode="json")

    @property
    @abstractmethod
    def key(self) -> str: ...

    @property
    def display_name(self) -> str:
        """Human-readable identity; custom providers may use their registration key."""
        return self.key

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
        environment_id: str,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment: ...
