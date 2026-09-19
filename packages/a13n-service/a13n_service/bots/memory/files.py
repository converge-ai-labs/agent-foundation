"""Filesystem documents retain conversation authority and exact Environment identity."""

from collections.abc import Callable

from a13n_harness.context import AgentContext
from a13n_harness.filesystem_memory import FilesystemMemoryStore
from a13n_harness.memory_plugins import FilesystemMemoryConfiguration
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.domain import Run
from a13n_service.memory.file_runtime import bind_filesystem_store
from a13n_service.memory.models import MemoryStorageRecord
from a13n_service.memory.resources import require_provider
from a13n_service.memory.service import MemoryService, failure
from a13n_service.storage import short_session

from .access import RuntimeAuthority, authorize, subject
from .binding import BotMemoryBinding
from .models import ScopeRecord
from .verification import BotMemoryVerifier


async def authorize_management(
    session: AsyncSession,
    actor: AuthenticatedActor,
    storage: MemoryStorageRecord,
    write: bool,
) -> None:
    scope = await session.get(ScopeRecord, storage.subject_id)
    if (
        scope is None
        or storage.scope_kind != "conversation"
        or (scope.organization_id, scope.workspace_id, scope.provider_id, subject(scope).value)
        != (storage.organization_id, storage.workspace_id, storage.provider_identity, storage.subject)
    ):
        raise failure("memory_scope_unavailable", "Conversation memory is unavailable.")
    await authorize(
        session, actor, scope, WorkspaceAction.bot_memory_create if write else WorkspaceAction.bot_memory_read
    )


async def filesystem_store(
    context: AgentContext,
    service: MemoryService,
    *,
    run: Run,
    binding: BotMemoryBinding,
    agent_id: str,
    current_context: Callable[[], AttemptContext],
    verifier: BotMemoryVerifier,
) -> FilesystemMemoryStore:
    assert binding.scope_id and binding.provider_id
    provider_id = binding.provider_id
    authority = RuntimeAuthority(binding.account_id, binding.scope_id, binding.provider_id, agent_id, current_context)

    async def check(write: bool) -> None:
        verified = await verifier.verify(authority, write=write)
        async with short_session(service.authorizer.sessions) as session:
            scope = await session.get(ScopeRecord, binding.scope_id)
            if scope is None:
                raise failure("memory_scope_unavailable", "Conversation memory is unavailable.")
            await authorize(
                session,
                verified,
                scope,
                WorkspaceAction.bot_memory_create if write else WorkspaceAction.bot_memory_read,
            )
            await require_provider(
                session,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                provider_id=provider_id,
                catalog=service.catalog,
                eligible=True,
            )

    await check(not binding.use_memory)
    async with short_session(service.authorizer.sessions) as session:
        scope = await session.get(ScopeRecord, binding.scope_id)
        assert scope is not None
        workspace_id, native_subject = scope.workspace_id, subject(scope).value
        provider = await require_provider(
            session,
            organization_id=scope.organization_id,
            workspace_id=workspace_id,
            provider_id=provider_id,
            catalog=service.catalog,
            eligible=True,
        )
        if provider.type != "a13n.filesystem":
            raise failure("memory_documents_unsupported", "A File-based provider is required.")
        configuration = FilesystemMemoryConfiguration.model_validate(provider.configuration)
    return await bind_filesystem_store(
        context,
        service,
        run=run,
        workspace_id=workspace_id,
        configuration=configuration,
        provider_identity=binding.provider_id,
        subject=native_subject,
        authorize=check,
        current_context=current_context,
        scope_kind="conversation",
        subject_id=binding.scope_id,
        organization_policy={"agent_id": agent_id, "bot": binding.model_dump(mode="json")}
        if binding.auto_organize
        else None,
    )
