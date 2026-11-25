"""Hard local ownership for retained executions and shared Envd bindings."""

from hashlib import sha256
from pathlib import Path
from weakref import WeakValueDictionary

from anyio import CancelScope, Lock, to_thread
from filelock import FileLock, Timeout


class OwnershipLocks:
    """Hold OS locks until the owning scope has joined; process death releases them."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._held: dict[str, FileLock] = {}
        self._key_locks: WeakValueDictionary[str, Lock] = WeakValueDictionary()
        self._dir_ready = False

    def _lock_for(self, identity: str) -> Lock:
        lock = self._key_locks.get(identity)
        if lock is None:
            lock = Lock()
            self._key_locks[identity] = lock
        return lock

    async def acquire(self, identity: str) -> bool:
        key_lock = self._lock_for(identity)
        async with key_lock:
            if identity in self._held:
                return True
            with CancelScope(shield=True):
                if not self._dir_ready or not self.directory.is_dir():
                    await to_thread.run_sync(lambda: self.directory.mkdir(parents=True, exist_ok=True))
                    self._dir_ready = True
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
            key_lock = self._lock_for(identity)
            async with key_lock:
                if (lock := self._held.pop(identity, None)) is not None:
                    await to_thread.run_sync(lock.release)
        # Keep the path: unlinking can give concurrent owners different lock inodes.

    async def close(self) -> None:
        for identity in tuple(self._held):
            await self.release(identity)
