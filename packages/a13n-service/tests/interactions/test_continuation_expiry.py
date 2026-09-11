from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from a13n_harness import AgentDefinition, AgentIdentityRef, AgentInstanceContext, AgentSpec, HarnessBuilder
from a13n_service.interactions.attempt_executor import RunAttemptExecutor
from a13n_service.interactions.attempt_resources import attempt_resource_stack
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.harness_results import AttemptDisposition
from a13n_service.interactions.harness_runtime import (
    HarnessCollaborators,
    HarnessDriver,
    HarnessInvocation,
    MaterializedHarnessInput,
)
from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler, ThreadInboxStore
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.terminal_committer import DatabaseAttemptCommitter
from a13n_service.run_stream import RunReplayStore
from a13n_service.storage import short_session, transaction
from a13n_service.subagents import AsyncSubagentResultMaterializer, AsyncSubagentResultPublisher
from anyio import fail_after, sleep
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.messages import TextContent as NativeTextContent
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID
from .test_attempt_execution import _completed_state
from .test_attempt_executor import _Adapter, _CapacitySlot, _Projector, _Wakeups
from .test_subagent_results import _accept_child, _fail_child

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("scenario", ["expired", "eligible", "new_arrival"])
async def test_completed_recovery_rechecks_input_after_runtime_preparation(
    relational_interaction_sessions, interaction_object_store, monkeypatch, scenario
):
    sessions = relational_interaction_sessions
    states, parent, context, child_id = await _accept_child(sessions, interaction_object_store)
    now = NOW + timedelta(seconds=4)
    execution = AttemptExecutionService(sessions, clock=lambda: now, lifecycle=test_lifecycle_writer())
    initial = await states.read(ORGANIZATION_ID, parent.id)
    stored = await execution.publish_checkpoint(
        context, states, initial, _completed_state(initial.envelope, context.run_attempt_id, context.attempt_number)
    )
    await _fail_child(sessions, child_id)
    replays = RunReplayStore(interaction_object_store)
    entry = await AsyncSubagentResultPublisher(sessions, replays, clock=lambda: now).publish(
        organization_id=ORGANIZATION_ID, child_run_id=child_id
    )
    async with transaction(sessions) as database:
        row = await database.get(ThreadInboxRecord, entry.id)
        row.expires_at = now + timedelta(seconds=1)

    materializer = AsyncSubagentResultMaterializer(sessions, replays)
    materialized = []

    async def materialize(entry, config):
        materialized.append(entry.id)
        if entry.kind.value == "steer":
            return AcceptedAgentInput.model_validate(entry.payload).content[0].text
        return await materializer(entry)

    inbox = DatabaseThreadInboxReconciler(sessions, materialize, clock=lambda: now)
    control = RunAttemptControl(context=context, execution=execution, states=states, state=stored, inbox=inbox)
    outcomes = RunOutcomeService(
        sessions, RunPayloadStore(interaction_object_store), clock=lambda: now, lifecycle=test_lifecycle_writer()
    )
    committer = DatabaseAttemptCommitter(sessions, outcomes, execution)
    commit = outcomes.commit_verified_state_outcome
    decisions = []

    async def commit_with_arrival(authority, verified):
        if scenario == "new_arrival" and len(decisions) == 1:
            await ThreadInboxStore(sessions, clock=lambda: now).append_steer(
                organization_id=ORGANIZATION_ID,
                run_id=parent.id,
                input=AcceptedAgentInput(schema_version="1", content=(TextContent(text="new direction"),)),
                entry_id="inb_9999999999999999",
            )
        result = await commit(authority, verified)
        decisions.append(result.run_status.value)
        return result

    monkeypatch.setattr(outcomes, "commit_verified_state_outcome", commit_with_arrival)
    requests = []

    async def model(messages, info):
        requests.append(messages)
        yield "continued"

    async def input_factory(preparation):
        return await control.continuation_input(committer)

    invocation = HarnessInvocation(
        definition=AgentDefinition(agent=AgentSpec(), output_type=str, model=FunctionModel(stream_function=model)),
        input=MaterializedHarnessInput(input_factory),
        collaborators=HarnessCollaborators(
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="foundation", subject="test-user"),
                agent_instance_id=parent.thread_id,
                actor="user:test-user",
            )
        ),
    )
    trace = []

    class Preparer:
        async def claim_state_writer(self):
            pass

        async def validate_dependencies(self, context):
            from .worker_helpers import prepare_permissions

            await prepare_permissions(sessions, parent, context)

        @asynccontextmanager
        async def open_runtime(self, context):
            nonlocal now
            assert decisions == ["running"]
            if scenario != "eligible":
                now += timedelta(seconds=2)

            async def close_resources():
                await sleep(0)
                trace.append("resources-closed")

            async with attempt_resource_stack(cleanup_timeout_seconds=5) as resources:
                resources.push_async_callback(close_resources)
                yield invocation

    capacity = _CapacitySlot(trace)
    projector = _Projector()
    executor = RunAttemptExecutor(
        activate_publication=AsyncMock(),
        context=context,
        control=control,
        driver=HarnessDriver(HarnessBuilder(instrumentation=None), control=control, projector=projector),
        preparer=Preparer(),
        wakeups=_Wakeups(trace),
        adapter=_Adapter,
        committer=committer,
        capacity_slot=capacity,
    )
    with fail_after(10):
        receipt = await executor.run()
    assert receipt.disposition is AttemptDisposition.completed
    assert trace[-2:] == ["resources-closed", "capacity:release"]
    assert capacity.releases == 1
    assert len(requests) == int(scenario != "expired")
    assert decisions == (["running", "running", "completed"] if scenario == "new_arrival" else ["running", "completed"])
    async with short_session(sessions) as database:
        run = await database.get(RunRecord, parent.id)
        attempt = await database.get(RunAttemptRecord, context.run_attempt_id)
        row = await database.get(ThreadInboxRecord, entry.id)
        assert run.status == "completed"
        assert run.failure_json is None
        assert run.output_json == ({"answer": 42} if scenario == "expired" else "continued")
        assert attempt.status == "succeeded"
        assert (attempt.harness_run_id is None) is (scenario == "expired")
        assert (
            len((await database.scalars(select(RunAttemptRecord).where(RunAttemptRecord.run_id == parent.id))).all())
            == 1
        )
        assert row.status == ("consumed" if scenario == "eligible" else "expired")
    assert (entry.id in materialized) is (scenario == "eligible")
    checkpoint = await states.read(ORGANIZATION_ID, parent.id)
    assert [receipt.inbox_entry_id for receipt in checkpoint.envelope.host.inbox_receipts] == (
        [] if scenario == "expired" else [entry.id] if scenario == "eligible" else ["inb_9999999999999999"]
    )
    if scenario == "expired":
        assert not projector.events
    else:
        prompts = [
            item.content if isinstance(item, NativeTextContent) else item
            for message in requests[0]
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            for item in ((part.content,) if isinstance(part.content, str) else part.content)
        ]
        assert "hello" not in prompts
        if scenario == "new_arrival":
            assert prompts.count("new direction") == 1
