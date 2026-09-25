"""The tenancy API types: what identity, membership and credential routes accept and return."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    JsonValue,
    SecretStr,
    StringConstraints,
)

from a13n_service.infra.ids import ObjectId
from a13n_service.tenancy.authorize import Verb

# Addresses compare case-insensitively everywhere: login, invitations, reset and email change.
Email = Annotated[EmailStr, AfterValidator(str.lower)]
Password = Annotated[SecretStr, Field(min_length=1, max_length=1024)]
NewPassword = Annotated[SecretStr, Field(min_length=12, max_length=1024)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Description = Annotated[str, StringConstraints(max_length=2048)]
Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")]
RoleName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
LinkToken = Annotated[str, StringConstraints(min_length=16, max_length=512)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PrincipalSummary(_View):
    id: str
    kind: str
    name: str
    email: str | None
    status: str
    # A user's profile image; it changes whenever the image does.
    image_url: str | None


class Organization(_View):
    id: str
    key: str
    name: str
    image_url: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    # The caller's verbs at organization scope: `admin` administers it, `write` edits shared resources.
    permissions: list[Verb]


class OrganizationUpdate(_Input):
    name: Name | None = None
    key: Key | None = None


class OrganizationPage(BaseModel):
    items: list[Organization]
    next_cursor: str | None


class Workspace(_View):
    id: str
    organization_id: str
    key: str
    name: str
    settings: dict[str, JsonValue]
    image_url: str | None
    archived_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    # The caller's verbs in this workspace; an archived workspace grants at most `read`.
    permissions: list[Verb]


class WorkspaceCreate(_Input):
    key: Key
    name: Name


class WorkspaceUpdate(_Input):
    name: Name | None = None
    # Links naming the old key stop resolving; everything else refers to the workspace by ID.
    key: Key | None = None


class WorkspacePage(BaseModel):
    items: list[Workspace]
    next_cursor: str | None


class GrantView(_View):
    id: str
    organization_id: str
    workspace_id: str | None
    principal: PrincipalSummary
    role: str
    created_by_id: str
    created_at: datetime


class GrantCreate(_Input):
    principal_id: ObjectId
    role: RoleName


class GrantUpdate(_Input):
    role: RoleName


class GrantPage(BaseModel):
    items: list[GrantView]
    next_cursor: str | None


class MemberPage(BaseModel):
    items: list[PrincipalSummary]
    next_cursor: str | None


class Invitation(_View):
    id: str
    organization_id: str
    workspace_id: str | None
    email: str
    role: str
    invited_by_id: str
    principal_id: str | None
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class InvitationCreate(_Input):
    email: Email
    role: RoleName


class InvitationReceipt(BaseModel):
    invitation: Invitation
    # `manual` when email delivery is not configured: the link is returned here once, for the inviter to share.
    delivery: Literal["queued", "manual"]
    invitation_url: str | None


class InvitationAccept(_Input):
    token: LinkToken
    password: Password
    name: Name | None = None


class InvitationPage(BaseModel):
    items: list[Invitation]
    next_cursor: str | None


class ServiceAccount(_View):
    id: str
    organization_id: str
    workspace_id: str
    name: str
    description: str
    status: Literal["active", "disabled"]
    role: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class ServiceAccountCreate(_Input):
    name: Name
    description: Description = ""
    role: RoleName = "runner"


class ServiceAccountUpdate(_Input):
    name: Name | None = None
    description: Description | None = None
    # Replaces the home-workspace grant, or restores one after the account was retired.
    role: RoleName | None = None
    status: Literal["active", "disabled"] | None = None


class ServiceAccountPage(BaseModel):
    items: list[ServiceAccount]
    next_cursor: str | None


class ApiKey(_View):
    id: str
    organization_id: str
    workspace_id: str
    principal: PrincipalSummary
    name: str
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_by_id: str
    version: int
    created_at: datetime


class KeyCreate(_Input):
    name: Name
    expires_at: AwareDatetime | None = None


class UserKeyCreate(KeyCreate):
    workspace_id: ObjectId


class IssuedKey(BaseModel):
    key: ApiKey
    # The bearer secret, returned only in this response.
    secret: str


class ApiKeyPage(BaseModel):
    items: list[ApiKey]
    next_cursor: str | None


class Profile(_View):
    id: str
    kind: str
    name: str
    email: str | None
    status: str
    image_url: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(_Input):
    name: Name | None = None
    # A new address is confirmed through a one-use link sent to it; changing it requires the current password.
    email: Email | None = None
    current_password: Password | None = None


class PasswordChange(_Input):
    current_password: Password
    password: NewPassword


class AccountDisable(_Input):
    current_password: Password


class PasswordReset(_Input):
    email: Email


class PasswordResetConfirm(_Input):
    token: LinkToken
    password: NewPassword


class EmailChangeConfirm(_Input):
    token: LinkToken


class LoginSession(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    current: bool


class LoginSessionPage(BaseModel):
    items: list[LoginSession]
    next_cursor: str | None


class LoginInput(_Input):
    email: Email
    password: Password


class LoginOutput(BaseModel):
    principal_id: str
    csrf_token: str


class SessionProfile(BaseModel):
    """What a browser restores from its HttpOnly cookie: the account and the token it sends on mutations."""

    user: Profile
    csrf_token: str


class AuthConfiguration(BaseModel):
    email_delivery: bool
    # False until the first administrator is created, which the public bootstrap route then allows.
    initialized: bool


class AuditEvent(_View):
    id: str
    # None for account-wide events, which only their user's own trail shows.
    organization_id: str | None
    workspace_id: str | None
    actor_id: str | None
    actor: PrincipalSummary | None
    action: str
    target_kind: str
    target_id: str
    outcome: str
    details: dict[str, JsonValue]
    occurred_at: datetime


class AuditPage(BaseModel):
    items: list[AuditEvent]
    next_cursor: str | None
