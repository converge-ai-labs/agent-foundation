"""Attempt-authorized memory bindings for root and inline child Agents."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from a13n_harness.capabilities.memory import MemoryCapability
from a13n_harness.errors import RunError
from a13n_harness.memory import (
    MemoryBackend,
    MemoryPage,
    MemoryPaginationUnsupported,
    MemoryRecord,
    MemoryRecordNotFound,
    MemoryScope,
    MemorySubject,
    MemoryWriteUnconfirmed,
    require_memory_subject,
    validate_memory_text,
)

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.models import AgentRecord
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.iam.domain import PrincipalType
from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptContext, read_attempt_authority
from a13n_service.interactions.domain import Run
from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from .domain import MemorySelection
from .execution import MemoryProviderAccess, open_memory_backend
from .resources import MemoryProviderError, require_provider
from .scopes import memory_subject
from .service import MemoryService


def graph_uses_memory(config: EffectiveAgentConfig) -> bool:
    return config.memory is not None or any(
        graph_uses_memory(child.effective_config) for child in config.child_configs.values()
    )


@asynccontextmanager
async def _backend_io(timeout: float, *, write: bool = False) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(timeout):
            yield
    except asyncio.CancelledError:
        raise
    except (MemoryRecordNotFound, MemoryPaginationUnsupported):
        raise
    except Exception as error:
        if write or isinstance(error, MemoryWriteUnconfirmed):
            raise MemoryWriteUnconfirmed("Inspect current memory before repeating the write") from error
        raise RunError("Memory is unavailable.", code="memory_unavailable") from error


class AuthorizedMemoryBackend(MemoryBackend):
    def __init__(
        self,
        service: MemoryService,
        subjects: tuple[MemorySubject, ...],
        authorize: Callable[[tuple[MemorySubject, ...], bool], Awaitable[MemoryProviderAccess]],
    ) -> None:
        self.service = service
        self.subjects = subjects
        self.authorize = authorize

    async def _authorize(self, subjects: tuple[MemorySubject, ...], *, write: bool = False) -> MemoryProviderAccess:
        if not subjects or any(subject not in self.subjects for subject in subjects):
            raise RunError("The memory scope is unavailable.", code="memory_scope_unavailable")
        return await self.authorize(subjects, write)

    async def search(
        self, query: str, *, subjects: tuple[MemorySubject, ...], limit: int, threshold: float | None = None
    ) -> tuple[MemoryRecord, ...]:
        access = await self._authorize(subjects)
        async with (
            _backend_io(self.service.timeout),
            open_memory_backend(access, self.service.catalog, self.service.protector) as backend,
        ):
            records = await backend.search(query, subjects=subjects, limit=limit, threshold=threshold)
            if len(records) > limit:
                raise ValueError("Invalid memory result count")
            for record in records:
                require_memory_subject(record, subjects)
            return records

    async def list(self, subject: MemorySubject, *, limit: int, cursor: str | None = None) -> MemoryPage:
        access = await self._authorize((subject,))
        async with (
            _backend_io(self.service.timeout),
            open_memory_backend(access, self.service.catalog, self.service.protector) as backend,
        ):
            page = await backend.list(subject, limit=limit, cursor=cursor)
            if len(page.items) > limit:
                raise ValueError("Invalid memory result count")
            for record in page.items:
                require_memory_subject(record, (subject,))
            return page

    async def get(self, memory_id: str, *, subject: MemorySubject) -> MemoryRecord:
        access = await self._authorize((subject,))
        async with (
            _backend_io(self.service.timeout),
            open_memory_backend(access, self.service.catalog, self.service.protector) as backend,
        ):
            record = await backend.get(memory_id, subject=subject)
            require_memory_subject(record, (subject,))
            return record

    async def add(self, text: str, *, subject: MemorySubject) -> MemoryRecord:
        validate_memory_text(text)
        access = await self._authorize((subject,), write=True)
        async with (
            _backend_io(self.service.timeout, write=True),
            open_memory_backend(access, self.service.catalog, self.service.protector) as backend,
        ):
            return await backend.add(text, subject=subject)

    async def update(self, memory_id: str, text: str, *, subject: MemorySubject) -> MemoryRecord:
        validate_memory_text(text)
        access = await self._authorize((subject,), write=True)
        async with (
            _backend_io(self.service.timeout, write=True),
            open_memory_backend(access, self.service.catalog, self.service.protector) as backend,
        ):
            return await backend.update(memory_id, text, subject=subject)

    async def delete(self, memory_id: str, *, subject: MemorySubject) -> None:
        access = await self._authorize((subject,), write=True)
        async with (
            _backend_io(self.service.timeout, write=True),
            open_memory_backend(access, self.service.catalog, self.service.protector) as backend,
        ):
            await backend.delete(memory_id, subject=subject)


async def validate_memory_providers(
    service: MemoryService, *, organization_id: str, workspace_id: str, config: EffectiveAgentConfig
) -> None:
    pending = [config]
    selected: set[str] = set()
    while pending:
        node = pending.pop()
        if node.memory is not None:
            selected.add(node.memory.provider_id)
        pending.extend(child.effective_config for child in node.child_configs.values())
    async with short_session(service.authorizer.sessions) as session:
        for provider_id in sorted(selected):
            await require_provider(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                provider_id=provider_id,
                eligible=True,
                catalog=service.catalog,
            )


def memory_capability(
    service: MemoryService,
    *,
    run: Run,
    workspace_id: str,
    agent_id: str,
    selection: MemorySelection,
    current_context: Callable[[], AttemptContext],
) -> MemoryCapability:
    scopes = {
        MemoryScope.THREAD: memory_subject(
            run.organization_id, workspace_id, selection.provider_id, MemoryScope.THREAD, run.thread_id
        ),
        MemoryScope.AGENT: memory_subject(
            run.organization_id, workspace_id, selection.provider_id, MemoryScope.AGENT, agent_id
        ),
    }
    snapshot = current_context().authorization.snapshot
    if (
        run.authority_principal.principal_type is PrincipalType.user
        and WorkspaceAction.memory_read in snapshot.workspace_actions
    ):
        scopes[MemoryScope.USER] = memory_subject(
            run.organization_id,
            workspace_id,
            selection.provider_id,
            MemoryScope.USER,
            run.authority_principal.principal_id,
        )
    if selection.scope is not None and selection.scope not in scopes:
        raise RunError("The configured memory scope is unavailable.", code="memory_scope_unavailable")

    async def authorize(subjects: tuple[MemorySubject, ...], write: bool) -> MemoryProviderAccess:
        try:
            async with short_session(service.authorizer.sessions) as session:
                context = current_context()
                current, _, _ = await read_attempt_authority(session, context, utc_now())
                if (
                    current.id != run.id
                    or current.authority_principal_id != run.authority_principal.principal_id
                    or current.authority_principal_type != run.authority_principal.principal_type.value
                ):
                    raise AttemptAuthorityError("Memory execution principal changed")
                actor = AuthenticatedActor(
                    principal=run.authority_principal,
                    auth_method="internal",
                    credential_id="attempt-memory",
                    boundary_workspace_id=workspace_id,
                )
                for selected in {run.agent_id, agent_id}:
                    await authorize_agent(
                        session,
                        actor=actor,
                        workspace_id=workspace_id,
                        agent_id=selected,
                        action=WorkspaceAction.agent_invoke,
                        snapshot=context.authorization.snapshot,
                    )
                    agent = await session.get(AgentRecord, selected)
                    if (
                        agent is None
                        or agent.organization_id != run.organization_id
                        or agent.workspace_id != workspace_id
                        or not agent.enabled
                        or agent.archived_at is not None
                    ):
                        raise AttemptAuthorityError("Memory Agent is unavailable")
                action = WorkspaceAction.memory_write if write else WorkspaceAction.memory_read
                for subject in subjects:
                    if subject.scope is MemoryScope.USER:
                        await authorize_workspace(
                            session,
                            actor=actor,
                            workspace_id=workspace_id,
                            action=action,
                            snapshot=context.authorization.snapshot,
                        )
                    else:
                        owner = run.agent_id if subject.scope is MemoryScope.THREAD else agent_id
                        await authorize_agent(
                            session,
                            actor=actor,
                            workspace_id=workspace_id,
                            agent_id=owner,
                            action=action,
                            snapshot=context.authorization.snapshot,
                        )
                provider = await require_provider(
                    session,
                    organization_id=run.organization_id,
                    workspace_id=workspace_id,
                    provider_id=selection.provider_id,
                    eligible=True,
                    catalog=service.catalog,
                )
                return MemoryProviderAccess.from_record(provider)
        except (AttemptAuthorityError, AuthorizationError, MemoryProviderError) as error:
            raise RunError("Memory execution authority is unavailable.", code="memory_scope_unavailable") from error

    return MemoryCapability(
        backend=AuthorizedMemoryBackend(service, tuple(scopes.values()), authorize),
        scope_ids={scope: subject.value for scope, subject in scopes.items()},
        **selection.model_dump(exclude={"provider_id"}),
    )
