"""Reauthorize Console and host-bound runtime operations independently."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from a13n_harness.memory import MemoryDocumentScope, MemorySubject
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.models import AgentRecord
from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.iam import (
    AuthenticatedActor,
    PrincipalRef,
    PrincipalType,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.memory.service import failure
from a13n_service.temporal import utc_now

from .binding import BotMemoryBinding
from .domain import MemorySettings, ScopeSettings
from .models import ScopeRecord


@dataclass(frozen=True, slots=True)
class VerifiedConversations:
    """Operation-local provider evidence, never accepted from model or API input."""

    credential_generation: int
    scope_versions: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class RuntimeAuthority:
    account_id: str
    scope_id: str
    provider_id: str
    agent_id: str
    context: Callable[[], AttemptContext]
    verified: VerifiedConversations | None = None


Authority = AuthenticatedActor | RuntimeAuthority


async def authorize(
    session: AsyncSession,
    authority: Authority,
    scope: ScopeRecord,
    action: WorkspaceAction,
    *,
    before_provider_check: bool = False,
) -> None:
    account = await session.get(AccountRecord, scope.account_id)
    if (
        account is None
        or account.workspace_id != scope.workspace_id
        or account.organization_id != scope.organization_id
    ):
        raise failure("memory_scope_not_found", "Memory scope not found.", ErrorCategory.not_found)
    if isinstance(authority, AuthenticatedActor):
        await authorize_workspace(session, actor=authority, workspace_id=scope.workspace_id, action=action)
        return
    if (authority.account_id, authority.scope_id, authority.provider_id) != (
        scope.account_id,
        scope.id,
        scope.provider_id,
    ):
        raise failure("memory_scope_not_found", "Memory scope not found.", ErrorCategory.not_found)
    settings = ScopeSettings.model_validate(scope.settings_json)
    write = action in (WorkspaceAction.bot_memory_create, WorkspaceAction.bot_memory_delete)
    if action is WorkspaceAction.bot_memory_share:
        raise failure("memory_sharing_forbidden", "Sharing requires an administrator.", ErrorCategory.forbidden)
    if (
        account.deleted_at is not None
        or account.status != "active"
        or account.memory_json is None
        or scope.audience == "unknown"
        or not settings.enabled
        or (not settings.save_on_request if write else not settings.use_memory)
    ):
        raise failure("memory_scope_unavailable", "Conversation memory is disabled.", ErrorCategory.forbidden)
    if not before_provider_check and (
        authority.verified is None
        or authority.verified.credential_generation != account.credential_generation
        or (scope.id, scope.version) not in authority.verified.scope_versions
    ):
        raise failure(
            "memory_scope_unverified", "Current conversation access has not been verified.", ErrorCategory.forbidden
        )
    context = authority.context()
    run, _, _ = await read_attempt_authority(session, context, utc_now())
    retained = BotMemoryBinding.model_validate(run.bot_memory_json) if run.bot_memory_json else None
    account_settings = MemorySettings.model_validate(account.memory_json)
    if (
        retained is None
        or run.organization_id != scope.organization_id
        or (retained.account_id, retained.scope_id, retained.provider_id)
        != (scope.account_id, scope.id, scope.provider_id)
        or retained.external_conversation_id != scope.external_conversation_id
        or (not retained.save_on_request if write else not retained.use_memory)
        or (not account_settings.save_on_request if write else not account_settings.use_memory)
    ):
        raise failure(
            "memory_scope_unavailable", "Retained conversation memory is unavailable.", ErrorCategory.forbidden
        )
    actor = AuthenticatedActor(
        principal=PrincipalRef(
            principal_type=PrincipalType(run.authority_principal_type), principal_id=run.authority_principal_id
        ),
        auth_method="internal",
        credential_id="bot-memory",
        boundary_workspace_id=scope.workspace_id,
    )
    for agent_id in {run.agent_id, authority.agent_id}:
        await authorize_agent(
            session,
            actor=actor,
            workspace_id=scope.workspace_id,
            agent_id=agent_id,
            action=WorkspaceAction.agent_invoke,
            snapshot=context.authorization.snapshot,
        )
        agent = await session.get(AgentRecord, agent_id)
        if (
            agent is None
            or agent.organization_id != scope.organization_id
            or agent.workspace_id != scope.workspace_id
            or not agent.enabled
            or agent.archived_at is not None
        ):
            raise failure("memory_scope_unavailable", "Memory Agent is unavailable.", ErrorCategory.forbidden)
    await authorize_workspace(
        session,
        actor=actor,
        workspace_id=scope.workspace_id,
        action=WorkspaceAction.memory_write if write else WorkspaceAction.memory_read,
        snapshot=context.authorization.snapshot,
    )


def subject(scope: ScopeRecord) -> MemorySubject:
    identity = json.dumps(
        [
            "a13n.bot-memory.v1",
            scope.organization_id,
            scope.workspace_id,
            scope.provider_id,
            scope.account_id,
            scope.external_conversation_id,
        ],
        separators=(",", ":"),
    )
    return MemorySubject(MemoryDocumentScope.CONVERSATION, "a13n-" + hashlib.sha256(identity.encode()).hexdigest())
