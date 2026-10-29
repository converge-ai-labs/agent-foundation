"""Short, cross-process exclusion for selected checkpoint reads and replacement."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

from anyio import CancelScope, current_time, sleep, to_thread
from filelock import FileLock, Timeout


@asynccontextmanager
async def checkpoint_lock(root: Path, thread_id: str) -> AsyncGenerator[None]:
    """Protect file use, not Run ownership; never hold across model or tool work."""
    directory = root / "checkpoint-locks"
    await to_thread.run_sync(lambda: directory.mkdir(mode=0o700, parents=True, exist_ok=True))
    identity = sha256(thread_id.encode()).hexdigest()
    lock = FileLock(directory / f"{identity}.lock", timeout=0, thread_local=False, mode=0o600)
    acquired = False
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
                await sleep(min(0.05, remaining))
        yield
    finally:
        if acquired:
            with CancelScope(shield=True):
                await to_thread.run_sync(lock.release)
