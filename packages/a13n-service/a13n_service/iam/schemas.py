"""Bounded public identity requests and metadata-only resources."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

from a13n_service.ids import ObjectId
from a13n_service.temporal import assume_utc

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Token = Annotated[SecretStr, Field(min_length=32, max_length=128)]
WorkspaceRole = Literal["viewer", "runner", "builder", "admin"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class PasswordRequest(RequestModel):
    password: SecretStr = Field(min_length=15, max_length=128)

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: SecretStr) -> SecretStr:
        if any(not 0x21 <= ord(char) <= 0x7E for char in value.get_secret_value()):
            raise ValueError("Password must contain printable ASCII characters without spaces")
        return value


class LoginRequest(PasswordRequest):
    email: EmailStr


class AcceptInvitationRequest(PasswordRequest):
    token: Token
    name: Name | None = None


class Grant(RequestModel):
    resource_type: Literal["organization", "workspace"]
    resource_id: ObjectId
    role_key: Literal["member", "viewer", "runner", "builder", "admin"]

    @model_validator(mode="after")
    def role_matches_resource(self) -> Self:
        allowed = (
            {"member", "admin"} if self.resource_type == "organization" else {"viewer", "runner", "builder", "admin"}
        )
        if self.role_key not in allowed:
            raise ValueError("Role is not valid for this resource")
        return self


class CreateInvitationRequest(RequestModel):
    email: EmailStr
    grants: list[Grant] = Field(min_length=1, max_length=100)

    @field_validator("grants")
    @classmethod
    def unique_grants(cls, value: list[Grant]) -> list[Grant]:
        if len({(grant.resource_type, grant.resource_id) for grant in value}) != len(value):
            raise ValueError("Each resource can receive only one grant")
        return value


class InviteWorkspaceRequest(RequestModel):
    email: EmailStr
    role: WorkspaceRole


class ExpectedVersion(RequestModel):
    expected_version: int = Field(ge=1)


class CreateKeyRequest(RequestModel):
    name: Name
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def absolute_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at requires a timezone")
        return value


class CreateServiceAccountRequest(RequestModel):
    name: Name
    description: str | None = Field(default=None, max_length=2048)
    role: Literal["viewer", "runner", "builder"]


class UpdateServiceAccountRequest(ExpectedVersion):
    name: Name
    description: str | None = Field(default=None, max_length=2048)
    status: Literal["active", "disabled"]
    role: Literal["viewer", "runner", "builder"]


class CreateWorkspaceRequest(RequestModel):
    name: Name


class SetRoleRequest(RequestModel):
    principal_id: ObjectId
    role: Literal["member", "viewer", "runner", "builder", "admin"]


class Resource(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)
    id: str

    @field_validator("*", mode="after")
    @classmethod
    def utc_timestamps(cls, value: object) -> object:
        return assume_utc(value) if isinstance(value, datetime) else value


class User(Resource):
    image_id: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def image_url(self) -> str | None:
        return None if self.image_id is None else f"/api/v1/users/{self.id}/avatar/{self.image_id}"

    email: str
    name: str
    status: str
    email_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class Organization(Resource):
    image_id: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def image_url(self) -> str | None:
        return None if self.image_id is None else f"/api/v1/organizations/{self.id}/icon/{self.image_id}"

    name: str
    created_at: datetime
    updated_at: datetime


class Workspace(Resource):
    image_id: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def image_url(self) -> str | None:
        return None if self.image_id is None else f"/api/v1/workspaces/{self.id}/icon/{self.image_id}"

    organization_id: str
    name: str
    created_at: datetime
    updated_at: datetime


class AuthSession(Resource):
    user_id: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class LoginResult(BaseModel):
    user: User
    session: AuthSession
    csrf_token: str = Field(repr=False)


class ApiKey(Resource):
    principal_type: str
    principal_id: str
    organization_id: str
    boundary_type: str
    boundary_id: str
    name: str
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CreatedKey(RequestModel):
    key: ApiKey
    bearer: str = Field(repr=False)


class Invitation(Resource):
    organization_id: str
    email: str
    verification_mode: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_by_user_id: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    grants: tuple[Grant, ...]


class InvitationDelivery(RequestModel):
    invitation: Invitation
    delivery: Literal["sent", "failed", "manual"]
    invitation_url: str | None = Field(default=None, repr=False)


class ServiceAccount(Resource):
    organization_id: str
    workspace_id: str
    name: str
    description: str | None
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    role: str


class RoleBinding(Resource):
    organization_id: str
    workspace_id: str | None
    principal_type: str
    principal_id: str
    resource_type: str
    resource_id: str
    role_key: str
    created_at: datetime
    updated_at: datetime


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None
