"""Offline client registration and write-only connection credentials."""

from typing import Literal

from a13n_envd_client.eip.v1.models import Identifier
from a13n_harness.environment.models import EnvironmentPermissionSet
from pydantic import BaseModel, ConfigDict, Field

from a13n_service.runs.environments.schemas import (
    DEFAULT_MOUNT_PERMISSIONS,
    EnvironmentName,
    EnvironmentView,
    WorkingDirectory,
)


class ClientEnvironmentConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    default_working_directory: WorkingDirectory
    permission_ceiling: EnvironmentPermissionSet = DEFAULT_MOUNT_PERMISSIONS
    declared_capabilities: EnvironmentPermissionSet | None = None
    description: str | None = Field(default=None, max_length=4096)


class Registration(ClientEnvironmentConfiguration):
    device_id: Identifier
    name: EnvironmentName


class ClientEnvironment(BaseModel):
    environment: EnvironmentView
    configuration: ClientEnvironmentConfiguration
    credential_version: int
    credential_present: bool


class Registered(BaseModel):
    client: ClientEnvironment
    # Repeated registration returns the original resource, never its secret.
    token: str | None = Field(default=None, repr=False)
    connection_path: str


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready", "disabled"]
