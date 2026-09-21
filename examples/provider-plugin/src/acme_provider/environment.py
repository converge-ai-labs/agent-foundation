"""A project workspace provider usable directly or through Service templates."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from a13n_harness.providers.environment import EnvironmentProviderDefinition
from a13n_harness.providers.environment.direct_local.configuration import DirectLocalEnvironmentConfiguration
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL, DirectLocalEnvironment
from a13n_harness.providers.environment.models import EnvironmentDescriptor, EnvironmentState
from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkspaceConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    root: Path

    @field_validator("root")
    @classmethod
    def absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Workspace root must be absolute")
        return value


class WorkspaceConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    directory: str = Field(default="projects", pattern=r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class WorkspaceRuntime:
    root: Path


class Workspace(DirectLocalEnvironment):
    def __init__(
        self,
        configuration: WorkspaceConfiguration,
        environment_id: str,
        runtime: WorkspaceRuntime,
        *,
        allow_create: bool,
    ):
        self.workspace = runtime.root / configuration.directory / environment_id
        self.allow_create = allow_create
        super().__init__(
            DirectLocalEnvironmentConfiguration.model_validate({"root": {"path": self.workspace}}),
            environment_id=environment_id,
        )

    @property
    def provider_key(self) -> str:
        return "acme_workspace"

    async def _prepare(self, *, mount_id: str) -> None:
        if self.allow_create:
            await asyncio.to_thread(self.workspace.mkdir, parents=True, exist_ok=True)
        await super()._prepare(mount_id=mount_id)


async def runtime(
    *,
    configuration: WorkspaceConnection,
    credential: object | None,
) -> WorkspaceRuntime:
    del credential
    return WorkspaceRuntime(configuration.root)


def construct(
    *,
    configuration: WorkspaceConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: WorkspaceRuntime | None,
    operation_id: str,
    allow_create: bool,
) -> Workspace:
    del operation_id
    if runtime is None or state is not None:
        raise ValueError("Workspace requires a stateless local runtime")
    return Workspace(configuration, environment_id, runtime, allow_create=allow_create)


def describe(configuration: WorkspaceConfiguration) -> EnvironmentDescriptor:
    del configuration
    return DIRECT_LOCAL.describe_environment(
        DirectLocalEnvironmentConfiguration.model_validate({"root": {"path": "/"}})
    )


acme_environment = EnvironmentProviderDefinition(
    type="acme_workspace",
    display_name="Acme project workspace",
    configuration_model=WorkspaceConnection,
    environment_model=WorkspaceConfiguration,
    construct=construct,
    describe_environment=describe,
    runtime_factory=runtime,
    supports_stop=True,
    supports_destroy=True,
)
