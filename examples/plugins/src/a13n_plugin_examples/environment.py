"""An installed Environment Provider plugin backed by Direct Local operations."""

from __future__ import annotations

from pathlib import Path

from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL, DirectLocalEnvironment
from a13n_harness.providers.environment.management import Environment, EnvironmentProviderConfiguration
from a13n_harness.providers.environment.models import EnvironmentDescriptor, EnvironmentState
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


class WorkspaceEnvironment(DirectLocalEnvironment):
    @property
    def provider_key(self) -> str:
        return PROVIDER_TYPE


def _direct_configuration(configuration: WorkspaceEnvironmentConfiguration) -> DirectLocalEnvironmentConfiguration:
    return DirectLocalEnvironmentConfiguration(root=DirectLocalRootConfiguration(path=configuration.root))


def _describe(configuration: WorkspaceEnvironmentConfiguration) -> EnvironmentDescriptor:
    return DIRECT_LOCAL.describe_environment(_direct_configuration(configuration))


def _construct(
    *,
    configuration: WorkspaceEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: object | None,
    operation_id: str,
    allow_create: bool,
) -> Environment:
    """This deterministic Provider needs no credential or SDK collaborator."""
    del runtime, operation_id, allow_create
    if state is not None:
        raise TypeError("example_workspace accepts no stored state")
    return WorkspaceEnvironment(_direct_configuration(configuration), environment_id=environment_id)


WORKSPACE_ENVIRONMENT = EnvironmentProviderDefinition(
    type=PROVIDER_TYPE,
    display_name="Example workspace",
    configuration_model=EnvironmentProviderConfiguration,
    environment_model=WorkspaceEnvironmentConfiguration,
    construct=_construct,
    describe_environment=_describe,
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
