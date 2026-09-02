"""Identity, tenant, authorization, and security-audit persistence."""

from .authentication import AuthenticationError, RequestAuthenticator, authenticate_request
from .authorization import (
    AuthenticatedActor,
    AuthorizationError,
    AuthorizedAgentCollection,
    AuthorizedWorkspace,
    WorkspaceAction,
    authorize_agent,
    authorize_agent_collection,
    authorize_agent_skill_binding,
    authorize_workspace,
)
from .domain import PrincipalRef, PrincipalType, ResourceRef
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
    "AuthorizedAgentCollection",
    "AuthorizedWorkspace",
    "OrganizationRecord",
    "PrincipalRef",
    "PrincipalType",
    "RequestAuthenticator",
    "ResourceRef",
    "RoleBindingRecord",
    "SecurityAuditRecord",
    "ServiceAccountRecord",
    "UserRecord",
    "WorkspaceAction",
    "WorkspaceRecord",
    "authenticate_request",
    "authorize_agent",
    "authorize_agent_collection",
    "authorize_agent_skill_binding",
    "authorize_workspace",
]
