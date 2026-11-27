from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path
from textwrap import indent
from unittest.mock import Mock

import pytest
from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.capabilities import CodeActCapability, CodeActConfig, ToolExecutionCapability
from a13n_harness.codeact.programs import load_program_source
from a13n_harness.codeact.state import CodeActStateOwner
from a13n_harness.errors import DefinitionError
from a13n_harness.tool_execution import ToolExecutionConfig, ToolExecutionRuntime
from a13n_harness.toolsets.codeact import _build_catalog
from pydantic import BaseModel, Field
from pydantic_ai import DeferredToolResults, ModelRetry, Tool, ToolOutput
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, HandleDeferredToolCalls
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.realtime import RealtimeModel

from .test_codeact import _codeact_tools, _local_environment, _tool_returns

pytestmark = pytest.mark.anyio


class Answer(BaseModel):
    total: int = Field(gt=0)


class OtherAnswer(BaseModel):
    detail: str


def _call(name, args, *, call_id="code"):
    return {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=call_id)}


@pytest.mark.parametrize("runner", ["run_code", "run_program"])
@pytest.mark.parametrize("union", [False, True], ids=["single", "union"])
async def test_codeact_submits_structured_output_and_stops_the_feed(tmp_path: Path, runner: str, union: bool) -> None:
    effects: list[int] = []
    requests = 0
    output_thread = threading.get_ident()

    def double(value: int) -> int:
        effects.append(value)
        return value * 2

    async def model(messages, info):
        nonlocal requests
        requests += 1
        assert requests == 1, "no model round-trip follows the submission"
        tools = {tool.name for tool in info.output_tools or []}
        name = "final_result_Answer" if union else "final_result"
        assert name in tools
        assert f"`{name}` (final output)" in info.instructions
        if union:
            assert "`final_result_OtherAnswer` (final output)" in info.instructions
        code = (
            "total = await double(value=21)\n"
            f"try:\n    {name}(total=total)\n"
            "finally:\n    await double(value=99)\n"
            "await double(value=100)"
        )
        arguments = {"code": code}
        if runner == "run_program":
            (tmp_path / "submit.codeact.py").write_text("async def main(inputs):\n" + indent(code, "    ") + "\n")
            arguments = {"path": "/workspace/submit.codeact.py"}
        yield _call(runner, arguments)

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=Answer | OtherAnswer if union else Answer,
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(CodeActConfig(allow_final_output=True)),
            _codeact_tools(double, allowed=("double",)),
        ),
    )

    @executable._agent.output_validator
    async def validate_answer(answer):
        assert threading.get_ident() == output_thread
        return answer

    result = await executable.run("Calculate", bindings=RunBindings.embedded(environment=_local_environment(tmp_path)))
    assert result.output_or_raise() == Answer(total=42)
    assert effects == [21]
    assert _tool_returns(result.all_messages(), runner)[-1].outcome == "success"


@pytest.mark.parametrize("rejection", ["schema", "validator"])
async def test_codeact_submission_uses_native_output_retries_with_a_host_runtime(rejection: str) -> None:
    requests = 0
    runtime = ToolExecutionRuntime()
    invalid = -1 if rejection == "schema" else 1

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            yield _call("run_code", {"code": f"final_result(total={invalid})"}, call_id="rejected")
        else:
            assert requests == 2
            retry = _tool_returns(messages, "run_code")[-1]
            assert retry.tool_call_id == "rejected"
            assert retry.retry is not None
            assert retry.retry.cause == ("validation_error" if rejection == "schema" else "model_retry")
            feedback = "greater than 0" if rejection == "schema" else "Please return an even total"
            assert feedback in str(retry.content)
            yield _call("run_code", {"code": "final_result(total=42)"}, call_id="accepted")

    executable = HarnessBuilder().build(
        AgentSpec(retries={"tools": 0, "output": 1}),
        output_type=Answer,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(CodeActConfig(allow_final_output=True)), ToolExecutionCapability()),
    )

    @executable._agent.output_validator
    def require_even(answer: Answer) -> Answer:
        if answer.total % 2:
            raise ModelRetry("Please return an even total")
        return answer

    try:
        result = await executable.run(
            "Calculate", bindings=RunBindings.embedded(tool_execution=runtime, codeact_state=CodeActStateOwner())
        )
    finally:
        await runtime.close()
    assert requests == 2
    assert result.output_or_raise() == Answer(total=42)


async def test_retained_program_cannot_acquire_output_functions_from_a_later_step(tmp_path: Path, monkeypatch) -> None:
    requests = 0
    release_source = asyncio.Event()
    runtime = ToolExecutionRuntime()
    (tmp_path / "submit.codeact.py").write_text("async def main(inputs):\n    final_result(total=42)\n")

    class HideInitialOutput(AbstractCapability):
        async def prepare_output_tools(self, ctx, tool_defs):
            return [] if ctx.run_step == 1 else tool_defs

    async def delayed_load(ctx, path, *, max_source_bytes):
        await release_source.wait()
        return await load_program_source(ctx, path, max_source_bytes=max_source_bytes)

    monkeypatch.setattr("a13n_harness.toolsets.codeact.load_program_source", delayed_load)

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            assert "(final output)" not in info.instructions
            yield _call("run_program", {"path": "/workspace/submit.codeact.py"})
        else:
            assert requests == 2
            assert "final_result" in {tool.name for tool in info.output_tools}
            receipt = _tool_returns(messages, "run_program")[-1].content
            assert receipt["type"] == "tool_execution_receipt"
            original = next(record for record in runtime.active if record.execution_id == receipt["execution_id"])
            release_source.set()
            settled = await runtime.wait(original.thread_id, original.execution_id, timeout_seconds=5)
            assert settled is not None and settled.record.status == "failed"
            assert json.loads(settled.record.error)["error"]["exception_type"] == "NameError"
            yield _call("final_result", {"total": 7}, call_id="native")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=Answer,
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(CodeActConfig(allow_final_output=True)),
            ToolExecutionCapability(ToolExecutionConfig(mode="auto", foreground_wait_seconds=0)),
            HideInitialOutput(),
        ),
    )
    try:
        result = await executable.run(
            "Calculate",
            bindings=RunBindings.embedded(
                tool_execution=runtime, codeact_state=CodeActStateOwner(), environment=_local_environment(tmp_path)
            ),
        )
    finally:
        await runtime.close()
    assert result.output_or_raise() == Answer(total=7)


