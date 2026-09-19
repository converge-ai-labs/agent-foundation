from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from a13n_harness import HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy, HandoffCapability
from a13n_harness.capabilities.context import _COMPACTION_PROMPT
from a13n_harness.model_context import user_prompt_content
from a13n_harness_ui.goal import GoalCapability, GoalView, saved_goal, with_goal
from a13n_harness_ui.goal_prompts import goal_check_prompt, has_completion_marker, post_restore_audit_prompt
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


def _text(messages: list[ModelMessage]) -> str:
    return "\n".join(
        item.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        for item in user_prompt_content(part)
        if isinstance(item.content, str)
    )


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("[GOAL_COMPLETE]", True),
        ("Done\n [GOAL_COMPLETE] \n", True),
        ("Use [GOAL_COMPLETE] later", False),
        ("[goal_complete]", False),
        ("```\n[GOAL_COMPLETE]\n```", True),
        ("done", False),
    ],
)
def test_completion_marker_preserves_reference_line_semantics(output, expected):
    assert has_completion_marker(output) is expected


def test_audit_prompts_escape_objective_and_preserve_verification_scope():
    prompt = goal_check_prompt("A & <B>")
    assert "A &amp; &lt;B&gt;" in prompt
    assert "every explicit requirement" in prompt.lower()
    assert "task management tools" in prompt
    assert "uncertain, indirect, partial, or missing evidence" in prompt
    audit = post_restore_audit_prompt("<task>", "<source>")
    assert "&lt;task&gt;" in audit
    assert "&lt;source&gt;" in audit
    assert "not proof" in audit


@pytest.mark.parametrize(
    ("limit", "outputs", "status", "iteration"),
    [
        (10, ["[GOAL_COMPLETE]"], "verified", 0),
        (10, ["part one", "part two", "[GOAL_COMPLETE]"], "verified", 2),
        (1, ["part one", "not yet"], "max_iterations", 1),
        (1, ["part one", "[GOAL_COMPLETE]"], "verified", 1),
        (0, ["not yet"], "max_iterations", 0),
        (-1, ["not yet"], "max_iterations", 0),
        (0, ["[GOAL_COMPLETE]"], "verified", 0),
    ],
)
async def test_native_goal_continuations_stay_inside_one_run(limit, outputs, status, iteration):
    calls = []
    saved = []
    changes = []

    async def save(state):
        saved.append(state)
        return f"checkpoint-{len(saved)}"

    async def changed(goal):
        changes.append(goal)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append(_text(messages))
        assert len(saved) == len(calls)
        yield outputs[len(calls) - 1]

    capability = GoalCapability(GoalView(objective="Implement all requirements", max_iterations=limit), changed=changed)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(capability, RootCheckpointCapability(save)),
    )
    result = await executable.run("Implement all requirements", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert result.output == outputs[-1]
    assert len(calls) == len(outputs)
    assert "goal-check" not in calls[0]
    if len(calls) > 1:
        assert "<goal-check>" in calls[1]
        assert saved_goal(saved[1]).iteration == 1
    goal = saved_goal(result.state)
    assert goal.status == status
    assert goal.iteration == iteration
    assert changes[-1].status == status


@pytest.mark.parametrize("kind", ["summarize", "compact"])
@pytest.mark.parametrize("limit", [0, 1])
async def test_context_replacement_requires_fresh_audit_before_marker_and_checkpoint(kind, limit):
    saved = []
    calls = []
    helper_calls = []

    async def save(state):
        saved.append(state)
        return f"checkpoint-{len(saved)}"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        text = _text(messages)
        if _COMPACTION_PROMPT in text:
            helper_calls.append(text)
            # A nested helper marker must never verify the root Goal.
            yield "Summary of current work\n[GOAL_COMPLETE]"
            return
        calls.append(text)
        if kind == "summarize" and len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize", json_args=json.dumps({"content": "Continue auditing"}), tool_call_id="summary"
                )
            }
            return
        if len(calls) == (2 if kind == "summarize" else 1):
            goal = saved_goal(saved[-1])
            assert goal.needs_restore_audit
            assert goal.iteration == 0
        yield "[GOAL_COMPLETE]"

    goal = GoalCapability(GoalView(objective="Verify every requirement", max_iterations=limit))
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            goal,
            RootCheckpointCapability(save),
            HandoffCapability() if kind == "summarize" else CompactionCapability(CompactionPolicy(trigger_tokens=2000)),
        ),
    )
    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("Previous task")]),
            ModelResponse(parts=[TextPart("Earlier work")], usage=RequestUsage(input_tokens=2100)),
        )
    )
    result = await executable.run("Verify every requirement", bindings=RunBindings.embedded(), previous_state=previous)
    assert result.status == "completed"
    assert saved_goal(result.state).status == ("verified" if limit else "max_iterations")
    assert saved_goal(result.state).iteration == limit
    assert saved_goal(result.state).needs_restore_audit is (not limit)
    assert ("<goal-post-restore-audit>" in calls[-1]) is bool(limit)
    assert len(saved) == len(calls)
    assert len(helper_calls) == (1 if kind == "compact" else 0)


