"""Short, cross-process exclusion for selected checkpoint reads and replacement."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

from anyio import CancelScope, fail_after, sleep, to_thread
from filelock import FileLock, Timeout


@asynccontextmanager
async def checkpoint_lock(root: Path, thread_id: str) -> AsyncIterator[None]:
    """Protect file use, not Run ownership; never hold across model or tool work."""
    directory = root / "checkpoint-locks"
    await to_thread.run_sync(lambda: directory.mkdir(mode=0o700, parents=True, exist_ok=True))
    identity = sha256(thread_id.encode()).hexdigest()
    lock = FileLock(directory / f"{identity}.lock", timeout=0, thread_local=False, mode=0o600)
    acquired = False
    try:
        with fail_after(30):
            while not acquired:
                try:
                    await to_thread.run_sync(lock.acquire)
                    acquired = True
                except Timeout:
                    await sleep(0.05)
        yield
    finally:
        if acquired:
            with CancelScope(shield=True):
                await to_thread.run_sync(lock.release)
