"""Daytona snapshot preparation capability; storage belongs to the lifecycle."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from a13n_harness.providers.environment.daytona.shared import DaytonaConnectionConfiguration
from a13n_harness.providers.environment.native.configuration import TokenCredential
from pydantic import BaseModel, JsonValue

from .envd_assets import Profile


class Snapshot(BaseModel):
    id: str
    state: str


class DaytonaSnapshotAPI(Protocol):
    async def find(self, name: str, manifest: dict[str, JsonValue], identity: str | None = None) -> Snapshot | None: ...
    async def create(self, name: str, manifest: dict[str, JsonValue]) -> Snapshot: ...
    async def delete(self, name: str, manifest: dict[str, JsonValue], identity: str) -> None: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class DaytonaDesktopPreparation:
    specification: Callable[[Profile, str | None], dict[str, JsonValue]]
    revision: Callable[[Profile, str | None], str]
    connect: Callable[[DaytonaConnectionConfiguration, TokenCredential], DaytonaSnapshotAPI]
