"""Soft-budget LRU for published skill packages, coordinated in their physical filesystem.

Readers publish unique pins before checking a per-package eviction gate. The gate's creator checks pins
before deleting and alone unlinks the gate, once. Neither pins nor gates expire: an unknown mutation keeps
its protection until the operator has quiesced the target. Other packages can still be acquired.
"""

import posixpath
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

import anyio
from a13n_harness.environment.files import (
    FileMkdirOperation,
    FileOperation,
    FileOperator,
    FileRemoveOperation,
    FileStatOperation,
    FileWriteTextOperation,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_logging import get_logger

ROOT = "/workspace/.agents/skill-cache"
_STATE = f"{ROOT}/.cache"
# Skills per file batch: three pin operations each stay within the Harness batch limit.
FILE_BATCH = 32
logger = get_logger(__name__)


@dataclass(slots=True)
class Use:
    path: str
    # An outstanding publication may outlive execution teardown; retain its pin in that case.
    retain: bool = False


class Cache:
    """Pins owned by one primary environment execution, including all inline agents."""

    def __init__(self, *, wait_seconds: float = 10):
        self.wait_seconds = wait_seconds
        self.uses: list[Use] = []

    async def acquire_many(self, files: FileOperator, digests: Sequence[str]) -> list[Use]:
        uses: list[Use] = []
        for offset in range(0, len(digests), FILE_BATCH):
            batch = digests[offset : offset + FILE_BATCH]
            created = [Use(f"{_STATE}/pins/{digest}/{uuid4().hex}") for digest in batch]
            operations: list[FileOperation] = []
            for digest, use in zip(batch, created, strict=True):
                operations.extend(
                    (
                        FileMkdirOperation(path=f"{_STATE}/pins/{digest}", parents=True, exist_ok=True),
                        FileWriteTextOperation(path=use.path, text="", mode="create"),
                        FileStatOperation(path=f"{_STATE}/evict/{digest}", missing_ok=True),
                    )
                )
            # Own pins only after a confirmed batch; a failed or cancelled one may still create them remotely.
            results = await files.batch(operations)
            self.uses.extend(created)
            uses.extend(created)
            pending = [digest for digest, result in zip(batch, results[2::3], strict=True) if result is not None]
            try:
                with anyio.fail_after(self.wait_seconds):
                    while pending:
                        await anyio.sleep(0.05)
                        gates = await files.batch(
                            [FileStatOperation(path=f"{_STATE}/evict/{digest}", missing_ok=True) for digest in pending]
                        )
                        pending = [digest for digest, gate in zip(pending, gates, strict=True) if gate is not None]
            except TimeoutError as error:
                raise EnvironmentError(
                    "Skill package eviction has not completed",
                    code="environment_unavailable",
                    details={"digests": ",".join(pending)},
                ) from error
        return uses

    async def touch_many(self, files: FileOperator, digests: Sequence[str]) -> None:
        for offset in range(0, len(digests), FILE_BATCH):
            operations: list[FileOperation] = [
                FileMkdirOperation(path=f"{_STATE}/entries", parents=True, exist_ok=True),
            ]
            timestamp = str(time.time_ns())
            operations.extend(
                FileWriteTextOperation(path=f"{_STATE}/entries/{digest}", text=timestamp, mode="upsert")
                for digest in digests[offset : offset + FILE_BATCH]
            )
            await files.batch(operations)

    async def release(self, files: FileOperator, *, root: str = ROOT) -> None:
        """Called before the primary execution closes its file client; unknown pins are retained."""
        uses, self.uses = self.uses, []
        paths = [f"{root.rstrip('/')}/{use.path.removeprefix(ROOT + '/')}" for use in uses if not use.retain]
        for offset in range(0, len(paths), FILE_BATCH):
            with anyio.move_on_after(5, shield=True) as cleanup:
                try:
                    await files.batch(
                        [
                            FileRemoveOperation(path=path, missing_ok=True)
                            for path in paths[offset : offset + FILE_BATCH]
                        ]
                    )
                except EnvironmentError as error:
                    logger.warning("Skill pin release failed", extra={"code": error.code})
            if cleanup.cancel_called:
                logger.warning("Skill pin release timed out; unfinished pins are retained")

    async def trim(self, files: FileOperator, *, capacity: int) -> None:
        """Best effort: capacity and cleanup failures never reject a newly acquired package."""
        try:
            await self._trim(files, capacity)
        except EnvironmentError as error:
            logger.warning("Skill cache reclamation deferred", extra={"code": error.code})

    async def _trim(self, files: FileOperator, capacity: int) -> None:
        if capacity == 0:
            return
        paths: list[str] = []
        offset = 0
        while True:
            page = await files.list(f"{_STATE}/entries", offset=offset, max_results=256, include_hidden=True)
            for item in page.entries:
                if re.fullmatch(r"[0-9a-f]{64}", posixpath.basename(item.path)):
                    paths.append(item.path)
            if not page.has_more:
                break
            offset += len(page.entries)
        if len(paths) <= capacity:
            return
        # Reading recency is necessary only when the package count exceeds capacity.
        entries: list[tuple[int, str]] = []
        for path in paths:
            try:
                entries.append((int(await files.read_bytes(path, length=32)), posixpath.basename(path)))
            except ValueError:
                continue
            except EnvironmentError as error:
                if error.code != "environment_not_found":
                    raise
        await files.mkdir(f"{_STATE}/evict", parents=True, exist_ok=True)
        remaining = min(len(entries) - capacity, 32)
        for _, digest in sorted(entries):
            if remaining <= 0:
                break
            if await self._evict(files, digest):
                remaining -= 1

    async def _evict(self, files: FileOperator, digest: str) -> bool:
        gate = f"{_STATE}/evict/{digest}"
        try:
            await files.write_text(gate, uuid4().hex, mode="create")
        except EnvironmentError as error:
            if error.code == "environment_conflict":
                return False
            raise
        release = True
        try:
            pins = await files.list(f"{_STATE}/pins/{digest}", max_results=1, include_hidden=True)
            if pins.entries or not await _exists(files, f"{ROOT}/{digest}"):
                return False
            # Once dispatched, an unknown recursive remove must keep this gate. Never retry it.
            release = False
            await files.remove(f"{ROOT}/{digest}", recursive=True)
            await files.remove(f"{_STATE}/entries/{digest}")
            release = True
            return True
        finally:
            if release:
                # Only this creator removes the gate. A late single unlink cannot target a later gate:
                # creating that later gate first requires this unlink to have happened.
                with anyio.CancelScope(shield=True):
                    await files.remove(gate)


async def _exists(files: FileOperator, path: str) -> bool:
    try:
        await files.stat(path)
        return True
    except EnvironmentError as error:
        if error.code == "environment_not_found":
            return False
        raise
