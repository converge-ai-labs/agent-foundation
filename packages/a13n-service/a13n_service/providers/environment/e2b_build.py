"""Registry contract for E2B's private desktop builder; implementation owns no business storage."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, Field, JsonValue


class Build(BaseModel):
    template_id: str = Field(alias="templateID", pattern=r"^[a-zA-Z0-9_-]+$")
    build_id: str = Field(alias="buildID", pattern=r"^[a-zA-Z0-9_-]+$")


class BuildStatus(Build):
    status: Literal["waiting", "building", "ready", "error"]


class TemplateBuild(BaseModel):
    build_id: str = Field(alias="buildID", pattern=r"^[a-zA-Z0-9_-]+$")
    status: Literal["waiting", "building", "ready", "error"]


class Template(BaseModel):
    template_id: str = Field(alias="templateID", pattern=r"^[a-zA-Z0-9_-]+$")
    public: bool
    names: list[str] = []
    aliases: list[str] = []
    builds: list[TemplateBuild]


class E2BTemplateAPI(Protocol):
    async def reserve(self, name: str) -> Build: ...
    async def find(self, name: str) -> Template | None: ...
    async def inspect(self, template_id: str, name: str) -> Template | None: ...
    async def submit(self, build: Build, spec: dict[str, JsonValue]) -> None: ...
    async def status(self, build: Build) -> BuildStatus: ...
    async def delete(self, template_id: str, name: str) -> None: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class E2BDesktopPreparation:
    specification: Callable[[], dict[str, JsonValue]]
    revision: Callable[[], str]
    connect: Callable[[str, str], E2BTemplateAPI]
