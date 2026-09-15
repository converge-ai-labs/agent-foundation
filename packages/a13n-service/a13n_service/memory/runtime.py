"""Attempt-authorized memory bindings for root and inline child Agents."""

from collections.abc import Awaitable, Callable, Mapping

from a13n_harness.capabilities.mem0 import Mem0Capability, Mem0Scope
from a13n_harness.capabilities.mem0_backends import Mem0Backend, Mem0Subject, added_memory_id
from a13n_harness.errors import RunError

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
from .scopes import memory_subject
from .service import MemoryService, memory_io


def graph_uses_memory(config: EffectiveAgentConfig) -> bool:
    return config.memory is not None or any(
        graph_uses_memory(child.effective_config) for child in config.child_configs.values()
    )


class AuthorizedMemoryBackend(Mem0Backend):
    def __init__(
        self,
        service: MemoryService,
        subjects: tuple[Mem0Subject, ...],
        authorize: Callable[[tuple[Mem0Subject, ...], bool], Awaitable[None]],
    ) -> None:
        self.service = service
        self.subjects = subjects
        self.authorize = authorize

    async def _authorize(self, subjects: tuple[Mem0Subject, ...], *, write: bool = False) -> None:
        if not subjects or any(subject not in self.subjects for subject in subjects):
            raise RunError("The memory scope is unavailable.", code="mem0_scope_unavailable")
        await self.authorize(subjects, write)

    def _check_results(self, raw: object, subjects: tuple[Mem0Subject, ...]) -> object:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("results"), list):
            raise ValueError("Invalid memory results")
        for item in raw["results"]:
            if not isinstance(item, Mapping) or not any(
                item.get(subject.field) == subject.value for subject in subjects
            ):
                raise ValueError("Memory result is outside the authorized scope")
        return raw

    async def search(
        self, query: str, *, subjects: tuple[Mem0Subject, ...], limit: int, threshold: float | None = None
    ) -> object:
        await self._authorize(subjects)
        async with memory_io(self.service.timeout):
            raw = await self.service.require_backend().search(
                query, subjects=subjects, limit=limit, threshold=threshold
            )
            return self._check_results(raw, subjects)

    async def list(self, subject: Mem0Subject, *, limit: int, cursor: str | None = None) -> object:
        await self._authorize((subject,))
        async with memory_io(self.service.timeout):
            raw = await self.service.require_backend().list(subject, limit=limit, cursor=cursor)
            return self._check_results(raw, (subject,))

    async def add(self, text: str, *, subject: Mem0Subject) -> object:
        await self._authorize((subject,), write=True)
        backend = self.service.require_backend()
        async with memory_io(self.service.timeout, write=True):
            raw = await backend.add(text, subject=subject)
            result = await self.service._get(backend, added_memory_id(raw), subject)
            if result.memory != text:
                raise ValueError("Explicit memory write was not confirmed")
            return raw


def memory_capability(
    service: MemoryService,
    *,
    run: Run,
    workspace_id: str,
    agent_id: str,
    selection: MemorySelection,
    current_context: Callable[[], AttemptContext],
) -> Mem0Capability:
    service.require_backend()
    scopes = {
        Mem0Scope.THREAD: memory_subject(run.organization_id, workspace_id, Mem0Scope.THREAD, run.thread_id),
        Mem0Scope.AGENT: memory_subject(run.organization_id, workspace_id, Mem0Scope.AGENT, agent_id),
    }
    snapshot = current_context().authorization.snapshot
    if (
        run.authority_principal.principal_type is PrincipalType.user
        and WorkspaceAction.memory_read in snapshot.workspace_actions
    ):
        scopes[Mem0Scope.USER] = memory_subject(
            run.organization_id, workspace_id, Mem0Scope.USER, run.authority_principal.principal_id
        )
    if selection.scope is not None and selection.scope not in scopes:
        raise RunError("The configured memory scope is unavailable.", code="mem0_scope_unavailable")

    async def authorize(subjects: tuple[Mem0Subject, ...], write: bool) -> None:
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
                    if subject.field == "user_id":
                        await authorize_workspace(
                            session,
                            actor=actor,
                            workspace_id=workspace_id,
                            action=action,
                            snapshot=context.authorization.snapshot,
                        )
                    else:
                        owner = run.agent_id if subject.field == "run_id" else agent_id
                        await authorize_agent(
                            session,
                            actor=actor,
                            workspace_id=workspace_id,
                            agent_id=owner,
                            action=action,
                            snapshot=context.authorization.snapshot,
                        )
        except (AttemptAuthorityError, AuthorizationError) as error:
            raise RunError("Memory execution authority is unavailable.", code="mem0_scope_unavailable") from error

    return Mem0Capability(
        backend=AuthorizedMemoryBackend(service, tuple(scopes.values()), authorize),
        scope_ids={scope: subject.value for scope, subject in scopes.items()},
        **selection.model_dump(),
    )
