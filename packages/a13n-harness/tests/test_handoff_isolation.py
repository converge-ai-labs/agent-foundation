from __future__ import annotations

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness._handoff import HANDOFF_CAPABILITY_ID
from a13n_harness.capabilities import CodeActCapability, HandoffCapability, ToolSearchCapability, ToolSearchGroup
from a13n_harness.capabilities.context import ContextRestoredEvent
from a13n_harness.context import AgentContext
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from .test_tool_discovery_retention import _scripted_model

pytestmark = pytest.mark.anyio


class RenameHandoff(AbstractCapability):
    def get_wrapper_toolset(self, toolset):
        return toolset.renamed({"context_continue_work": "summarize"})


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("sibling", ["ordinary", "duplicate"])
@pytest.mark.parametrize("renamed", [False, True])
async def test_handoff_rejects_entire_mixed_response_before_dispatch(reverse, sibling, renamed):
    effects = []
    restored = []
    requests = 0
    name = "context_continue_work" if renamed else "summarize"

    def ordinary() -> str:
        effects.append("executed")
        return "fresh result"

    class Observe(AbstractCapability):
        async def on_event(self, ctx, *, event):
            if isinstance(event, ContextRestoredEvent):
                restored.append(event)

    def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            calls = [
                ToolCallPart(name, {"content": "stale summary"}, tool_call_id="handoff"),
                ToolCallPart(
                    "ordinary" if sibling == "ordinary" else name,
                    {} if sibling == "ordinary" else {"content": "duplicate"},
                    tool_call_id="sibling",
                ),
            ]
            return ModelResponse(parts=list(reversed(calls)) if reverse else calls)
        if requests == 2:
            assert effects == []
            assert restored == []
            assert "only executable tool call" in ModelMessagesTypeAdapter.dump_json(messages).decode()
            return ModelResponse(parts=[ToolCallPart("ordinary", {}, tool_call_id="work")])
        if requests == 3:
            assert any(
                isinstance(part, ToolReturnPart) and part.content == "fresh result"
                for message in messages
                for part in message.parts
            )
            if renamed:
                return ModelResponse(parts=[TextPart("done")])
            return ModelResponse(parts=[ToolCallPart(name, {"content": "fresh result"}, tool_call_id="fresh")])
        assert len(restored) == 1
        assert "stale summary" not in ModelMessagesTypeAdapter.dump_json(messages).decode()
        return ModelResponse(parts=[TextPart("done")])

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_scripted_model(model),
        capabilities=(
            HandoffCapability(),
            *([RenameHandoff()] if renamed else []),
            Capability(tools=[ordinary]),
            Observe(),
        ),
    )
    result = await executable.run("Work then summarize", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert effects == ["executed"]


@pytest.mark.parametrize("renamed", [False, True])
async def test_handoff_hard_exclusion_survives_outer_allow_policy(renamed):
    class AllowAll(AbstractCapability):
        def get_wrapper_toolset(self, toolset):
            if renamed:
                toolset = toolset.renamed({"continue_work": "summarize"}).prefixed("context")
            denied = CodeActPolicyToolset(toolset, CodeActToolPolicy(default=False))
            return CodeActPolicyToolset(denied, CodeActToolPolicy(default=True))

    name = "context_continue_work" if renamed else "summarize"
    requests = 0

    def model(messages, info):
        nonlocal requests
        requests += 1
        runner = next(tool for tool in info.function_tools if tool.name == "run_code")
        assert f"async def {name}(" not in runner.description
        if requests == 1:
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": f"await {name}(content='forbidden')"})])
        assert "# Context Summary" not in ModelMessagesTypeAdapter.dump_json(messages).decode()
        return ModelResponse(parts=[TextPart("done")])

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_scripted_model(model),
        capabilities=(HandoffCapability(), AllowAll(), CodeActCapability()),
    )
    result = await executable.run("test", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"


async def test_codeact_forwarding_cannot_mutate_handoff_owner():
    seen = []
    errors = []

    async def forward(ctx: RunContext[AgentContext]) -> str:
        owner = ctx.capabilities[HANDOFF_CAPABILITY_ID]
        assert isinstance(owner, HandoffCapability)
        assert owner._toolset is not None
        try:
            return await owner._toolset.summarize(ctx, "forbidden")
        except ToolFailed as exc:
            errors.append(str(exc))
            raise
        finally:
            seen.append(owner._toolset.state)

    requests = 0

    def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": "await forward()"})])
        assert errors == ["Handoff must be called directly, outside CodeAct."]
        return ModelResponse(parts=[TextPart("done")])

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_scripted_model(model),
        capabilities=(
            HandoffCapability(),
            CodeActCapability(),
            Capability(
                toolsets=[
                    CodeActPolicyToolset(FunctionToolset([forward]), CodeActToolPolicy(default=True)),
                ]
            ),
        ),
    )
    result = await executable.run("test", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert len(seen) == 1
    assert seen[0].operation_id is None and seen[0].summary is None


async def test_handoff_cannot_be_hidden_behind_proxy():
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_scripted_model(lambda messages, info: ModelResponse(parts=[TextPart("unused")])),
        capabilities=(ToolSearchCapability(groups={"context": ToolSearchGroup(HandoffCapability(), "Context")}),),
    )
    result = await executable.run("test", bindings=RunBindings.embedded())
    assert result.status == "failed"
    assert result.failure.details["exception_type"] == "UserError"


