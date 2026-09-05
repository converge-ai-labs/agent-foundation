"""Resolve an invocation choice inside the transaction accepting its Run."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import ChildEnvironmentPolicy, canonical_digest
from a13n_service.agents.models import AgentRecord
from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions
from a13n_service.ids import new_object_id
from a13n_service.interactions.domain import Run, RunInputKind
from a13n_service.interactions.models import RunRecord, ThreadRecord

from .domain import EnvironmentSelection, ExistingEnvironmentSelection, NewEnvironmentSelection
from .errors import invalid_environment
from .models import (
    EnvironmentProviderRecord,
    EnvironmentRecord,
    EnvironmentTemplateRecord,
    EnvironmentTemplateRevisionRecord,
)


class Omitted(Enum):
    UNSET = "omitted"


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
            agent = await session.get(AgentRecord, run.agent_id)
            choice = (
                NewEnvironmentSelection(template_id=agent.default_environment_template_id)
                if agent and agent.default_environment_template_id
                else None
            )
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
    if isinstance(choice, NewEnvironmentSelection):
        template = await session.scalar(
            select(EnvironmentTemplateRecord).where(
                EnvironmentTemplateRecord.id == choice.template_id,
                EnvironmentTemplateRecord.workspace_id == workspace_id,
                EnvironmentTemplateRecord.archived_at.is_(None),
            )
        )
        if template is None:
            raise invalid_environment("Environment template is unavailable")
        revision = await session.scalar(
            select(EnvironmentTemplateRevisionRecord).where(
                EnvironmentTemplateRevisionRecord.template_id == template.id,
                EnvironmentTemplateRevisionRecord.version == (choice.version or template.version),
            )
        )
        if revision is None:
            raise invalid_environment("Environment template revision is unavailable")
        environment = allocate_revision(revision, now=run.created_at)
        session.add(environment)
    elif isinstance(choice, ExistingEnvironmentSelection):
        environment = await session.scalar(
            select(EnvironmentRecord).where(
                EnvironmentRecord.id == choice.environment_id, EnvironmentRecord.workspace_id == workspace_id
            )
        )
        if environment is None:
            raise invalid_environment("Environment is unavailable")
    else:
        raise invalid_environment("Environment selection is invalid")
    provider = await session.get(EnvironmentProviderRecord, environment.provider_id)
    if provider is None or not provider.enabled:
        raise invalid_environment("Environment Provider is disabled")
    ranks = {"read_only": 0, "read_write": 1, "full": 2}
    access = min((environment.access, run.environment_access or environment.access), key=ranks.__getitem__)
    await session.flush()
    return run.model_copy(update={"environment_id": environment.id, "environment_access": access})


def allocate_revision(revision: EnvironmentTemplateRevisionRecord, *, now: datetime) -> EnvironmentRecord:
    return EnvironmentRecord(
        id=new_object_id("env"),
        organization_id=revision.organization_id,
        workspace_id=revision.workspace_id,
        provider_id=revision.provider_id,
        template_revision_id=revision.id,
        ownership="managed",
        access=revision.to_resource().access.value,
        generation=0,
        status="unprepared",
        retention_condition="idle",
        condition_since=now,
        operation_generation=0,
        created_at=now,
        updated_at=now,
    )


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


def bind_environment_intent(run: Run, choice: EnvironmentSelection | Omitted | None) -> Run:
    if choice is Omitted.UNSET:
        return run
    intent = {"environment": choice.model_dump(mode="json") if choice else None}
    return run.model_copy(
        update={"request_fingerprint": canonical_digest({"request": run.request_fingerprint, **intent})}
    )
