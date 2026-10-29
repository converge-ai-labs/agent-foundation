"""Cross-process checkpoint use and optimistic, nonblocking maintenance scans."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from anyio import CancelScope, to_thread
from filelock import FileLock, Timeout

from .checkpoint_lock import checkpoint_lock


class CheckpointAccess:
    """Use tokens protect files, not execution ownership or whole model Runs."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._users = root / "checkpoint-users"
        self._generation = self._users / "generation"

    def _gate(self, *, wait: bool = True):
        return checkpoint_lock(self._root, "checkpoint-reclamation", wait=wait)

    def _register(self, holder: FileLock) -> None:
        self._users.mkdir(parents=True, exist_ok=True)
        # Every access invalidates an in-progress scan, even if it finishes
        # before the collector rechecks. No timestamps or PID liveness guesses.
        self._generation.write_text(uuid4().hex, encoding="ascii")
        holder.acquire(timeout=0)

    @asynccontextmanager
    async def use(self) -> AsyncIterator[None]:
        holder = FileLock(self._users / f"{uuid4().hex}.lock", thread_local=False)
        try:
            async with self._gate():
                with CancelScope(shield=True):
                    await to_thread.run_sync(self._register, holder)
            yield
        finally:
            if holder.is_locked:
                with CancelScope(shield=True):
                    async with self._gate():
                        await to_thread.run_sync(holder.release)
                        await to_thread.run_sync(lambda: Path(holder.lock_file).unlink(missing_ok=True))

    def _idle_generation(self) -> str | None:
        # Caller holds the registration gate. Never unlink a live lock file:
        # POSIX would split its inode identity, and Windows rejects the unlink.
        for path in self._users.glob("*.lock"):
            holder = FileLock(path, thread_local=False)
            try:
                holder.acquire(timeout=0)
            except Timeout:
                return None
            else:
                holder.release()
                path.unlink(missing_ok=True)
        try:
            return self._generation.read_text(encoding="ascii")
        except FileNotFoundError:
            return ""

    async def snapshot(self) -> str | None:
        """Start only when idle; scanning itself never holds up foreground use."""
        try:
            async with self._gate(wait=False):
                return await to_thread.run_sync(self._idle_generation)
        except TimeoutError:
            return None

    @asynccontextmanager
    async def unchanged(self, generation: str) -> AsyncIterator[bool]:
        """Hold the short gate only for a bounded deletion batch, or skip it."""
        entered = False
        try:
            async with self._gate(wait=False):
                entered = True
                yield await to_thread.run_sync(self._idle_generation) == generation
        except TimeoutError:
            if entered:
                raise
            yield False