def test_saved_goal_roundtrip_and_clear_preserve_other_state():
    state = HarnessState.new()
    assert saved_goal(state) is None
    goal = GoalView(objective="task")
    updated = with_goal(state, goal)
    assert saved_goal(updated) == goal
    assert saved_goal(state) is None
    assert saved_goal(with_goal(updated, None)) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [("completed", "unverified_stop"), ("failed", "error"), ("cancelled", "cancelled"), ("suspended", "suspended")],
)
async def test_finalization_does_not_confuse_run_end_with_goal_completion(status, expected):
    capability = GoalCapability(GoalView(objective="task", iteration=3))
    state = await capability.finish(HarnessState.new(), status=status)
    assert saved_goal(state).status == expected
    assert saved_goal(state).iteration == 3


@pytest.mark.parametrize("fail", [False, True])
async def test_steering_after_completion_candidate_requires_another_goal_check(fail):
    from a13n_harness.context import AgentContext
    from pydantic_ai import RunContext
    from pydantic_ai.capabilities import AbstractCapability

    calls = 0

    class Steering(AbstractCapability[AgentContext]):
        async def after_output_process(self, ctx: RunContext[AgentContext], *, output_context, output):
            if calls == 1:
                ctx.enqueue("Requirement B is still incomplete", priority="asap")
            return output

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2:
            if fail:
                raise ValueError("Model failed after steering")
            yield "Requirement B still needs work"
        else:
            yield "[GOAL_COMPLETE]"

    goal = GoalCapability(GoalView(objective="Complete A and B"))
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(goal, Steering())
    )
    result = await executable.run("Complete A and B", bindings=RunBindings.embedded())
    state = await goal.finish(result.state, status=result.status, usage=result.usage)
    assert saved_goal(state).status == ("error" if fail else "verified")
    assert calls == (2 if fail else 3)
    assert saved_goal(state).iteration == (0 if fail else 1)


@pytest.mark.parametrize("structured", [False, True])
async def test_app_captures_authored_objective_before_surface_transform_and_clears_on_normal_input(
    tmp_path, monkeypatch, structured
):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.thread_files import ComposerInput
    from pydantic_ai.messages import TextContent

    from .test_app import _settings, _write_configuration

    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "\nmax_goal_iterations: 0\n")
    objective = 'Implement "all" requirements\nand report the evidence.'

    async def model(messages, info):
        yield "Still incomplete"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        prompt = (
            [TextContent(objective), TextContent("Not authored intent", metadata={"display": False})]
            if structured
            else ComposerInput(parts=(objective,))
        )
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt, mode="goal", input_surface="webui")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.goal.objective == objective
        assert operation.goal.max_iterations == 0
        assert operation.goal.status == "max_iterations"
        assert operation.goal.iteration == 0
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        detail = await app.get_thread(thread.thread_id)
        assert detail.thread.goal.objective == objective
        assert detail.thread.goal.status == "max_iterations"
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Use normal mode")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.goal is None
        assert (await app.get_thread(thread.thread_id)).thread.goal is None


async def test_resumed_goal_token_totals_do_not_double_count_a_segment():
    from pydantic_ai.usage import RunUsage

    previous = GoalView(objective="task", input_tokens=10, output_tokens=5, status="suspended")
    capability = GoalCapability(previous)
    usage = RunUsage(input_tokens=20, output_tokens=7)
    first = await capability.finish(HarnessState.new(), status="suspended", usage=usage)
    repeated = await capability.finish(first, status="suspended", usage=usage)
    assert saved_goal(repeated).input_tokens == 30
    assert saved_goal(repeated).output_tokens == 12
