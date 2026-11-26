"""Bounded, provider-neutral observations for an unopened Environment mount."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .execution import EnvironmentExecution
from .files import FileOperator


class FileBootstrapObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    limits: tuple[int, int, int]
    status: Literal["present", "absent"]
    text: str = ""
    truncated: bool = False


class ProjectGuidanceObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agents_md: tuple[str, ...] = ()
    skill_directories: tuple[str, ...] = ()
    incomplete: bool = False


class WorkspaceOutlineObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: str
    content: str


class EnvironmentBootstrapSnapshot(BaseModel):
    """Observations relative to one mount root. Missing observations are unknown."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    files: tuple[FileBootstrapObservation, ...] = ()
    project_guidance: ProjectGuidanceObservation | None = None
    workspace_outline: tuple[WorkspaceOutlineObservation, ...] = ()


Observation = FileBootstrapObservation | ProjectGuidanceObservation | WorkspaceOutlineObservation
ObservationReader = Callable[[FileOperator], Awaitable[Observation]]
BootstrapCapture = Callable[[EnvironmentExecution], Awaitable[Observation | None]]


@dataclass(frozen=True, slots=True)
class BootstrapContext:
    """An admitted route and its Host's observations, without opening the mount."""

    path: str
    virtual_path: str
    deferred: bool
    snapshot: EnvironmentBootstrapSnapshot | None
    observe: Callable[[tuple[str, ...], ObservationReader], None]
