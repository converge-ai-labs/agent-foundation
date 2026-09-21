from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    AgentDefinition,
    HarnessBuilder,
    HarnessState,
    InlineSubagentDeferredResults,
    RunBindings,
    RunError,
    SubagentDefinition,
)
from a13n_harness.capabilities import SubagentCapability
from a13n_harness.capabilities.subagents import _inline_subagent_records
from a13n_harness.plugins import PluginRunExchange, PluginRunNext, PluginRunResponse
from a13n_harness.tools import (
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import DeferredToolResults

pytestmark = pytest.mark.anyio


def _tree(depth: int = 1, *, plugins: tuple[AbstractHarnessPlugin, ...] = (), repeat: bool = False):
    effects: list[str] = []
    model_calls: list[str] = []
    actions: dict[str, str] = {}

    def ordinary() -> str:
        effects.append("ordinary")
        return "ordinary-done"

    def approved() -> str:
        effects.append("approved")
        return "approved-done"

    def external() -> str:
        raise CallDeferred({"source": "external-system", "nested": {"key": [1, 2]}})

    async def leaf_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        model_calls.append("leaf")
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns or (repeat and model_calls.count("leaf") == 2):
            yield {
                index: DeltaToolCall(name=name, json_args="{}", tool_call_id=name)
                for index, name in enumerate(("ordinary", "approved", "external"))
            }
        else:
            yield json.dumps({part.tool_call_id: part.content for part in returns})

    definition = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="leaf",
        model=FunctionModel(stream_function=leaf_stream),
        capabilities=(
            Capability(
                tools=[
                    ordinary,
                    HarnessTool(
                        approved,
                        requires_approval=True,
                        harness_metadata=HarnessToolMetadata(
                            tool_id="test.approved",
                            effects=frozenset({"write"}),
                            credential_audiences=(),
                            idempotency="none",
                            output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=2048),
                        ),
                    ),
                    external,
                ],
                id="actions",
            ),
        ),
        plugins=plugins,
    )

    def parent_model(name: str) -> FunctionModel:
        calls = 0

        async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
            nonlocal calls
            del messages, info
            model_calls.append(name)
            calls += 1
            follow_up = actions.get("follow_up") == name and calls in {4, 5}
            if (follow_up and calls == 5) or (not follow_up and calls % 2 == 0):
                yield "parent-done"
                return
            child_id = actions.get(name)
            args = {"execution_id": child_id} if child_id else {"subagent": name}
            args["prompt"] = "approved by prompt is not authority"
            yield {
                0: DeltaToolCall(
                    name="resume_subagent" if child_id else "delegate",
                    json_args=json.dumps(args),
                    tool_call_id=f"{name}-{calls}",
                )
            }

        return FunctionModel(stream_function=stream)

    for index in range(depth):
        name = f"worker{index}"
        definition = AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            definition_id=f"parent-{index}",
            model=parent_model(name),
            capabilities=(SubagentCapability(),),
            subagents=(SubagentDefinition(name=name, description="Work", agent=definition),),
        )
    return HarnessBuilder().build(definition), effects, model_calls, actions


def _submission(state: HarnessState) -> InlineSubagentDeferredResults:
    record = next(item for item in _inline_subagent_records(state).values() if item.pending_run_id)
    assert record.pending_run_id is not None
    return InlineSubagentDeferredResults(
        child_thread_id=record.state.thread_id,
        pending_run_id=record.pending_run_id,
        results=DeferredToolResults(calls={"external": "external-done"}, approvals={"approved": True}),
    )


@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("fork", [False, True])
async def test_inline_deferred_round_trip_and_nested_routing(depth: int, fork: bool) -> None:
    executable, effects, _, actions = _tree(depth)
    initial = await executable.run("start")
    assert initial.status == "completed"  # The parent does not retain a suspended Python stack.
    assert initial.state is not None
    state = HarnessState.model_validate_json(initial.state.model_dump_json())
    original_submission = _submission(state)
    if fork:
        state = state.fork()
        with pytest.raises(RunError, match="no pending"):
            executable.stream(
                "resume",
                previous_state=state,
                bindings=RunBindings.embedded(inline_subagent_results=(original_submission,)),
            )
    records = _inline_subagent_records(state)
    for record in records.values():
        actions[record.subagent_name] = record.child_instance_id
    leaf = next(item for item in records.values() if item.pending_run_id)
    assert leaf.deferred_requests is not None
    assert leaf.deferred_requests.metadata["external"]["nested"] == {"key": [1, 2]}
    assert effects == ["ordinary"]

    # An ordinary prompt cannot approve a pending child or trigger interrupted-tool recovery.
    without_results = await executable.run("resume", previous_state=state)
    assert without_results.state is not None
    retained = _inline_subagent_records(without_results.state)[leaf.state.thread_id]
    assert retained == leaf
    assert effects == ["ordinary"]

    resumed = await executable.run(
        "resume",
        previous_state=without_results.state,
        bindings=RunBindings.embedded(inline_subagent_results=(_submission(state),)),
    )
    assert resumed.status == "completed"
    assert resumed.state is not None
    completed = _inline_subagent_records(resumed.state)[leaf.state.thread_id]
    assert completed.deferred_requests is None
    assert completed.pending_run_id is None
    assert effects == ["ordinary", "approved"]
    assert "external-done" in completed.state.model_dump_json()
    with pytest.raises(RunError, match="no pending"):
        executable.stream(
            "stale",
            previous_state=resumed.state,
            bindings=RunBindings.embedded(inline_subagent_results=(_submission(state),)),
        )


