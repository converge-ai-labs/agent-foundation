"""Run intent inheritance and Environment binding inside acceptance transactions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import ChildEnvironmentPolicy
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.environments.domain import EnvironmentSelection, ExistingEnvironmentSelection, NewEnvironmentSelection
from a13n_service.environments.errors import invalid_environment
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentTemplateRevisionRecord
from a13n_service.environments.selection import Omitted, allocate_selection, resolve_selection
from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions

from .domain import Run
from .models import RunRecord, ThreadRecord


@dataclass(frozen=True, slots=True)
class ExplicitEnvironment:
    selection: EnvironmentSelection | None


class EnvironmentDefault(StrEnum):
    agent = "agent"
    thread = "thread"


@dataclass(frozen=True, slots=True)
class RetainedRunEnvironment:
    run_id: str
    thread_id: str
    requested: EnvironmentSelection | Omitted | None = Omitted.UNSET


type EnvironmentIntent = ExplicitEnvironment | EnvironmentDefault | RetainedRunEnvironment


def requested_environment(
    choice: EnvironmentSelection | Omitted | None,
    *,
    default: EnvironmentDefault | RetainedRunEnvironment,
) -> EnvironmentIntent:
    return default if choice is Omitted.UNSET else ExplicitEnvironment(choice)


async def resolve_requested_environment(
    session: AsyncSession,
    *,
    agent_id: str,
    agent_revision_id: str | None = None,
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
    if agent_revision_id is None:
        agent = await session.get(AgentRecord, agent_id)
        agent_revision_id = agent.default_revision_id if agent else None
    revision = await session.get(AgentRevisionRecord, agent_revision_id) if agent_revision_id else None
    if agent_revision_id is not None and (revision is None or revision.agent_id != agent_id):
        raise invalid_environment("Agent Revision for Environment selection is unavailable")
    template_id = revision.config.get("default_environment_template_id") if revision else None
    return NewEnvironmentSelection(template_id=template_id) if template_id else None


async def select_run_environment(
    session: AsyncSession, *, run: Run, workspace_id: str, intent: EnvironmentIntent
) -> Run:
    if isinstance(intent, ExplicitEnvironment):
        choice = intent.selection
    elif intent is EnvironmentDefault.agent:
        choice = await resolve_requested_environment(
            session, agent_id=run.agent_id, agent_revision_id=run.agent_revision_id, choice=Omitted.UNSET
        )
    elif intent is EnvironmentDefault.thread:
        thread = await session.get(ThreadRecord, run.thread_id)
        if thread is None or thread.organization_id != run.organization_id or thread.session_id != run.session_id:
            raise invalid_environment("Environment source Thread is unavailable")
        choice = (
            ExistingEnvironmentSelection(environment_id=thread.default_environment_id)
            if thread.default_environment_id
            else None
        )
    elif isinstance(intent, RetainedRunEnvironment):
        source = await session.get(RunRecord, intent.run_id)
        if (
            source is None
            or source.organization_id != run.organization_id
            or source.session_id != run.session_id
            or source.thread_id != intent.thread_id
        ):
            raise invalid_environment("Environment source Run is unavailable")
        choice = ExistingEnvironmentSelection(environment_id=source.environment_id) if source.environment_id else None
        if intent.requested is not Omitted.UNSET and intent.requested != choice:
            raise invalid_environment("Continuation must retain its source Run Environment")
    else:
        raise TypeError("Run acceptance requires an explicit Environment intent")
    if choice is None:
        return run.model_copy(update={"environment_id": None})
    await authorize_persisted_agent_principal_actions(
        session,
        principal=run.authority_principal,
        organization_id=run.organization_id,
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
    environment = await allocate_selection(
        session,
        selected,
        workspace_id=workspace_id,
        now=run.created_at,
        labels=choice.labels if isinstance(choice, NewEnvironmentSelection) else None,
    )
    await session.flush()
    return run.model_copy(update={"environment_id": environment.id})


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
    provider = await session.get(EnvironmentProviderRecord, revision.provider_id)
    if provider is not None and provider.type == "direct-local":
        raise invalid_environment("Direct Local does not support dedicated child environments")
    return NewEnvironmentSelection(template_id=revision.template_id, version=revision.version)
