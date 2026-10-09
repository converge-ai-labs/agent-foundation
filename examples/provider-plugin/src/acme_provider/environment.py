"""A Host-managed workspace with independent Direct Local execution connectors."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from a13n_environment import EnvironmentConnector, EnvironmentProvider, EnvironmentProviderDefinition, EnvironmentStatus
from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.errors import EnvironmentProviderErrorCategory, provider_error
from a13n_environment.models import EnvironmentDescriptor, EnvironmentState
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


def workspace(account: WorkspaceConnection, recipe: WorkspaceConfiguration, environment_id: str) -> Path:
    if Path(environment_id).name != environment_id or environment_id in {".", ".."}:
        raise ValueError("Environment identity must be one path component")
    return account.root / recipe.directory / environment_id


def connector(
    *,
    configuration: WorkspaceConnection,
    credential: object,
    environment: WorkspaceConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: object,
) -> EnvironmentConnector:
    del credential, runtime
    return DIRECT_LOCAL.execution_connector(
        {"root": {"path": str(workspace(configuration, environment, environment_id))}},
        environment_id=environment_id,
        state=state,
    )


class WorkspaceProvider(EnvironmentProvider[WorkspaceConfiguration]):
    def __init__(self, account: WorkspaceConnection):
        self.account = account

    def _root(self, environment: object, environment_id: str, state: EnvironmentState | None) -> Path:
        if state is not None:
            raise ValueError("Workspace uses its directory recipe and accepts no stored state")
        return workspace(self.account, WorkspaceConfiguration.model_validate(environment), environment_id)

    async def create(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None = None
    ) -> None:
        await asyncio.to_thread(self._root(environment, environment_id, state).mkdir, parents=True, exist_ok=True)

    async def start(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> None:
        if not await asyncio.to_thread(self._root(environment, environment_id, state).is_dir):
            raise provider_error("acme_workspace", "provider_target_missing", EnvironmentProviderErrorCategory.MISSING)

    async def inspect(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> EnvironmentStatus:
        exists = await asyncio.to_thread(self._root(environment, environment_id, state).is_dir)
        return EnvironmentStatus("running" if exists else "absent", None)

    async def stop(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> None:
        raise provider_error(
            "acme_workspace", "provider_operation_unsupported", EnvironmentProviderErrorCategory.UNSUPPORTED
        )

    async def destroy(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> None:
        raise provider_error(
            "acme_workspace", "provider_operation_unsupported", EnvironmentProviderErrorCategory.UNSUPPORTED
        )

    async def keepalive(
        self,
        environment: object,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        deadline: datetime,
        operation_id: str,
    ) -> datetime | None:
        raise provider_error(
            "acme_workspace", "provider_operation_unsupported", EnvironmentProviderErrorCategory.UNSUPPORTED
        )

    def keepalive_horizon(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> timedelta:
        return timedelta(0)

    def execution_connector(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> EnvironmentConnector:
        root = self._root(environment, environment_id, state)
        return DIRECT_LOCAL.execution_connector({"root": {"path": str(root)}}, environment_id=environment_id)

    async def close(self) -> None:
        pass


async def open_provider(
    *, configuration: WorkspaceConnection, credential: object, runtime: object
) -> WorkspaceProvider:
    del credential, runtime
    return WorkspaceProvider(configuration)


def describe(configuration: WorkspaceConfiguration) -> EnvironmentDescriptor:
    return DIRECT_LOCAL.describe_environment(DIRECT_LOCAL.validate_environment({"root": {"path": "/"}}))


acme_environment = EnvironmentProviderDefinition(
    type="acme_workspace",
    display_name="Acme project workspace",
    configuration_model=WorkspaceConnection,
    environment_model=WorkspaceConfiguration,
    provider_factory=open_provider,
    connector_factory=connector,
    describe_environment=describe,
)
