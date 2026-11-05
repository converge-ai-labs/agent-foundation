"""Native Envd start evidence, independent of an executable Python continuation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .commands import BoundProcessHandle, ProcessIdentity, ProcessInfo
from .retention import EnvironmentOutputPolicy


class EnvdProcessState(BaseModel):
    """Checkpointed before process.start; contains no command or credential material."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    device_id: str
    session_id: str
    generation: int = Field(gt=0)
    start_operation_id: str
    output_policy: EnvironmentOutputPolicy
    identity: ProcessIdentity | None = None


class ShellExecutionState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mount_name: str
    process: EnvdProcessState


@runtime_checkable
class RecoverableProcessOperations(Protocol):
    async def recover(self, state: EnvdProcessState) -> ProcessInfo: ...

    def detach(self, handle: BoundProcessHandle) -> None: ...


type ProcessCheckpoint = Callable[[EnvdProcessState], Awaitable[None]]
_CHECKPOINT: ContextVar[ProcessCheckpoint | None] = ContextVar("envd_process_checkpoint", default=None)


@contextmanager
def process_checkpoint(callback: ProcessCheckpoint) -> Iterator[None]:
    token = _CHECKPOINT.set(callback)
    try:
        yield
    finally:
        _CHECKPOINT.reset(token)


def current_process_checkpoint() -> ProcessCheckpoint | None:
    return _CHECKPOINT.get()
