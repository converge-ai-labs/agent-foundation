"""Declared Agent Secret bindings and current owner eligibility."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import EffectiveAgentConfig, SecretRequirement
from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.iam import AuthenticatedActor, AuthorizationError, PrincipalType, WorkspaceAction, authorize_workspace
from a13n_service.iam.authorization import PrincipalPermissions

from .domain import AgentSecretBinding, InvokingUserSecretCredential, WorkspaceSecretCredential
from .models import SecretRecord


class AgentSecretError(ApplicationError):
    pass


def secret_unavailable() -> AgentSecretError:
    return AgentSecretError(
        "input_secret_unavailable", "A selected Agent Secret is unavailable.", category=ErrorCategory.invalid_request
    )


def graph_secret_requirements(config: EffectiveAgentConfig) -> tuple[SecretRequirement, ...]:
    """Include frozen child declarations, without granting a node its siblings' Secrets."""
    requirements: dict[str, SecretRequirement] = {}
    pending = [config]
    while pending:
        node = pending.pop()
        for requirement in node.secret_requirements:
            previous = requirements.get(requirement.key)
            if previous is None or requirement.required:
                requirements[requirement.key] = requirement
        pending.extend(child.effective_config for child in node.child_configs.values())
    return tuple(requirements.values())


def validate_secret_bindings(
    bindings: tuple[AgentSecretBinding, ...], requirements: tuple[SecretRequirement, ...]
) -> None:
    keys = {binding.key for binding in bindings}
    declared = {requirement.key for requirement in requirements}
    required = {requirement.key for requirement in requirements if requirement.required}
    if len(keys) != len(bindings) or not keys <= declared:
        raise AgentSecretError(
            "input_secret_undeclared",
            "Agent Secret bindings must name unique declared requirements.",
            category=ErrorCategory.invalid_request,
        )
    if not required <= keys:
        raise AgentSecretError(
            "input_secret_required",
            "A required Agent Secret binding is missing.",
            category=ErrorCategory.invalid_request,
        )


def select_child_secret_bindings(
    bindings: tuple[AgentSecretBinding, ...], config: EffectiveAgentConfig
) -> tuple[AgentSecretBinding, ...]:
    requirements = graph_secret_requirements(config)
    keys = {requirement.key for requirement in requirements}
    selected = tuple(binding for binding in bindings if binding.key in keys)
    validate_secret_bindings(selected, requirements)
    return selected


async def require_secret(
    database: AsyncSession,
    *,
    actor: AuthenticatedActor,
    binding: AgentSecretBinding,
    accepting: bool,
    snapshot: PrincipalPermissions | None = None,
) -> SecretRecord:
    """Authorize selection separately from execution; never decrypt during acceptance."""
    credential = binding.credential
    try:
        workspace = await authorize_workspace(
            database,
            actor=actor,
            workspace_id=actor.workspace_id,
            action=WorkspaceAction.secrets_bind
            if accepting and isinstance(credential, WorkspaceSecretCredential)
            else WorkspaceAction.secrets_read,
            snapshot=snapshot,
        )
    except AuthorizationError as error:
        raise secret_unavailable() from error
    query = select(SecretRecord).where(
        SecretRecord.organization_id == workspace.organization_id,
        SecretRecord.workspace_id == actor.workspace_id,
        SecretRecord.deleted_at.is_(None),
        SecretRecord.ciphertext.is_not(None),
        SecretRecord.nonce.is_not(None),
        SecretRecord.encryption_key_id.is_not(None),
    )
    if isinstance(credential, InvokingUserSecretCredential):
        if actor.principal.principal_type is not PrincipalType.user:
            raise secret_unavailable()
        query = query.where(
            SecretRecord.owner_type == "user",
            SecretRecord.owner_id == actor.principal.principal_id,
            SecretRecord.key == credential.secret_key,
        )
    else:
        query = query.where(
            SecretRecord.owner_type == "workspace",
            SecretRecord.owner_id == actor.workspace_id,
            SecretRecord.id == credential.secret_id,
        )
    row = await database.scalar(query)
    if row is None:
        raise secret_unavailable()
    return row


@dataclass(frozen=True, slots=True, repr=False)
class AgentSecretSnapshot:
    secret_id: str
    organization_id: str
    workspace_id: str
    owner_type: str
    owner_id: str
    key: str
    version: int
    ciphertext: bytes
    nonce: bytes
    encryption_key_id: str

    @classmethod
    def from_record(cls, row: SecretRecord):
        if row.ciphertext is None or row.nonce is None or row.encryption_key_id is None:
            raise secret_unavailable()
        return cls(
            row.id,
            row.organization_id,
            row.workspace_id,
            row.owner_type,
            row.owner_id,
            row.key,
            row.version,
            bytes(row.ciphertext),
            bytes(row.nonce),
            row.encryption_key_id,
        )
