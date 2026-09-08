"""Shared identity values owned by Service IAM."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$")]


class PrincipalType(StrEnum):
    user = "user"
    service_account = "service_account"


class PrincipalRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_type: PrincipalType
    principal_id: ObjectId


class ResourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    resource_id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId | None = None


class AuthorizationError(Exception):
    def __init__(self, code: str, *, concealed: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.concealed = concealed


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    principal: PrincipalRef
    auth_method: str
    credential_id: str
    boundary_workspace_id: str | None
    boundary_organization_id: str | None = None
    request_id: str | None = None
    # Host authenticators own their credential lifecycle. The built-in authenticator
    # marks Service credentials so later stream authorization also rechecks revocation.
    credential_source: Literal["host", "service"] = "host"

    def __post_init__(self) -> None:
        if (self.boundary_workspace_id is None) == (self.boundary_organization_id is None):
            raise ValueError("exactly one credential boundary is required")
        if self.boundary_organization_id is not None and (
            self.principal.principal_type != PrincipalType.user or self.auth_method != "session"
        ):
            raise ValueError("Organization boundaries require a human session")

    @property
    def workspace_id(self) -> str:
        if self.boundary_workspace_id is None:
            raise AuthorizationError("workspace_boundary_required", concealed=True)
        return self.boundary_workspace_id
