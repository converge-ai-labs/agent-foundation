"""Thread-scoped immutable byte library outside prunable Thread scratch."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from a13n_harness.objects import ObjectInfo, ObjectStoreError, object_not_found
from anyio import to_thread
from filelock import FileLock

from a13n_harness_ui.settings import ObjectStoreSettings


class ThreadObjectStore:
    """Short cross-process catalog lock serializes reads, promotion, and eviction.

    Catalog publication precedes reclamation. A crash can leave unreferenced
    bytes, reclaimed on the next operation or maintenance pass, never a live
    catalog entry pointing at deliberately reclaimed bytes.
    """

    def __init__(self, root: Path, thread_id: str, *, policy: ObjectStoreSettings | None = None) -> None:
        if not thread_id or not all(char.isascii() and (char.isalnum() or char in "_-") for char in thread_id):
            raise ValueError("Invalid Thread identifier")
        self._directory = root / "object-library" / thread_id
        self._policy = policy or ObjectStoreSettings()

    def _operation(self, action: str, **args: Any) -> Any:
        self._directory.mkdir(parents=True, exist_ok=True)
        with FileLock(self._directory / "catalog.lock", timeout=10):
            path = self._directory / "catalog.json"
            entries = json.loads(path.read_text()) if path.exists() else {}
            current = time.time()
            entries = {
                key: entry
                for key, entry in entries.items()
                if entry["info"]["retention"] == "stored"
                or entry["accessed_at"] > current - self._policy.temporary_ttl_seconds
            }
            try:
                if action == "put":
                    data = args["data"]
                    assert isinstance(data, bytes)
                    if len(data) > self._policy.max_temporary_bytes:
                        raise ObjectStoreError(
                            "object_too_large", "Object exceeds the Host temporary Store byte limit."
                        )
                    info = ObjectInfo(
                        object_id=f"obj_{uuid4().hex}",
                        media_type=args["media_type"],
                        name=args["name"],
                        size=len(data),
                        digest=sha256(data).hexdigest(),
                    )
                    (self._directory / info.object_id).write_bytes(data)
                    entries[info.object_id] = {"info": info.model_dump(mode="json"), "accessed_at": current}
                    temporary = [key for key, entry in entries.items() if entry["info"]["retention"] == "temporary"]
                    size = sum(entries[key]["info"]["size"] for key in temporary)
                    while (
                        len(temporary) > self._policy.max_temporary_objects or size > self._policy.max_temporary_bytes
                    ):
                        key = temporary.pop(0)
                        size -= entries.pop(key)["info"]["size"]
                    return info
                if action == "list":
                    return [
                        ObjectInfo.model_validate(entry["info"])
                        for entry in list(entries.values())[args["offset"] : args["offset"] + args["limit"]]
                    ]
                if action == "prune":
                    return None
                if action == "clear":
                    entries.clear()
                    return None
                key = str(args["object_id"])
                if key not in entries:
                    raise object_not_found()
                info = ObjectInfo.model_validate(entries[key]["info"])
                if action == "read":
                    try:
                        result = (self._directory / key).read_bytes()
                    except FileNotFoundError:
                        raise object_not_found() from None
                    if len(result) != info.size or sha256(result).hexdigest() != info.digest:
                        raise ObjectStoreError("object_integrity_error", "Stored bytes failed the integrity check.")
                    entry = entries.pop(key)
                    entry["accessed_at"] = current
                    entries[key] = entry
                    return result
                if action == "info":
                    return info
                if action == "store":
                    info = info.model_copy(update={"retention": "stored"})
                    entries[key]["info"] = info.model_dump(mode="json")
                    return info
                if action == "delete":
                    entries.pop(key)
                    return None
                raise ValueError("Unknown object operation")
            finally:
                temporary_path = path.with_name(f"catalog-{uuid4().hex}.tmp")
                temporary_path.write_text(json.dumps(entries))
                temporary_path.replace(path)
                for candidate in self._directory.glob("obj_*"):
                    if candidate.name not in entries:
                        candidate.unlink(missing_ok=True)
                for candidate in self._directory.glob("catalog-*.tmp"):
                    candidate.unlink(missing_ok=True)

    async def put(self, data: bytes, *, media_type: str, name: str | None = None) -> ObjectInfo:
        return await to_thread.run_sync(lambda: self._operation("put", data=data, media_type=media_type, name=name))

    async def read(self, object_id: str) -> bytes:
        return await to_thread.run_sync(lambda: self._operation("read", object_id=object_id))

    async def info(self, object_id: str) -> ObjectInfo:
        return await to_thread.run_sync(lambda: self._operation("info", object_id=object_id))

    async def store(self, object_id: str) -> ObjectInfo:
        return await to_thread.run_sync(lambda: self._operation("store", object_id=object_id))

    async def list(self, *, offset: int = 0, limit: int = 50) -> list[ObjectInfo]:
        return await to_thread.run_sync(
            lambda: self._operation("list", offset=max(offset, 0), limit=min(max(limit, 1), 100))
        )

    async def delete(self, object_id: str) -> None:
        await to_thread.run_sync(lambda: self._operation("delete", object_id=object_id))

    async def prune(self) -> None:
        await to_thread.run_sync(lambda: self._operation("prune"))

    async def clear(self) -> None:
        await to_thread.run_sync(lambda: self._operation("clear"))


async def prune(
    root: Path, policy: ObjectStoreSettings, *, exists: Callable[[str], Awaitable[bool]] | None = None
) -> None:
    """Visit inactive Threads too; the same per-Thread lock protects live operations."""
    directory = root / "object-library"
    directories = await to_thread.run_sync(lambda: tuple(directory.iterdir()) if directory.exists() else ())
    for candidate in directories:
        if candidate.is_dir():
            store = ThreadObjectStore(root, candidate.name, policy=policy)
            if exists is not None and not await exists(candidate.name):
                await store.clear()
            else:
                await store.prune()
