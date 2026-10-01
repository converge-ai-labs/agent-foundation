"""Host-local plaintext API keys, kept outside configuration and Run snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, JsonValue, SecretStr, ValidationError

from a13n_harness_ui.configuration.models import ResourceId, StrictModel
from a13n_harness_ui.errors import HarnessUiError

from .auth import HostAuthDocument


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
    """API-key projection of the shared Host authentication document."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.document = HostAuthDocument(path)

    async def _read(self) -> _Document:
        try:
            return _Document.model_validate(await self.document.read())
        except ValidationError:
            raise HarnessUiError(
                "The Host authentication store could not be read or updated.", code="api_key_store_unavailable"
            ) from None

    async def list(self) -> tuple[ApiKeyStatus, ...]:
        document = await self._read()
        return tuple(ApiKeyStatus(credential_ref=ref) for ref in sorted(document.keys))

    async def load(self, reference: str) -> str | None:
        document = await self._read()
        key = document.keys.get(reference)
        return key.get_secret_value() if key is not None else None

    async def put(self, value: ApiKeyInput) -> ApiKeyStatus:
        await self._change(value.credential_ref, value.key)
        return ApiKeyStatus(credential_ref=value.credential_ref)

    async def delete(self, reference: str) -> None:
        await self._change(reference, None)

    async def _change(self, reference: str, key: SecretStr | None) -> None:
        def change(raw: dict[str, Any]) -> None:
            document = _Document.model_validate(raw)
            keys = {ref: val.get_secret_value() for ref, val in document.keys.items()}
            if key is None:
                keys.pop(reference, None)
            else:
                keys[reference] = key.get_secret_value()
            raw["keys"] = keys

        await self.document.update(change)
