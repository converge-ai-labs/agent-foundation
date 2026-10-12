"""A Host-managed workspace with independent Direct Local execution connectors."""

from pathlib import Path

from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
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


def describe(configuration: WorkspaceConfiguration) -> EnvironmentDescriptor:
    return DIRECT_LOCAL.describe_environment(DIRECT_LOCAL.validate_environment({"root": {"path": "/"}}))


acme_environment = EnvironmentProviderDefinition(
    type="acme_workspace",
    display_name="Acme project workspace",
    configuration_model=WorkspaceConnection,
    environment_model=WorkspaceConfiguration,
    connector_factory=connector,
    describe_environment=describe,
)