async def test_unrelated_summarize_name_remains_codeact_eligible():
    def summarize(value: str) -> str:
        return value.upper()

    requests = 0

    def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": "await summarize(value='hello')"})])
        assert "HELLO" in ModelMessagesTypeAdapter.dump_json(messages).decode()
        return ModelResponse(parts=[TextPart("done")])

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_scripted_model(model),
        capabilities=(
            CodeActCapability(),
            Capability(
                toolsets=[
                    CodeActPolicyToolset(FunctionToolset([summarize]), CodeActToolPolicy(default=True)),
                ]
            ),
        ),
    )
    result = await executable.run("test", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"


@pytest.mark.parametrize("replacement", ["compact", "summarize", "pending", "pending_fast"])
@pytest.mark.parametrize("runner", ["run_code", "run_program"])
async def test_retained_runner_keeps_discovery_until_safe_replacement(tmp_path, replacement, runner):
    import asyncio
    import json

    from a13n_harness import CodeActStateOwner
    from a13n_harness.capabilities import CompactionCapability, CompactionPolicy, ToolExecutionCapability
    from a13n_harness.capabilities.context import _COMPACTION_PROMPT
    from a13n_harness.tool_execution import ToolExecutionConfig, ToolExecutionRuntime
    from pydantic_ai import Tool
    from pydantic_ai.capabilities import ToolSearch
    from pydantic_ai.messages import NativeToolCallPart, NativeToolReturnPart, UserPromptPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from pydantic_ai.usage import RequestUsage

    from .test_codeact import _local_environment

    pending = replacement in {"pending", "pending_fast"}
    fast = replacement == "pending_fast"
    started, release = asyncio.Event(), asyncio.Event()
    runtime = ToolExecutionRuntime()
    original = None
    steps = 0
    restored = []
    completed = []
    compact_inputs = []
    (tmp_path / "work.codeact.py").write_text(
        "async def main(inputs):\n    await tool_a()\n    return await tool_b()\n"
    )

    async def tool_a(ctx: RunContext[AgentContext]) -> int:
        nonlocal original
        original = ctx
        assert ctx.is_tool_available("tool_b")
        started.set()
        if not fast:
            await release.wait()
        return 41

    async def tool_b() -> int:
        completed.append("b")
        return 42

    class Observe(AbstractCapability):
        async def after_model_request(self, ctx, *, request_context, response):
            if steps >= 2 and any(isinstance(p, ToolCallPart) for p in response.parts):
                response.usage = RequestUsage(input_tokens=3000, output_tokens=10)
            return response

        async def on_event(self, ctx, *, event):
            if isinstance(event, ContextRestoredEvent):
                assert completed == ["b"]
                restored.append(event.source)

    async def model(messages, info):
        nonlocal steps
        if any(isinstance(p, UserPromptPart) and p.content == _COMPACTION_PROMPT for p in messages[-1].parts):
            compact_inputs.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
            assert completed == ["b"]
            yield "Completed computation: 42."
            return
        steps += 1
        if pending and steps == 2:
            yield {0: NativeToolCallPart("web_search", {}, tool_call_id="native")}
            yield {
                1: DeltaToolCall(
                    name="summarize", json_args=json.dumps({"content": "stale summary"}), tool_call_id="pending"
                )
            }
            return
        stage = steps - int(pending and steps > 2)
        # Repeated busy feedback must not exhaust the model/tool retry budget.
        if replacement == "summarize" and stage >= 4:
            assert "Wait for retained CodeAct" in ModelMessagesTypeAdapter.dump_json(messages).decode()
            stage = 3 if stage <= 6 else stage - 3
        if fast and stage == 4:
            assert restored == ["summarize"]
            assert "stale summary" not in ModelMessagesTypeAdapter.dump_json(messages).decode()
            yield "done"
            return
        if stage == 1:
            name, args = "search_tools", {"queries": ["tool_a", "tool_b"]}
        elif stage == 2:
            if pending:
                yield {1: NativeToolReturnPart("web_search", "result", tool_call_id="native")}
            name = runner
            args = (
                {"code": "await tool_a()\nawait tool_b()"}
                if runner == "run_code"
                else {"path": "/workspace/work.codeact.py"}
            )
        elif stage == 3:
            await started.wait()
            assert not restored
            assert bool(original.deps._tool_executions.history_calls) is not fast
            if pending:
                owner = original.capabilities[HANDOFF_CAPABILITY_ID]
                assert owner._toolset.state.summary is None
                assert any(
                    isinstance(part, NativeToolReturnPart) and part.tool_call_id == "native"
                    for message in messages
                    for part in message.parts
                )
            if fast:
                assert completed == ["b"]
                assert "42" in ModelMessagesTypeAdapter.dump_json(messages).decode()
                name, args = "summarize", {"content": "Completed computation: 42."}
            else:
                name, args = (
                    ("summarize", {"content": "stale summary"})
                    if replacement == "summarize"
                    else ("tool_execution_info", {})
                )
        elif stage == 4:
            assert not restored
            assert original.is_tool_available("tool_b")
            if replacement == "summarize":
                assert "Wait for retained CodeAct" in ModelMessagesTypeAdapter.dump_json(messages).decode()
                owner = original.capabilities[HANDOFF_CAPABILITY_ID]
                assert owner._toolset.state.summary is None
            release.set()
            records = await runtime.summarize(original.deps.thread_id)
            assert len(records) == 1
            settled = await runtime.wait(original.deps.thread_id, records[0].execution_id)
            assert settled.record.status == "succeeded"
            assert not original.deps._tool_executions.history_calls
            name, args = "tool_execution_result", {"execution_id": records[0].execution_id}
        elif stage == 5 and replacement != "compact":
            assert "42" in ModelMessagesTypeAdapter.dump_json(messages).decode()
            name, args = "summarize", {"content": "Completed computation: 42."}
        else:
            assert restored == ["compact" if replacement == "compact" else "summarize"]
            assert "stale summary" not in ModelMessagesTypeAdapter.dump_json(messages).decode()
            yield "done"
            return
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{steps}")}

    source = CodeActPolicyToolset(
        FunctionToolset([Tool(tool_a, defer_loading=True), Tool(tool_b, defer_loading=True)], id="source"),
        CodeActToolPolicy(default=True),
    )
    context_capability = (
        CompactionCapability(CompactionPolicy(trigger_tokens=2000)) if replacement == "compact" else HandoffCapability()
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(
            Capability(toolsets=[source]),
            ToolSearch(strategy="keywords"),
            CodeActCapability(),
            context_capability,
            Observe(),
            ToolExecutionCapability(ToolExecutionConfig(mode="auto", foreground_wait_seconds=30 if fast else 0)),
        ),
    )
    try:
        async with asyncio.timeout(20):
            result = await executable.run(
                "Compute A then B",
                bindings=RunBindings.embedded(
                    tool_execution=runtime,
                    codeact_state=CodeActStateOwner(),
                    environment=_local_environment(tmp_path),
                ),
            )
            assert result.output_or_raise() == "done"
        assert completed == ["b"]
        if replacement == "compact":
            assert len(compact_inputs) == 1
            assert "42" in compact_inputs[0]
    finally:
        release.set()
        await runtime.close()
