"""Detached queue intent prepared for either idle or completion-time acceptance."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .domain import Run
from .input import AcceptedAgentInput
from .state import RunCheckpoint


@dataclass(frozen=True, slots=True)
class PreparedQueuedRun:
    run: Run
    state: RunCheckpoint
    input: AcceptedAgentInput
    validate: Callable[[AsyncSession], Awaitable[None]]
