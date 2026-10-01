"""The single atomic writer for the Host's versioned auth.json document."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from anyio import to_thread
from filelock import FileLock, Timeout
from pydantic import ValidationError

from a13n_harness_ui.errors import HarnessUiError

_LIMIT = 2 * 1024 * 1024


class HostAuthDocument:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open("rb") as source:
                raw = source.read(_LIMIT + 1)
        except FileNotFoundError:
            return {"version": 1, "keys": {}}
        if len(raw) > _LIMIT:
            raise ValueError("oversize")
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("version", 1) != 1:
            raise ValueError("incompatible auth document")
        return value

    async def read(self) -> dict[str, Any]:
        return await self._run(self._read)

    async def update[T](self, change: Callable[[dict[str, Any]], T]) -> T:
        return await self._run(lambda: self._change(change))

    @staticmethod
    async def _run[T](operation: Callable[[], T]) -> T:
        try:
            return await to_thread.run_sync(operation)
        except (OSError, ValueError, ValidationError, Timeout):
            raise HarnessUiError(
                "The Host authentication store could not be read or updated.", code="api_key_store_unavailable"
            ) from None

    def _change[T](self, change: Callable[[dict[str, Any]], T]) -> T:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.path.parent.chmod(0o700)
        with FileLock(str(self.path) + ".lock", timeout=10, mode=0o600):
            document = self._read()
            result = change(document)
            raw = json.dumps(document, allow_nan=False)
            if len(raw.encode("utf-8")) > _LIMIT:
                raise ValueError("oversize")
            fd, name = tempfile.mkstemp(prefix=".auth-", dir=self.path.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as target:
                    target.write(raw)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return result
