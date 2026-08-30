"""Identity, tenant, authorization, and security-audit persistence."""

from .authentication import AuthenticationError, RequestAuthenticator, authenticate_request
from .authorization import (
    AuthenticatedActor,
    AuthorizationError,
    AuthorizedWorkspace,
    WorkspaceAction,
    authorize_workspace,
)
from .domain import PrincipalRef, PrincipalType
from .models import (
    OrganizationRecord,
    RoleBindingRecord,
    SecurityAuditRecord,
    ServiceAccountRecord,
    UserRecord,
    WorkspaceRecord,
)

__all__ = [
    "AuthenticatedActor",
    "AuthenticationError",
    "AuthorizationError",
    "AuthorizedWorkspace",
    "OrganizationRecord",
    "PrincipalRef",
    "PrincipalType",
    "RequestAuthenticator",
    "RoleBindingRecord",
    "SecurityAuditRecord",
    "ServiceAccountRecord",
    "UserRecord",
    "WorkspaceAction",
    "WorkspaceRecord",
    "authenticate_request",
    "authorize_workspace",
]
