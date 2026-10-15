"""Checkpointed file versions observed by a conversation's model."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from a13n_harness.providers.memory import MemoryStoreError
from a13n_harness.state import AgentContextState

FILE_MEMORY_STATE_ID = "a13n.memory.file"
_MAX_FILES = 2048


class _Reads(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observed: dict[str, dict[str, str | None]] = Field(default_factory=dict)
    pending: dict[str, dict[str, str | None]] = Field(default_factory=dict)


class FileMemoryReads:
    """Keep tool reads pending until the next model response, not a sibling tool call.

    Own confirmed writes advance already observed paths immediately. Internal
    reads and conflict diagnostics never refresh the model's read dependencies.
    """

    def __init__(self, state: AgentContextState) -> None:
        self._state = state
        self._lock = asyncio.Lock()
        self._reads: _Reads | None = None

    async def _load(self) -> _Reads:
        if self._reads is None:
            self._reads = await self._state.read(FILE_MEMORY_STATE_ID, _Reads, version="1") or _Reads()
        return self._reads

    async def _save(self, reads: _Reads) -> None:
        await self._state.write(FILE_MEMORY_STATE_ID, reads, version="1")

    async def observe(self, key: str, versions: Mapping[str, str | None]) -> None:
        async with self._lock:
            reads = await self._load()
            known = reads.observed.get(key, {}).keys() | reads.pending.get(key, {}).keys() | versions.keys()
            if len(known) > _MAX_FILES:
                raise MemoryStoreError(
                    "read_set_full",
                    "Memory read tracking is full; summarize the conversation before reading more files.",
                )
            reads.pending.setdefault(key, {}).update(versions)
            await self._save(reads)

    async def deliver(self) -> None:
        async with self._lock:
            reads = await self._load()
            for key, versions in reads.pending.items():
                reads.observed.setdefault(key, {}).update(versions)
            reads.pending.clear()
            await self._save(reads)

    async def dependencies(self, key: str) -> dict[str, str | None]:
        async with self._lock:
            return dict((await self._load()).observed.get(key, {}))

    async def changed(self, key: str, versions: Mapping[str, str | None]) -> None:
        async with self._lock:
            reads = await self._load()
            observed = reads.observed.get(key, {})
            pending = reads.pending.get(key, {})
            for path, version in versions.items():
                if path in observed:
                    observed[path] = version
                if path in pending:
                    pending[path] = version
            await self._save(reads)

    async def move(
        self,
        key: str,
        source: str,
        destination: str,
        operation: Callable[[Mapping[str, str | None]], Awaitable[str]],
    ) -> str:
        # A move adds a guarded path. Reserve its capacity before storage changes
        # and serialize the observation transfer with concurrent tool reads.
        async with self._lock:
            reads = await self._load()
            observed = reads.observed.get(key, {})
            pending = reads.pending.get(key, {})
            known = observed.keys() | pending.keys()
            if source in known and destination not in known and len(known) >= _MAX_FILES:
                raise MemoryStoreError(
                    "read_set_full", "Memory read tracking is full; summarize before moving more files."
                )
            version = await operation(dict(observed))
            for versions in (observed, pending):
                if source in versions:
                    versions[source] = None
                    versions[destination] = version
                elif destination in versions:
                    versions[destination] = version
            await self._save(reads)
            return version

    async def clear(self) -> None:
        async with self._lock:
            self._reads = _Reads()
            await self._save(self._reads)
