"""An installed Environment Provider plugin backed by Direct Local operations."""

from __future__ import annotations

from pathlib import Path

from a13n_environment import EnvironmentConnector
from a13n_environment.definition import EnvironmentProviderDefinition
from a13n_environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.management import EnvironmentProviderConfiguration
from a13n_environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.plugins import ProviderManifest
from pydantic import BaseModel, ConfigDict, field_validator

PROVIDER_TYPE = "example_workspace"


class WorkspaceEnvironmentConfiguration(BaseModel):
    """Credential-free schema version 1 target recipe for the example Provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: Path

    @field_validator("root")
    @classmethod
    def _absolute_root(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if "\x00" in str(expanded):
            raise ValueError("root must not contain NUL")
        if not expanded.is_absolute():
            raise ValueError("root must be absolute")
        return expanded


def _direct_configuration(configuration: WorkspaceEnvironmentConfiguration) -> DirectLocalEnvironmentConfiguration:
    return DirectLocalEnvironmentConfiguration(root=DirectLocalRootConfiguration(path=configuration.root))


def _describe(configuration: WorkspaceEnvironmentConfiguration) -> EnvironmentDescriptor:
    return DIRECT_LOCAL.describe_environment(_direct_configuration(configuration))


def _connector(
    *,
    configuration: EnvironmentProviderConfiguration,
    credential: BaseModel | None,
    environment: WorkspaceEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: object | None,
) -> EnvironmentConnector:
    """Map the plugin's recipe to the existing Direct Local execution provider."""
    del configuration, credential, runtime
    return DIRECT_LOCAL.execution_connector(
        _direct_configuration(environment), environment_id=environment_id, state=state
    )


WORKSPACE_ENVIRONMENT = EnvironmentProviderDefinition(
    type=PROVIDER_TYPE,
    display_name="Example workspace",
    configuration_model=EnvironmentProviderConfiguration,
    environment_model=WorkspaceEnvironmentConfiguration,
    connector_factory=_connector,
    describe_environment=_describe,
    supports_managed=False,
)

manifest = ProviderManifest(api_version=2, environment=(WORKSPACE_ENVIRONMENT,))

__all__ = [
    "PROVIDER_TYPE",
    "WORKSPACE_ENVIRONMENT",
    "WorkspaceEnvironmentConfiguration",
    "manifest",
]
