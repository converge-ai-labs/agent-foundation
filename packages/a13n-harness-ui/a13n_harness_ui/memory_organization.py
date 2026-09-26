"""Bounded maintenance opportunities executed through observation-only Threads."""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from a13n_harness.providers.memory import DirectoryFileStore
from a13n_harness.providers.memory.contracts import FileText, MemoryStoreError, Origin
from a13n_harness.usage import RunUsageSummary
from a13n_logging import get_logger
from anyio import CancelScope, Lock, fail_after, to_thread
from anyio.abc import TaskGroup
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from a13n_harness_ui.configuration.models import LoadedHarnessUiConfiguration
from a13n_harness_ui.memory import MemoryOrganizationRun, MemoryScope, memory_scopes
from a13n_harness_ui.memory_diff import diff_context, snapshot

_SUCCESS_COOLDOWN = 3600
_RETRY_BACKOFF = 900
_DEADLINE = 300
_MAX_PENDING = 32


class OrganizationState(BaseModel):
    """One successful manifest and a crash-safe retry gate; never a Run checkpoint."""

    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, str] = Field(default_factory=dict)
    snapshot: dict[str, str] = Field(default_factory=dict)
    next_attempt_at: float = 0


class MemoryOrganizationStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    memory_enabled: bool = False
    availability: Literal["ready", "disabled", "model_not_configured", "webui_only", "configuration_unavailable"]
    active_scopes: tuple[str, ...] = ()
    last_outcome: Literal["completed", "failed", "cancelled", "concurrent_change"] | None = None
    attempts: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class OrganizationStore(DirectoryFileStore):
    """Track own effects without ever acknowledging unrelated foreground changes.

    The underlying store retains per-operation CAS. A conservative conflict flag
    also prevents a later read/retry from blessing a concurrent writer's version.
    """

    def __init__(self, root: Path, manifest: dict[str, str]) -> None:
        super().__init__(root)
        self.expected = dict(manifest)
        self.conflicted = False
        self.diff = ""
        self._effects = Lock()

    def _observe(self, path: str, version: str | None) -> None:
        if self.expected.get(path) != version:
            self.conflicted = True

    async def read(self, path: str) -> FileText:
        async with self._effects:
            try:
                current = await super().read(path)
            except MemoryStoreError as exc:
                if exc.code != "not_found" or path in self.expected:
                    self.conflicted = True
                raise
            self._observe(path, current.version)
            return current

    async def write(self, path: str, text: str, *, expected: str | None, origin: Origin) -> str:
        async with self._effects:
            self._observe(path, expected)
            try:
                version = await super().write(path, text, expected=expected, origin=origin)
            except MemoryStoreError:
                self.conflicted = True
                raise
            self.expected[path] = version
            return version

    async def move(self, source: str, destination: str, *, expected: str, origin: Origin) -> str:
        async with self._effects:
            self._observe(source, expected)
            self._observe(destination, None)
            try:
                version = await super().move(source, destination, expected=expected, origin=origin)
            except MemoryStoreError:
                self.conflicted = True
                raise
            self.expected.pop(source, None)
            self.expected[destination] = version
            return version

    async def delete(self, path: str, *, expected: str, origin: Origin) -> None:
        async with self._effects:
            self._observe(path, expected)
            try:
                await super().delete(path, expected=expected, origin=origin)
            except MemoryStoreError:
                self.conflicted = True
                raise
            self.expected.pop(path, None)

    async def purge(self) -> None:
        raise MemoryStoreError("forbidden", "Memory organization cannot purge a scope.")

    async def confirmed(self) -> bool:
        return not self.conflicted and await manifest(self) == self.expected


async def manifest(store: DirectoryFileStore) -> dict[str, str]:
    return {entry.path: entry.version for entry in await store.list()}


def _read_state(path: Path) -> OrganizationState:
    try:
        return OrganizationState.model_validate_json(path.read_bytes())
    except (FileNotFoundError, ValidationError):
        return OrganizationState()


def _write_state(path: Path, state: OrganizationState) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        staged = Path(file.name)
        file.write(state.model_dump_json().encode())
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


