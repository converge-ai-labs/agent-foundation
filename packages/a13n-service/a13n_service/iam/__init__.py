"""Identity, organization, authorization, and security-audit persistence."""

from .authentication import AuthenticationError, RequestAuthenticator
from .authorization import (
    AuthorizedAgentCollection,
    AuthorizedWorkspace,
    WorkspaceAction,
    authorize_agent,
    authorize_agent_collection,
    authorize_agent_scoped_collection,
    authorize_agent_skill_binding,
    authorize_persisted_agent_principal_actions,
    authorize_workspace,
)
from .domain import AuthenticatedActor, AuthorizationError, PrincipalRef, PrincipalType, ResourceRef
from .http.authentication import authenticate_request
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
    "authorize_agent_scoped_collection",
    "authorize_agent_skill_binding",
    "authorize_persisted_agent_principal_actions",
    "authorize_workspace",
]
