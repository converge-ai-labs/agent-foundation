"""Host-local plaintext API keys, kept outside configuration and Run snapshots."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from anyio import to_thread
from filelock import FileLock, Timeout
from pydantic import ConfigDict, Field, JsonValue, SecretStr, ValidationError

from a13n_harness_ui.configuration.models import ResourceId, StrictModel
from a13n_harness_ui.errors import HarnessUiError


class ApiKeyInput(StrictModel):
    credential_ref: ResourceId
    key: SecretStr = Field(repr=False, min_length=1, max_length=32768)


class ApiKeyStatus(StrictModel):
    credential_ref: ResourceId


class _Document(StrictModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    # Pydantic uses this narrower annotation to validate retained JSON extras.
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)  # pyright: ignore[reportIncompatibleVariableOverride]

    version: Literal[1] = 1
    keys: dict[ResourceId, SecretStr] = Field(default_factory=dict, repr=False)


class ApiKeyStore:
    """Serialize read-modify-write operations without retaining credential values."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> _Document:
        try:
            with self.path.open("rb") as source:
                raw = source.read(2 * 1024 * 1024 + 1)
        except FileNotFoundError:
            return _Document()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("oversize")
        return _Document.model_validate_json(raw)

    async def list(self) -> tuple[ApiKeyStatus, ...]:
        document = await self._run(self._read)
        return tuple(ApiKeyStatus(credential_ref=ref) for ref in sorted(document.keys))

    async def load(self, reference: str) -> str | None:
        document = await self._run(self._read)
        key = document.keys.get(reference)
        return key.get_secret_value() if key is not None else None

    async def put(self, value: ApiKeyInput) -> ApiKeyStatus:
        await self._run(lambda: self._change(value.credential_ref, value.key))
        return ApiKeyStatus(credential_ref=value.credential_ref)

    async def delete(self, reference: str) -> None:
        await self._run(lambda: self._change(reference, None))

    @staticmethod
    async def _run[T](operation: Callable[[], T]) -> T:
        # The worker is not abandoned on cancellation during an atomic publication.
        try:
            return await to_thread.run_sync(operation)
        except (OSError, ValueError, ValidationError, Timeout):
            raise HarnessUiError(
                "The Host API-key store could not be read or updated.", code="api_key_store_unavailable"
            ) from None

    def _change(self, reference: str, key: SecretStr | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.path.parent.chmod(0o700)
        with FileLock(str(self.path) + ".lock", timeout=10, mode=0o600):
            document = self._read()
            keys = dict(document.keys)
            if key is None:
                keys.pop(reference, None)
            else:
                keys[reference] = key
            payload = document.model_dump(mode="json")
            payload["keys"] = {ref: val.get_secret_value() for ref, val in keys.items()}
            raw = json.dumps(payload, allow_nan=False)
            if len(raw.encode("utf-8")) > 2 * 1024 * 1024:
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
