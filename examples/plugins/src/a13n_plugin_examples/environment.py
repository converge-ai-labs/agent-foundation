"""An installed Environment Provider plugin backed by Direct Local operations."""

from __future__ import annotations

from pathlib import Path

from a13n_harness.providers.authentication import Authentication, CredentialMode
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL, DirectLocalEnvironment
from a13n_harness.providers.environment.management import EmptyProviderConfiguration, Environment
from a13n_harness.providers.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.plugins import ProviderManifest
from pydantic import BaseModel, ConfigDict, field_validator

PROVIDER_TYPE = "example_workspace"


class WorkspaceEnvironmentConfiguration(BaseModel):
    """Credential-free schema version 1 target recipe for the example Provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: Path
    read_only: bool = True

    @field_validator("root")
    @classmethod
    def _absolute_root(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if "\x00" in str(expanded):
            raise ValueError("root must not contain NUL")
        if not expanded.is_absolute():
            raise ValueError("root must be absolute")
        return expanded


class WorkspaceEnvironment(DirectLocalEnvironment):
    @property
    def provider_key(self) -> str:
        return PROVIDER_TYPE


def _direct_configuration(configuration: WorkspaceEnvironmentConfiguration) -> DirectLocalEnvironmentConfiguration:
    return DirectLocalEnvironmentConfiguration(
        root=DirectLocalRootConfiguration(path=configuration.root, read_only=configuration.read_only)
    )


def _describe(configuration: BaseModel) -> EnvironmentDescriptor:
    if not isinstance(configuration, WorkspaceEnvironmentConfiguration):
        raise TypeError("example_workspace requires WorkspaceEnvironmentConfiguration")
    return DIRECT_LOCAL.describe_environment(_direct_configuration(configuration))


def _construct(
    *, configuration: BaseModel, environment_id: str, state: EnvironmentState | None, runtime: None
) -> Environment:
    """This deterministic Provider needs no credential or SDK collaborator."""
    del runtime
    if not isinstance(configuration, WorkspaceEnvironmentConfiguration) or state is not None:
        raise TypeError("example_workspace requires WorkspaceEnvironmentConfiguration and no stored state")
    return WorkspaceEnvironment(_direct_configuration(configuration), environment_id=environment_id)


WORKSPACE_ENVIRONMENT = EnvironmentProviderDefinition[EmptyProviderConfiguration, EmptyProviderConfiguration, None](
    type=PROVIDER_TYPE,
    display_name="Example workspace",
    configuration_model=EmptyProviderConfiguration,
    credential_model=None,
    environment_models={"1": WorkspaceEnvironmentConfiguration},
    construct=_construct,
    describe_environment=_describe,
    authentication=Authentication(mode=CredentialMode.forbidden),
    supports_stop=True,
    supports_destroy=True,
)

manifest = ProviderManifest(api_version=1, environment=(WORKSPACE_ENVIRONMENT,))

__all__ = [
    "PROVIDER_TYPE",
    "WORKSPACE_ENVIRONMENT",
    "WorkspaceEnvironment",
    "WorkspaceEnvironmentConfiguration",
    "manifest",
]
