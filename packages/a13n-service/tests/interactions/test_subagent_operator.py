from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from a13n_harness import (
    AgentDefinition,
    AgentIdentityRef,
    HarnessBuilder,
    SubagentDefinition,
)
from a13n_harness.capabilities import (
    AsyncDelegateRequest,
    AsyncResumeRequest,
    ResolvedDelegationContext,
    SubagentCancelRequest,
    SubagentDelegationPlan,
    SubagentInfoRequest,
    SubagentOperatorContext,
    SubagentSteerRequest,
    SubagentToolCallContext,
    SubagentWaitRequest,
)
from a13n_harness.context import BuiltSubagent
from a13n_harness.execution import DelegationContextPolicy
from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.domain import Run
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.interactions.state import CompletedOutcomeCandidate
from a13n_service.storage import ObjectStore, short_session
from a13n_service.subagents import (
    ChildRunAcceptanceService,
    ChildRunAdmissionPreparer,
    DurableSubagentOperator,
)
from a13n_service.subagents.execution_store import ACTIVITY_OUTPUT_PREVIEW_LIMIT
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.models.test import TestModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW
from .test_attempt_execution import _authority, _worker
from .test_subagent_acceptance import (
    CHILD_DEFINITION_ID,
    _accept_parent,
    _complete_run,
    _grant_and_seed_child,
)

pytestmark = pytest.mark.anyio


@dataclass(slots=True)
class AuthorityBox:
    current_context: AttemptContext


@dataclass(slots=True)
class IdSequence:
    values: tuple[str, ...]
    calls: int = 0

    def __call__(self) -> str:
        value = self.values[self.calls]
        self.calls += 1
        return value


