"""Modal-specific immutable image preparation capability; no business storage."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration, ModalCredential
from pydantic import JsonValue

from .envd_assets import Profile


class ModalImageAPI(Protocol):
    async def build(self, manifest: dict[str, JsonValue], scope: str, image_id: str | None) -> str: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class ModalDesktopPreparation:
    specification: Callable[[Profile, str | None], dict[str, JsonValue]]
    revision: Callable[[Profile, str | None], str]
    locator: Callable[[ModalConnectionConfiguration], str]
    connect: Callable[[ModalConnectionConfiguration, ModalCredential], ModalImageAPI]
