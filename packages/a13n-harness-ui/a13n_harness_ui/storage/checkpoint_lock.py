"""Short, cross-process exclusion for selected checkpoint reads and replacement."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

from anyio import CancelScope, current_time, sleep, to_thread
from filelock import FileLock, Timeout

_PREPARED_DIRS: set[Path] = set()


@asynccontextmanager
async def checkpoint_lock(root: Path, thread_id: str) -> AsyncGenerator[None]:
    """Protect file use, not Run ownership; never hold across model or tool work."""
    directory = root / "checkpoint-locks"
    if directory not in _PREPARED_DIRS or not directory.is_dir():
        await to_thread.run_sync(lambda: directory.mkdir(mode=0o700, parents=True, exist_ok=True))
        _PREPARED_DIRS.add(directory)
    identity = sha256(thread_id.encode()).hexdigest()
    lock = FileLock(directory / f"{identity}.lock", timeout=0, thread_local=False, mode=0o600)
    acquired = False
    delay = 0.005
    try:
        # Polling already provides cancellation checkpoints. A long-lived timeout
        # timer would capture the caller's RunContext even after being cancelled.
        deadline = current_time() + 30
        while not acquired:
            remaining = deadline - current_time()
            if remaining <= 0:
                raise TimeoutError
            try:
                await to_thread.run_sync(lock.acquire)
                acquired = True
            except Timeout:
                await sleep(min(delay, remaining))
                delay = min(delay * 2, 0.025)
        yield
    finally:
        if acquired:
            with CancelScope(shield=True):
                await to_thread.run_sync(lock.release)
