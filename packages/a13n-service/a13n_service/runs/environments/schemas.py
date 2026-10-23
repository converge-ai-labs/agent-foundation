"""Environment instances and thread mounts as the API accepts and returns them, and the stored handle/failure."""

from datetime import datetime
from typing import Annotated, Literal

from a13n_harness.environment.models import (
    FILE_EXECUTION_ACTIONS,
    EnvironmentAction,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, SecretStr, StringConstraints

from a13n_service.infra.ids import ObjectId
from a13n_service.providers import envd
from a13n_service.resources.metrics_data_sources.schemas import Labels

# Desired mounts one thread holds at most; a run freezes them, plus a primary sandbox its agent reserves.
MAX_MOUNTS = 32
DEFAULT_MOUNT_PERMISSIONS = EnvironmentPermissionSet(operations=FILE_EXECUTION_ACTIONS)
# The Harness mount-name rule; `workspace` is the primary mount.
MountName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]


def _canonical_directory(value: str) -> str:
    """An absolute path inside the environment's own namespace, never a route out of it."""
    segments = value[1:].split("/") if value != "/" else []
    if len(value) > 1024 or not value.startswith("/") or "\x00" in value or {"", ".", ".."} & set(segments):
        raise ValueError("working_directory must be a canonical absolute path")
    return value


WorkingDirectory = Annotated[str, AfterValidator(_canonical_directory)]
EnvironmentName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
EnvdEndpoint = Annotated[str, Field(min_length=1, max_length=2048), AfterValidator(envd.normalized_endpoint)]


def _envd_token(token: SecretStr) -> SecretStr:
    envd.checked_token(token.get_secret_value())
    return token


EnvdToken = Annotated[SecretStr, Field(min_length=1, max_length=4096), AfterValidator(_envd_token)]

type Certainty = Literal["not_dispatched", "known", "unknown"]


class Handle(BaseModel):
    """What reaches one managed instance: the recipe it was built from, which its provider state is bound to, that
    state, and the version of the provider credential that last reached it. A stateless provider has no state."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    recipe: dict[str, JsonValue]
    state: EnvironmentState | None = None
    credential_version: str | None = None
    e2b_desktop_template: str | None = None
    modal_desktop_image: str | None = None
    daytona_desktop_snapshot: str | None = None


class EnvironmentFailure(BaseModel):
    """The last error of the outstanding operation or, on a ready instance, of its last renewal.

    `unknown` means the call may have taken effect: only the same operation may continue. `permanent` failures
    refuse new mounts and acceptance until the cause is fixed or the instance is deleted; `environment_lost` means
    the provider no longer has the sandbox.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str
    certainty: Certainty
    permanent: bool
    operation_id: str | None
    at: datetime


class MetricsApplication(BaseModel):
    """The frozen sampler/exporter selection; applied_at confirms creation, not delivery."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str
    source_version: int
    collection: Literal["native", "proxy", "pull"]
    sample_interval_ms: int
    applied_at: datetime | None = None


class EnvironmentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str
    # NULL for an external target.
    provider_id: str | None
    template_id: str | None
    # An external target's native identity and the endpoint its daemon serves; NULL for a managed sandbox.
    device_id: str | None
    endpoint: str | None
    owner_principal_id: str | None
    name: str
    metrics_data_source_id: str | None = None
    metrics_labels: Labels = Field(default_factory=dict)
    metrics_application: MetricsApplication | None = None
    status: str
    operation_id: str | None
    operation_started_at: datetime | None
    failure: EnvironmentFailure | None
    last_used_at: datetime | None
    version: int
    created_by_id: str
    created_at: datetime
    updated_at: datetime


class EnvironmentPage(BaseModel):
    items: list[EnvironmentView]
    next_cursor: str | None


class ManagedEnvironmentCreate(BaseModel):
    """A workspace-managed sandbox reserved from a template, for threads to mount; maintenance creates it."""

    model_config = ConfigDict(extra="forbid")
    template_id: ObjectId
    metrics_data_source_id: ObjectId | None = None
    metrics_labels: Labels = Field(default_factory=dict)
    # Defaults to the template's name.
    name: EnvironmentName | None = None


class ExternalTargetCreate(BaseModel):
    """An envd daemon someone runs, registered by its endpoint and the token it accepts."""

    model_config = ConfigDict(extra="forbid")
    endpoint: EnvdEndpoint
    token: EnvdToken
    metrics_data_source_id: ObjectId | None = None
    metrics_labels: Labels = Field(default_factory=dict)
    # Defaults to the device ID the daemon states.
    name: EnvironmentName | None = None


class EnvironmentUpdate(BaseModel):
    """Fields left out stay unchanged. Only an external target has an endpoint and token; a new endpoint comes with
    its token, so a stored token never reaches an endpoint it was not entered for."""

    model_config = ConfigDict(extra="forbid")
    name: EnvironmentName | None = None
    endpoint: EnvdEndpoint | None = None
    token: EnvdToken | None = None


class MountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: MountName
    environment_id: ObjectId
    # The mount's default directory inside the environment and the root of its route.
    working_directory: WorkingDirectory | None = None
    permission_ceiling: EnvironmentPermissionSet = DEFAULT_MOUNT_PERMISSIONS


class MountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permission_ceiling: EnvironmentPermissionSet


class MountView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    environment_id: str
    working_directory: str | None
    permission_ceiling: EnvironmentPermissionSet = DEFAULT_MOUNT_PERMISSIONS


class MountPage(BaseModel):
    items: list[MountView]
    next_cursor: str | None = None


class ComputerCheck(BaseModel):
    """A point-in-time capability probe, not an Agent or mount permission grant."""

    status: Literal["checked", "unsupported", "not_ready"]
    observe_ready: bool = False
    input_ready: bool = False
    display_count: int = 0
    operations: list[EnvironmentAction] = Field(default_factory=list)