@pytest.mark.parametrize("invalid", ["unknown", "run", "duplicate", "partial", "category"])
async def test_inline_submission_rejected_before_parent_model(invalid: str) -> None:
    executable, effects, calls, _ = _tree()
    initial = await executable.run("start")
    assert initial.state is not None
    submission = _submission(initial.state)
    if invalid == "unknown":
        submission = replace(submission, child_thread_id="unknown")
    elif invalid == "run":
        submission = replace(submission, pending_run_id="old-run")
    elif invalid == "partial":
        submission = replace(submission, results=DeferredToolResults(approvals={"approved": True}))
    elif invalid == "category":
        submission = replace(
            submission, results=DeferredToolResults(calls={"approved": True}, approvals={"external": True})
        )
    submissions = (submission, submission) if invalid == "duplicate" else (submission,)
    before = list(calls)
    with pytest.raises(RunError):
        executable.stream(
            "resume", previous_state=initial.state, bindings=RunBindings.embedded(inline_subagent_results=submissions)
        )
    assert calls == before
    assert effects == ["ordinary"]


async def test_new_inline_batch_with_reused_call_ids_rejects_previous_run_submission() -> None:
    executable, effects, _, actions = _tree(repeat=True)
    first = await executable.run("start")
    assert first.state is not None
    record = next(iter(_inline_subagent_records(first.state).values()))
    actions[record.subagent_name] = record.child_instance_id
    submission = _submission(first.state)
    second = await executable.run(
        "resume", previous_state=first.state, bindings=RunBindings.embedded(inline_subagent_results=(submission,))
    )
    assert second.state is not None
    next_submission = _submission(second.state)
    assert next_submission.child_thread_id == submission.child_thread_id
    assert next_submission.pending_run_id != submission.pending_run_id
    assert effects == ["ordinary", "approved", "ordinary"]
    with pytest.raises(RunError, match="stale Run"):
        executable.stream(
            "stale", previous_state=second.state, bindings=RunBindings.embedded(inline_subagent_results=(submission,))
        )


async def test_inline_approval_uses_current_inherited_policy() -> None:
    executable, effects, _, actions = _tree()
    first = await executable.run("start")
    assert first.state is not None
    record = next(iter(_inline_subagent_records(first.state).values()))
    actions[record.subagent_name] = record.child_instance_id

    async def policy(invocation, metadata, *, context):
        del invocation, context
        # Delegate itself must remain callable; only the child's write is revoked.
        if metadata.tool_id == "test.approved":
            return InvocationPolicyDecision.deny("revoked")
        return InvocationPolicyDecision.allow()

    result = await executable.run(
        "resume",
        previous_state=first.state,
        bindings=RunBindings.embedded(
            inline_subagent_results=(_submission(first.state),),
            capabilities=(InvocationPolicyCapability(evaluator=policy),),
        ),
    )
    assert result.state is not None
    assert effects == ["ordinary"]
    completed = _inline_subagent_records(result.state)[record.state.thread_id]
    assert completed.pending_run_id is None
    denied = [
        part
        for message in completed.state.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id == "approved"
    ]
    assert len(denied) == 1
    assert denied[0].outcome == "failed"
    assert denied[0].content == "Managed tool invocation was denied."


async def test_inline_cleanup_failure_retains_pending_then_clears_consumed_batch() -> None:
    class CleanupFailure(AbstractHarnessPlugin):
        @property
        def plugin_id(self) -> str:
            return "deferred-cleanup"

        def wrap_run(self, exchange: PluginRunExchange, call_next: PluginRunNext) -> PluginRunResponse:
            async def iterate():
                try:
                    async for item in call_next(exchange):
                        yield item
                finally:
                    raise RuntimeError("cleanup failed after complete checkpoint")

            return PluginRunResponse(iterate())

    executable, effects, _, actions = _tree(plugins=(CleanupFailure(),))
    first = await executable.run("start")
    assert first.state is not None
    record = next(iter(_inline_subagent_records(first.state).values()))
    assert record.deferred_requests is not None
    assert record.pending_run_id is not None
    actions[record.subagent_name] = record.child_instance_id
    second = await executable.run(
        "resume",
        previous_state=first.state,
        bindings=RunBindings.embedded(inline_subagent_results=(_submission(first.state),)),
    )
    assert second.state is not None
    completed = _inline_subagent_records(second.state)[record.state.thread_id]
    assert completed.deferred_requests is None
    assert completed.pending_run_id is None
    assert effects == ["ordinary", "approved"]


async def test_consumed_nested_submission_does_not_block_same_run_ordinary_resume() -> None:
    executable, effects, calls, actions = _tree(2)
    first = await executable.run("start")
    assert first.state is not None
    for record in _inline_subagent_records(first.state).values():
        actions[record.subagent_name] = record.child_instance_id
    actions["follow_up"] = "worker1"
    resumed = await executable.run(
        "resume twice",
        previous_state=first.state,
        bindings=RunBindings.embedded(inline_subagent_results=(_submission(first.state),)),
    )
    assert resumed.status == "completed"
    assert calls.count("worker0") == 6
    assert calls.count("leaf") == 3
    assert effects == ["ordinary", "approved"]
    results = [
        part
        for message in resumed.new_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(results) == 2
    assert all(part.outcome == "success" for part in results)
