"""Run intent inheritance and Environment binding inside acceptance transactions."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import ChildEnvironmentPolicy
from a13n_service.agents.models import AgentRecord
from a13n_service.environments.domain import EnvironmentSelection, ExistingEnvironmentSelection, NewEnvironmentSelection
from a13n_service.environments.errors import invalid_environment
from a13n_service.environments.models import EnvironmentTemplateRevisionRecord
from a13n_service.environments.selection import Omitted, allocate_selection, intersect_access, resolve_selection
from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions

from .domain import Run, RunInputKind
from .models import RunRecord, ThreadRecord


async def resolve_environment_intent(
    session: AsyncSession,
    *,
    agent_id: str,
    choice: EnvironmentSelection | Omitted | None,
    inherited_id: str | Omitted | None = Omitted.UNSET,
) -> EnvironmentSelection | None:
    """Resolve omission against the selected source, then the Agent default.

    Both input preview and transactional acceptance use this decision. Resource
    authorization and allocation remain separate from intent resolution.
    """
    if choice is not Omitted.UNSET:
        return choice
    if inherited_id is not Omitted.UNSET:
        return ExistingEnvironmentSelection(environment_id=inherited_id) if inherited_id else None
    agent = await session.get(AgentRecord, agent_id)
    return (
        NewEnvironmentSelection(template_id=agent.default_environment_template_id)
        if agent and agent.default_environment_template_id
        else None
    )


async def select_run_environment(
    session: AsyncSession, *, run: Run, workspace_id: str, choice: EnvironmentSelection | Omitted | None = Omitted.UNSET
) -> Run:
    thread = await session.get(ThreadRecord, run.thread_id)
    source_id = run.retry_of_run_id
    if run.input_kind in {
        RunInputKind.waiting_feedback,
        RunInputKind.waiting_continue,
        RunInputKind.async_subagent_result,
    }:
        source_id = source_id or run.parent_run_id
    if source_id is not None:
        source = await session.get(RunRecord, source_id)
        if source is None or source.tenant_id != run.tenant_id or source.thread_id != run.thread_id:
            raise invalid_environment("Environment source Run is unavailable")
        inherited = (
            ExistingEnvironmentSelection(environment_id=source.environment_id) if source.environment_id else None
        )
        if choice is not Omitted.UNSET and choice != inherited:
            raise invalid_environment("Continuation must retain its source Run Environment")
        choice = inherited
        run = run.model_copy(update={"environment_access": source.environment_access})
    if choice is Omitted.UNSET:
        if thread is not None and thread.origin_run_id is not None and thread.current_run_id == run.id:
            source = await session.get(RunRecord, thread.origin_run_id)
            if source is None or source.tenant_id != run.tenant_id:
                raise invalid_environment("Fork source Run is unavailable")
            choice = (
                ExistingEnvironmentSelection(environment_id=source.environment_id) if source.environment_id else None
            )
            run = run.model_copy(update={"environment_access": source.environment_access})
        elif run.environment_id is not None:
            choice = ExistingEnvironmentSelection(environment_id=run.environment_id)
        elif thread is not None and thread.current_run_id != run.id:
            choice = (
                ExistingEnvironmentSelection(environment_id=thread.default_environment_id)
                if thread.default_environment_id
                else None
            )
        else:
            choice = await resolve_environment_intent(session, agent_id=run.agent_id, choice=choice)
    if choice is None:
        return run.model_copy(update={"environment_id": None, "environment_access": None})
    await authorize_persisted_agent_principal_actions(
        session,
        principal=run.authority_principal,
        organization_id=run.tenant_id,
        workspace_id=workspace_id,
        agent_id=run.agent_id,
        actions=frozenset(
            {
                WorkspaceAction.environment_template_use
                if isinstance(choice, NewEnvironmentSelection)
                else WorkspaceAction.environment_use
            }
        ),
    )
    selected = await resolve_selection(session, workspace_id=workspace_id, choice=choice)
    environment = allocate_selection(session, selected, workspace_id=workspace_id, now=run.created_at)
    access = intersect_access(environment.access, run.environment_access)
    await session.flush()
    return run.model_copy(update={"environment_id": environment.id, "environment_access": access})


async def queued_environment_choice(session: AsyncSession, submission_id: str) -> EnvironmentSelection | Omitted | None:
    from a13n_service.interactions.control_models import QueuedSubmissionRecord

    row = await session.get(QueuedSubmissionRecord, submission_id)
    if row is None:
        raise invalid_environment("Queued submission is unavailable")
    submission = row.to_resource().submission
    return submission.environment if "environment" in submission.model_fields_set else Omitted.UNSET


async def child_environment_choice(
    session: AsyncSession, *, parent: Run, policy: ChildEnvironmentPolicy
) -> EnvironmentSelection | None:
    if policy.mode == "none":
        return None
    if policy.mode == "shared":
        return ExistingEnvironmentSelection(environment_id=parent.environment_id) if parent.environment_id else None
    revision = await session.get(EnvironmentTemplateRevisionRecord, policy.template_revision_id)
    if revision is None:
        raise invalid_environment("Child Environment template revision is unavailable")
    return NewEnvironmentSelection(template_id=revision.template_id, version=revision.version)