class MemoryOrganizer:
    """App-owned eligibility and per-scope locking, not a second execution engine."""

    def __init__(
        self,
        *,
        configuration_root: Path | None,
        current: Callable[[], Awaitable[LoadedHarnessUiConfiguration | None]],
        run: Callable[[MemoryOrganizationRun], Awaitable[RunUsageSummary]],
        webui: bool,
    ) -> None:
        self._root = configuration_root
        self._current = current
        self._execute = run
        self._webui = webui
        self._group: TaskGroup | None = None
        self._pending: dict[str, CancelScope] = {}
        self._active: set[str] = set()
        self._last: Literal["completed", "failed", "cancelled", "concurrent_change"] | None = None
        self._attempts = self._requests = self._input_tokens = self._output_tokens = 0

    def start(self, group: TaskGroup) -> None:
        if self._webui:
            self._group = group

    def stop(self) -> None:
        self._group = None
        self.cancel()

    def cancel(self) -> None:
        for scope in self._pending.values():
            scope.cancel()

    def configuration_changed(self, source: LoadedHarnessUiConfiguration) -> None:
        if not source.document.memory.enabled or not source.document.memory.auto_organize.enabled:
            self.cancel()

    def status(self, source: LoadedHarnessUiConfiguration | None) -> MemoryOrganizationStatus:
        if not self._webui:
            availability = "webui_only"
        elif source is None or self._root is None:
            availability = "configuration_unavailable"
        elif not source.document.memory.enabled or not source.document.memory.auto_organize.enabled:
            availability = "disabled"
        elif source.memory_organization_model_id is None:
            availability = "model_not_configured"
        else:
            availability = "ready"
        return MemoryOrganizationStatus(
            memory_enabled=source is not None and source.document.memory.enabled,
            availability=availability,
            active_scopes=tuple(sorted(self._active)),
            last_outcome=self._last,
            attempts=self._attempts,
            requests=self._requests,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )

    def offer(self, project_id: str | None) -> None:
        # Synchronous, bounded and inference-free: admission never waits for maintenance.
        if self._group is None or self._root is None:
            return
        for scope in memory_scopes(self._root, project_id):
            if scope.key not in self._pending and len(self._pending) < _MAX_PENDING:
                cancel = CancelScope()
                self._pending[scope.key] = cancel
                self._group.start_soon(self._attempt, scope, cancel)

    async def _attempt(self, scope: MemoryScope, cancel: CancelScope) -> None:
        try:
            with cancel:
                source = await self._current()
                if self.status(source).availability != "ready" or source is None:
                    return
                await self._organize(scope, source)
            if cancel.cancel_called:
                self._last = "cancelled"
        except Exception:
            self._last = "failed"
            # Do not log memory content or provider exception text.
            get_logger(__name__).warning("Memory organization failed for scope %s", scope.key)
        finally:
            self._active.discard(scope.key)
            self._pending.pop(scope.key, None)

    async def _organize(self, scope: MemoryScope, source: LoadedHarnessUiConfiguration) -> None:
        initial = await manifest(DirectoryFileStore(scope.root))
        if not initial:
            return
        bookkeeping = scope.root / ".a13n-memory"
        await to_thread.run_sync(lambda: bookkeeping.mkdir(parents=True, exist_ok=True))
        lock = FileLock(bookkeeping / "organize.lock", timeout=0, thread_local=False)
        try:
            await to_thread.run_sync(lock.acquire)
        except Timeout:
            return
        try:
            state_path = bookkeeping / "organization.json"
            state = await to_thread.run_sync(_read_state, state_path)
            initial = await manifest(DirectoryFileStore(scope.root))
            if not initial or initial == state.manifest or time.time() < state.next_attempt_at:
                return
            # Persist before inference: crashes and cancellations retain the old manifest,
            # partial file edits and a retry delay, never a replayable tool conversation.
            state.next_attempt_at = time.time() + _RETRY_BACKOFF
            await to_thread.run_sync(_write_state, state_path, state)
            store = OrganizationStore(scope.root, initial)
            self._active.add(scope.key)
            self._attempts += 1
            with fail_after(_DEADLINE):
                current_snapshot = await snapshot(store, initial)
                store.diff = await to_thread.run_sync(diff_context, state.snapshot, state.manifest, current_snapshot)
                await self._run(scope, store, source)
            successful_snapshot = await snapshot(store, store.expected)
            if await store.confirmed():
                state.manifest = store.expected
                state.snapshot = successful_snapshot
                state.next_attempt_at = time.time() + _SUCCESS_COOLDOWN
                await to_thread.run_sync(_write_state, state_path, state)
                self._last = "completed"
            else:
                self._last = "concurrent_change"
        finally:
            with CancelScope(shield=True):
                await to_thread.run_sync(lock.release)

    async def _run(self, scope: MemoryScope, store: OrganizationStore, source: LoadedHarnessUiConfiguration) -> None:
        usage = await self._execute(MemoryOrganizationRun(scope, store, source, store.diff))
        self._requests += usage.requests
        self._input_tokens += usage.input_tokens
        self._output_tokens += usage.output_tokens
