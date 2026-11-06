"""Hard local ownership for retained executions and shared Envd bindings."""

from hashlib import sha256
from pathlib import Path

from anyio import CancelScope, Lock, to_thread
from filelock import FileLock, Timeout


class OwnershipLocks:
    """Hold OS locks until the owning scope has joined; process death releases them."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._held: dict[str, FileLock] = {}
        self._guard = Lock()

    async def acquire(self, identity: str) -> bool:
        async with self._guard:
            if identity in self._held:
                return True
            with CancelScope(shield=True):
                await to_thread.run_sync(lambda: self.directory.mkdir(parents=True, exist_ok=True))
                path = self.directory / f"{sha256(identity.encode()).hexdigest()}.lock"
                lock = FileLock(path, timeout=0, thread_local=False, mode=0o600)
                try:
                    await to_thread.run_sync(lock.acquire)
                except Timeout:
                    return False
                self._held[identity] = lock
            return True

    async def release(self, identity: str) -> None:
        with CancelScope(shield=True):
            async with self._guard:
                if (lock := self._held.pop(identity, None)) is not None:
                    await to_thread.run_sync(lock.release)
        # Keep the path: unlinking can give concurrent owners different lock inodes.

    async def close(self) -> None:
        for identity in tuple(self._held):
            await self.release(identity)
