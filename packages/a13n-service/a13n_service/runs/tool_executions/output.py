"""Latest-only Shell output cache. Publication never participates in execution or lease renewal."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

from a13n_harness.toolsets.shell_observation import ShellObservation
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)
_TTL_SECONDS = 10
_REFRESH_SECONDS = 5
_MAX_BYTES = 4 * 1024 * 1024


def key(execution_id: str) -> str:
    return f"a13n:tool-output:{execution_id}"


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    thread_id: str
    worker_id: str
    observation: ShellObservation


class Output:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self._pending: dict[str, Snapshot] = {}
        self._sent: dict[str, tuple[bytes, float]] = {}

    def offer(self, execution_id: str, snapshot: Snapshot) -> None:
        self._pending[execution_id] = snapshot
        self._sent.setdefault(execution_id, (b"", 0))

    def forget(self, execution_id: str) -> None:
        self._pending.pop(execution_id, None)
        self._sent.pop(execution_id, None)
        # Redis expiry bounds a concurrent write; DB terminal state always wins on reads.

    async def get(self, thread_id: str, execution_id: str, worker_id: str) -> ShellObservation | None:
        try:
            async with asyncio.timeout(min(1, self.runtime.settings.redis.timeout)):
                raw = await self.runtime.redis.get(key(execution_id))
            if raw is None or len(raw) > _MAX_BYTES:
                return None
            snapshot = Snapshot.model_validate_json(raw)
        except (RedisError, TimeoutError, ValueError):
            return None
        if snapshot.thread_id != thread_id or snapshot.worker_id != worker_id:
            return None
        return snapshot.observation

    async def flush(self) -> None:
        pending, self._pending = self._pending, {}
        sent: dict[str, tuple[bytes, float]] = {}
        current = asyncio.get_running_loop().time()
        try:
            async with (
                asyncio.timeout(min(1, self.runtime.settings.redis.timeout)),
                self.runtime.redis.pipeline(transaction=False) as pipe,
            ):
                for execution_id, snapshot in pending.items():
                    payload = snapshot.model_dump_json()
                    encoded = payload.encode()
                    if len(encoded) > _MAX_BYTES:
                        continue
                    fingerprint = hashlib.sha256(encoded).digest()
                    previous = self._sent.get(execution_id)
                    if previous is not None and previous[0] == fingerprint and current - previous[1] < _REFRESH_SECONDS:
                        continue
                    pipe.set(key(execution_id), payload, ex=_TTL_SECONDS)
                    sent[execution_id] = (fingerprint, current)
                if sent:
                    await pipe.execute()
        except (RedisError, TimeoutError) as error:
            logger.warning("Shell output cache unavailable", extra={"exception_details": exception_details(error)})
        else:
            # Cleanup may forget an execution while Redis is awaiting a reply.
            self._sent.update(
                {execution_id: value for execution_id, value in sent.items() if execution_id in self._sent}
            )

    async def run(self) -> None:
        while True:
            await asyncio.sleep(1)
            await self.flush()
