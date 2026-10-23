"""Synchronized explicit CodeAct values shared by foreground and detached calls."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_harness.codeact.config import CodeActConfig
from a13n_harness.codeact.values import JsonLimitExceeded, bounded_json_size

CODEACT_STATE_ID = "a13n.codeact"
CODEACT_STATE_VERSION = "1"
type StateKey = Annotated[str, Field(min_length=1, max_length=256)]


class CodeActStoredValues(BaseModel):
    """Portable data only; interpreter bindings and tool authority remain live."""

    model_config = ConfigDict(extra="forbid")
    values: dict[StateKey, JsonValue] = Field(default_factory=dict)


def validate_stored_values(state: CodeActStoredValues, config: CodeActConfig) -> None:
    if len(state.values) > config.max_state_entries:
        raise ValueError(f"CodeAct stored values exceed max_state_entries={config.max_state_entries}")
    try:
        bounded_json_size(state.model_dump(), config.max_state_bytes)
    except JsonLimitExceeded as exc:
        raise ValueError(f"CodeAct stored values exceed max_state_bytes={config.max_state_bytes}") from exc


class CodeActStateOwner:
    """Host-held Thread namespace; late writes never mutate exported snapshots."""

    def __init__(self) -> None:
        self._thread_id: str | None = None
        self._state = CodeActStoredValues()
        self._lock = asyncio.Lock()

    async def initialize(self, thread_id: str, state: CodeActStoredValues) -> None:
        async with self._lock:
            if self._thread_id is None:
                self._thread_id = thread_id
                self._state = state.model_copy(deep=True)
            elif self._thread_id != thread_id:
                raise ValueError("CodeAct state owner belongs to another Thread")

    async def snapshot(self) -> CodeActStoredValues:
        async with self._lock:
            return self._state.model_copy(deep=True)

    async def store(self, key: StateKey, value: JsonValue, config: CodeActConfig) -> None:
        async with self._lock:
            candidate = self._state.model_copy(deep=True)
            candidate.values[key] = deepcopy(value)
            validate_stored_values(candidate, config)
            self._state = candidate

    async def forget(self, key: StateKey) -> bool:
        async with self._lock:
            if key not in self._state.values:
                return False
            del self._state.values[key]
            return True
