"""Vercel-specific snapshot preparation; durable ownership belongs to lifecycle storage."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.vercel.shared import VercelConnectionConfiguration
from pydantic import BaseModel, JsonValue


class BuildTarget(BaseModel):
    session_id: str
    created_at: int


class Snapshot(BaseModel):
    id: str
    state: str


class VercelSnapshotAPI(Protocol):
    async def create(self, name: str, manifest: dict[str, JsonValue]) -> BuildTarget: ...
    async def find_builder(
        self, name: str, manifest: dict[str, JsonValue], target: BuildTarget | None
    ) -> BuildTarget | None: ...
    async def install(self, name: str, manifest: dict[str, JsonValue], target: BuildTarget) -> str: ...
    async def snapshot(self, name: str, manifest: dict[str, JsonValue], target: BuildTarget) -> Snapshot: ...
    async def find_snapshot(self, name: str, target: BuildTarget, identity: str | None) -> Snapshot | None: ...
    async def delete_builder(self, name: str, manifest: dict[str, JsonValue], target: BuildTarget) -> None: ...
    async def delete_snapshot(self, name: str, target: BuildTarget, identity: str) -> None: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class VercelDesktopPreparation:
    specification: Callable[[], dict[str, JsonValue]]
    revision: Callable[[], str]
    connect: Callable[[VercelConnectionConfiguration, TokenCredential], VercelSnapshotAPI]
