"""Console identity projections and bounded self-service requests."""

from datetime import datetime

from pydantic import EmailStr, Field, SecretStr

from .schemas import Name, PasswordRequest, RequestModel, Resource, Token


class UpdateProfileRequest(RequestModel):
    name: Name


class PasswordResetRequest(RequestModel):
    email: EmailStr


class CompletePasswordResetRequest(PasswordRequest):
    token: Token


class EmailChangeRequest(RequestModel):
    email: EmailStr
    current_password: SecretStr = Field(min_length=1, max_length=128)


class CompleteEmailChangeRequest(RequestModel):
    token: Token


class AuthConfiguration(RequestModel):
    email_delivery: bool


class OrganizationPermissions(RequestModel):
    organization_admin: bool


class Permissions(OrganizationPermissions):
    actions: list[str]


class SecurityEvent(Resource):
    organization_id: str | None
    workspace_id: str | None
    actor_type: str
    actor_id: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    outcome: str
    occurred_at: datetime
    request_id: str | None
