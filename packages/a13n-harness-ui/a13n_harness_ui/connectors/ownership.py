"""One local automation election held through producers, root cleanup and resources."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from filelock import AsyncFileLock, Timeout


@asynccontextmanager
async def automation_owner(root: Path) -> AsyncIterator[bool]:
    lock = AsyncFileLock(root / ".bot-runtime.lock", timeout=0)
    try:
        await lock.acquire()
    except Timeout:
        yield False
    else:
        try:
            yield True
        finally:
            await lock.release()