async def test_operator_delegates_reads_steers_waits_and_cancels(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    operator, context, delegate_plan, _, run_ids, _ = await _operator(
        interaction_sessions,
        interaction_object_store,
    )

    delegated = await operator.delegate(
        delegate_plan,
        AsyncDelegateRequest(subagent_name="researcher", prompt="research"),
        tool_call=SubagentToolCallContext(tool_call_id="call-1", tool_name="delegate"),
    )
    info = await operator.info(context, SubagentInfoRequest(execution_id=delegated.execution_id))
    steered = await operator.steer(
        context,
        SubagentSteerRequest(execution_id=delegated.execution_id, message="focus on races"),
    )
    cancelled = await operator.cancel(context, SubagentCancelRequest(execution_id=delegated.execution_id))
    waited = await operator.wait(
        context,
        SubagentWaitRequest(execution_id=delegated.execution_id, timeout_seconds=0.01),
    )

    assert run_ids.calls == 1
    assert delegated.child_definition_id == CHILD_DEFINITION_ID
    assert len(info.executions) == 1
    assert info.executions[0].input == delegate_plan.context.input
    assert info.executions[0].segment_index == 0
    assert steered.accepted is True and steered.enqueue_id is not None
    assert cancelled.accepted is True and cancelled.status == "cancelled"
    assert waited.executions[0].status == "cancelled"


async def test_operator_treats_repeated_delegate_calls_as_distinct(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    operator, context, delegate_plan, _, run_ids, _ = await _operator(
        interaction_sessions,
        interaction_object_store,
    )

    first = await operator.delegate(
        delegate_plan,
        AsyncDelegateRequest(subagent_name="researcher", prompt="research"),
    )
    second = await operator.delegate(
        delegate_plan,
        AsyncDelegateRequest(subagent_name="researcher", prompt="research"),
    )
    page = await operator.info(context, SubagentInfoRequest())

    assert first.execution_id != second.execution_id
    assert first.child_run_id != second.child_run_id
    assert run_ids.calls == 2
    assert page.total == 2


async def test_operator_resumes_only_the_selected_completed_child_head(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    operator, context, delegate_plan, states, run_ids, _ = await _operator(
        interaction_sessions,
        interaction_object_store,
    )
    delegated = await operator.delegate(
        delegate_plan,
        AsyncDelegateRequest(subagent_name="researcher", prompt="research"),
    )
    child_claim = await AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "child-lease",
        attempt_id_factory=lambda: "rat_6767676767676767",
        lifecycle=test_lifecycle_writer(),
    ).claim(delegated.child_run_id, _worker())
    assert child_claim is not None and delegated.child_run_id is not None
    await _complete_run(
        interaction_sessions,
        interaction_object_store,
        states,
        await _run(interaction_sessions, delegated.child_run_id),
        _authority(child_claim),
    )
    completed = await operator.info(context, SubagentInfoRequest(execution_id=delegated.execution_id))
    assert completed.executions[0].resumable is True
    resume_plan = _plan(context, delegated_input='{"delegated_task":"continue"}')

    resumed = await operator.resume(
        resume_plan,
        AsyncResumeRequest(execution_id=delegated.execution_id, prompt="continue"),
    )

    assert resumed.resumed_from == delegated.execution_id
    assert resumed.thread_id == delegated.thread_id
    assert resumed.segment_index == 1
    assert resumed.status == "running"
    assert run_ids.calls == 2
    prior = await operator.info(context, SubagentInfoRequest(execution_id=delegated.execution_id))
    assert prior.executions[0].resumable is False


async def test_operator_projects_bounded_closed_child_output_activity(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    operator, context, delegate_plan, states, _, _ = await _operator(
        interaction_sessions,
        interaction_object_store,
    )
    delegated = await operator.delegate(
        delegate_plan,
        AsyncDelegateRequest(subagent_name="researcher", prompt="research"),
    )
    assert delegated.child_run_id is not None
    child_claim = await AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "child-lease",
        attempt_id_factory=lambda: "rat_6767676767676767",
        lifecycle=test_lifecycle_writer(),
    ).claim(delegated.child_run_id, _worker())
    assert child_claim is not None
    output = "x" * (ACTIVITY_OUTPUT_PREVIEW_LIMIT + 1)
    await _complete_run(
        interaction_sessions,
        interaction_object_store,
        states,
        await _run(interaction_sessions, delegated.child_run_id),
        _authority(child_claim),
        outcome=CompletedOutcomeCandidate(output=output, output_text=output),
    )

    info = await operator.info(context, SubagentInfoRequest(execution_id=delegated.execution_id))
    waited = await operator.wait(context, SubagentWaitRequest(execution_id=delegated.execution_id))

    activity = info.executions[0].activity
    assert activity is not None
    assert activity.sequence == 1
    assert activity.output_preview == output[:ACTIVITY_OUTPUT_PREVIEW_LIMIT]
    assert activity.output_truncated is True
    assert waited.executions[0] == info.executions[0]


async def _operator(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
) -> tuple[
    DurableSubagentOperator,
    SubagentOperatorContext,
    SubagentDelegationPlan,
    RunStateStore,
    IdSequence,
    AuthorityBox,
]:
    await _grant_and_seed_child(sessions)
    states, parent, _ = await _accept_parent(sessions, objects)
    parent_claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "parent-lease",
        attempt_id_factory=lambda: "rat_7878787878787878",
        lifecycle=test_lifecycle_writer(),
    ).claim(parent.id, _worker())
    assert parent_claim is not None
    authority = _authority(parent_claim)
    running_parent = await _run(sessions, parent.id)
    context = SubagentOperatorContext(
        parent_thread_id=running_parent.thread_id,
        parent_run_id=running_parent.id,
        parent_agent_instance_id="agent-parent",
        host_refs={"session_id": running_parent.session_id},
    )
    run_ids = IdSequence(("run_ffffffffffffffff", "run_5656565656565656"))
    admission = ChildRunAdmissionPreparer(
        sessions,
        states,
        thread_id_factory=IdSequence(
            (
                "thread-ffffffffffffffffffffffffffffffff",
                "thread-56565656565656565656565656565656",
            )
        ),
        run_id_factory=run_ids,
        relationship_id_factory=IdSequence(("crr_ffffffffffffffff", "crr_5656565656565656")),
        clock=lambda: NOW + timedelta(seconds=2),
    )
    acceptance = ChildRunAcceptanceService(
        sessions,
        states,
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=2),
    )
    authority_box = AuthorityBox(authority)
    operator = DurableSubagentOperator(
        sessions,
        authority_box,
        admission,
        acceptance,
        ThreadInboxStore(sessions, clock=lambda: NOW + timedelta(seconds=4)),
        RunOutcomeService(
            sessions,
            RunPayloadStore(objects),
            clock=lambda: NOW + timedelta(seconds=5),
            lifecycle=test_lifecycle_writer(),
        ),
        parent_context=lambda: context,
        default_wait_timeout_seconds=0.01,
        wait_poll_interval_seconds=0.001,
        clock=lambda: NOW + timedelta(seconds=2),
    )
    return operator, context, _plan(context), states, run_ids, authority_box


def _plan(
    context: SubagentOperatorContext,
    *,
    delegated_input: str = '{"delegated_task":"research"}',
    child_definition_id: str = CHILD_DEFINITION_ID,
) -> SubagentDelegationPlan:
    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id=child_definition_id,
        model=TestModel(),
    )
    executable = HarnessBuilder().build(child)
    declaration = SubagentDefinition(name="researcher", description="Research", agent=child)
    return SubagentDelegationPlan(
        child=BuiltSubagent(declaration=declaration, definition=child, executable=executable),
        child_identity=AgentIdentityRef(issuer="a13n.service", subject="child"),
        context=ResolvedDelegationContext(
            input=delegated_input,
            policy=DelegationContextPolicy(),
        ),
        usage_limits=None,
        parent=context,
    )


async def _run(sessions: async_sessionmaker[AsyncSession], run_id: str) -> Run:
    async with short_session(sessions) as database:
        record = await database.get(RunRecord, run_id)
        assert record is not None
        return record.to_resource()
