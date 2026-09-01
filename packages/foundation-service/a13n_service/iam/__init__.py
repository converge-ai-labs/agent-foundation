"""Identity, tenant, authorization, and security-audit persistence."""

from .authentication import AuthenticationError, RequestAuthenticator, authenticate_request
from .authorization import (
    AuthenticatedActor,
    AuthorizationError,
    AuthorizedAgentPresetCollection,
    AuthorizedWorkspace,
    WorkspaceAction,
    authorize_agent_preset,
    authorize_agent_preset_collection,
    authorize_agent_skill_binding,
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
    "AuthorizedAgentPresetCollection",
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
    "authorize_agent_preset",
    "authorize_agent_preset_collection",
    "authorize_agent_skill_binding",
    "authorize_workspace",
]
