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