@pytest.mark.parametrize("plain", [False, True], ids=["default-config", "plain-text"])
async def test_codeact_does_not_expose_output_functions_when_inactive(plain: bool) -> None:
    requests = 0

    async def model(messages, info):
        nonlocal requests
        requests += 1
        assert "(final output)" not in info.instructions
        if requests == 1:
            # The name of both the structured target and Pydantic AI's synthetic text target.
            yield _call("run_code", {"code": "final_result()"})
        elif requests == 2:
            failed = _tool_returns(messages, "run_code")[-1]
            assert failed.outcome == "failed"
            assert json.loads(failed.content)["error"]["exception_type"] == "NameError"
            yield _call("run_code", {"code": "6 * 7"}, call_id="normal")
        else:
            assert requests == 3
            assert _tool_returns(messages, "run_code")[-1].content == 42
            if plain:
                yield "done"
            else:
                yield _call("final_result", {"total": 42}, call_id="native")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str if plain else Answer,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(CodeActConfig(allow_final_output=True) if plain else None),),
    )
    assert (await executable.run("Calculate")).output_or_raise() == ("done" if plain else Answer(total=42))


async def test_codeact_realtime_uses_the_ordinary_catalog_even_with_output_targets() -> None:
    class ObserveRealtimeCatalog(AbstractCapability):
        async def before_model_request(self, ctx, request_context):
            live_ctx = replace(ctx, model=Mock(spec=RealtimeModel))
            assert live_ctx.realtime and live_ctx.output_tools
            owner = next(cap for cap in ctx.capabilities.values() if isinstance(cap, CodeActCapability))
            live_catalog = await owner._live_instructions(live_ctx)
            assert "`load`" in live_catalog
            assert "(final output)" not in live_catalog
            execution_catalog = _build_catalog(ctx.tool_manager.tools, ctx=live_ctx, allow_final_output=True)
            assert "final_result" not in execution_catalog.definitions
            assert not execution_catalog.output_names
            return request_context

    async def model(messages, info):
        yield _call("final_result", {"total": 42}, call_id="native")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=Answer,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(CodeActConfig(allow_final_output=True)), ObserveRealtimeCatalog()),
    )
    assert (await executable.run("Calculate")).output_or_raise() == Answer(total=42)


@pytest.mark.parametrize("output_name", ["look_up", "run-code", "run-program"])
async def test_codeact_rejects_output_names_that_collide_in_the_sandbox(output_name: str) -> None:
    def lookup() -> None:
        raise AssertionError("a collision must fail before tool execution")

    async def model(messages, info):
        pytest.fail("a collision must fail before the model request")
        yield "unused"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=ToolOutput(Answer, name=output_name),
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(CodeActConfig(allow_final_output=True)),
            _codeact_tools(Tool(lookup, name="look-up", defer_loading=True), allowed=("look-up",)),
        ),
    )
    with pytest.raises(DefinitionError, match=f"CodeAct output tool '{output_name}' conflicts with sandbox name"):
        (await executable.run("Calculate")).output_or_raise()


@pytest.mark.parametrize("resolution", ["unresolved", "unsupported", "resolved", "approved", "denied"])
async def test_codeact_final_output_respects_deferred_callbacks(resolution: str) -> None:
    requests = 0
    effects: list[str] = []
    approval = resolution in {"approved", "denied"}
    accepted = resolution in {"resolved", "approved"}

    async def deferred() -> int:
        effects.append("called")
        if approval:
            return 42
        raise CallDeferred()

    async def resolve(ctx, requests):
        effects.append("resolved")
        if approval:
            return DeferredToolResults(approvals={requests.approvals[0].tool_call_id: accepted})
        return DeferredToolResults(calls={requests.calls[0].tool_call_id: 42})

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            code = (
                "import asyncio\n"
                "# The first callback must settle even though the feed will stop at the second.\n"
                "await asyncio.gather(deferred(), final_result(total=42))"
            )
            yield _call("run_code", {"code": code})
        else:
            assert requests == 2 and not accepted
            assert effects == (["resolved"] if approval else ["called"])
            failed = _tool_returns(messages, "run_code")[-1]
            assert failed.outcome == "failed"
            assert "cannot submit final output after a failed deferred call" in failed.content
            yield _call("final_result", {"total": 7}, call_id="native")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=Answer,
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(CodeActConfig(allow_final_output=True)),
            _codeact_tools(Tool(deferred, requires_approval=approval), allowed=("deferred",)),
            *((HandleDeferredToolCalls(handler=resolve),) if resolution in {"resolved", "approved", "denied"} else ()),
        ),
    )
    result = await executable.run(
        "Calculate", bindings=RunBindings.embedded(deferred_tools_supported=resolution != "unsupported")
    )
    assert result.output_or_raise() == Answer(total=42 if accepted else 7)
    if accepted:
        assert effects == (["resolved", "called"] if approval else ["called", "resolved"])
        assert requests == 1
