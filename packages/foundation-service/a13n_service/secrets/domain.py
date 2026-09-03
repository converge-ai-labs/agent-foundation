"""Non-secret references to Secret values resolved at an authorized use boundary."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SecretId = Annotated[str, StringConstraints(pattern=r"^sec_[a-z0-9]{16,64}$")]
SecretKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$", min_length=1, max_length=128),
]


class WorkspaceSecretCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["workspace_secret"] = "workspace_secret"
    secret_id: SecretId


class InvokingUserSecretCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["invoking_user_secret"] = "invoking_user_secret"
    secret_key: SecretKey


SecretCredentialSource = Annotated[
    WorkspaceSecretCredential | InvokingUserSecretCredential,
    Field(discriminator="source"),
]


class SecretOwnerType(StrEnum):
    """Durable owner kinds accepted by the shared Secret table."""

    workspace = "workspace"
    user = "user"
    ingress = "ingress"
    connector = "connector"
    mcp_connection = "mcp_connection"
    a2a_push_configuration = "a2a_push_configuration"


class SecretOperation(StrEnum):
    """Finite internal reasons for resolving or changing protected material."""

    management = "management"
    runtime = "runtime"
    setup = "setup"
    callback = "callback"
    reconciliation = "reconciliation"


class SecretUseContext(BaseModel):
    """Exact owner, key, generation, and operation for one internal Secret use."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str = Field(min_length=1, max_length=72)
    workspace_id: str = Field(min_length=1, max_length=72)
    owner_type: SecretOwnerType
    owner_id: str = Field(min_length=1, max_length=72)
    key: SecretKey
    operation: SecretOperation
    credential_generation: int = Field(ge=1)
