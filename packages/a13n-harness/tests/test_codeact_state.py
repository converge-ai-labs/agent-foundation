from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from a13n_harness import HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import CodeActCapability, CodeActConfig
from a13n_harness.context import AgentContext
from a13n_harness.model_context import user_prompt_content
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
from a13n_harness.toolsets.codeact import _ExecutionBudget
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RunUsage, UsageLimits

from .test_codeact import _codeact_tools, _local_environment

pytestmark = pytest.mark.anyio


async def _run(
    calls: list[tuple[str, dict[str, Any]]],
    *,
    previous_state: HarnessState | None = None,
    config: CodeActConfig | None = None,
    capabilities: tuple[AbstractCapability[Any], ...] = (),
    bindings: RunBindings | None = None,
    usage_limits: UsageLimits | None = None,
):
    seen: list[ToolReturnPart | RetryPromptPart] = []
    contexts: list[str] = []
    index = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal index
        contexts.append(
            "\n".join(
                item.content
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
                for item in user_prompt_content(part)
            )
            + (info.instructions or "")
        )
        if index:
            responses = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart | RetryPromptPart) and part.tool_call_id == f"step-{index}"
            ]
            seen.append(responses[-1])
        if index == len(calls):
            yield "done"
            return
        name, args = calls[index]
        index += 1
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"step-{index}")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(*capabilities, CodeActCapability(config)),
    )
    result = await executable.run(
        "run", bindings=bindings or RunBindings.embedded(), previous_state=previous_state, usage_limits=usage_limits
    )
    assert result.output_or_raise() == "done"
    return result, seen, contexts


def _code(source: str, *, restart: bool = False) -> tuple[str, dict[str, Any]]:
    return "run_code", {"code": source, "restart": restart}


def _values(state: HarnessState | None) -> Any:
    assert state is not None
    return state.agent_context_state.entries["a13n.codeact"].data["values"]


def _stored(values: dict[str, Any]) -> HarnessState:
    return HarnessState.new(
        agent_context_state=AgentContextStateSnapshot(
            entries={"a13n.codeact": CapabilityState(version="1", data={"values": values})}
        )
    )


async def test_explicit_values_survive_failure_restart_and_json_continuation() -> None:
    result, seen, _ = await _run(
        [
            _code('temporary = 9\nawait store(key="search.results", value={"ids": [1, 2]})\n1 / 0'),
            _code('await load(key="search.results")', restart=True),
            _code("temporary"),
            _code('value = await load(key="search.results")\nvalue["ids"].append(3)\nawait load(key="search.results")'),
        ]
    )
    assert isinstance(seen[0], ToolReturnPart) and seen[0].outcome == "failed"
    assert seen[1].content == {"ids": [1, 2]}
    assert isinstance(seen[2], ToolReturnPart) and seen[2].outcome == "failed"
    assert seen[3].content == {"ids": [1, 2]}
    assert _values(result.state) == {"search.results": {"ids": [1, 2]}}
    assert result.state is not None
    restored = HarnessState.model_validate_json(result.state.model_dump_json())
    continued, seen, contexts = await _run([_code('await load(key="search.results")')], previous_state=restored)
    assert seen[0].content == {"ids": [1, 2]}
    assert "CodeAct stored keys (1 of 1)" in contexts[0]
    assert continued.state is not None and continued.state.thread_id == restored.thread_id
    fork, _, _ = await _run([_code('await store(key="search.results", value=[3])')], previous_state=restored.fork())
    assert _values(fork.state) == {"search.results": [3]}
    assert _values(restored) == {"search.results": {"ids": [1, 2]}}
    _, empty, _ = await _run([_code("await load()")])
    assert empty[0].content == []


async def test_store_load_forget_are_key_only_and_null_is_not_missing() -> None:
    result, seen, _ = await _run(
        [
            ("store", {"key": "nullable", "value": None}),
            _code('await load(key="nullable")'),
            _code('await load(key="missing")'),
            _code("await load()"),
            _code('await forget(key="nullable")'),
            _code('await forget(key="nullable")'),
        ]
    )
    assert seen[1].content is None
    assert isinstance(seen[2], ToolReturnPart) and seen[2].outcome == "failed"
    assert seen[3].content == ["nullable"]
    assert seen[4].content is True
    assert seen[5].content is False
    assert _values(result.state) == {}


@pytest.mark.parametrize("runner", ["run_code", "run_program"])
async def test_complex_values_cross_boundaries_only_as_json(runner: str, tmp_path: Path) -> None:
    received: list[Any] = []

    def echo(value: Any) -> Any:
        received.append(value)
        return value

    source = (
        "z = (1 + 2j) * 2\n"
        'value = await echo(value={"real": z.real, "imag": z.imag})\n'
        'await store(key="complex.parts", value=value)\n'
    )
    if runner == "run_program":
        (tmp_path / "complex.codeact.py").write_text(
            "async def main(inputs):\n"
            + "".join(f"    {line}\n" for line in source.splitlines())
            + "    return value\n"
        )
        calls = [(runner, {"path": "complex.codeact.py"})]
    else:
        calls = [_code(source + "value"), _code("[z.real, z.imag]")]
    calls.append(_code('await load(key="complex.parts")'))
    result, seen, _ = await _run(
        calls,
        capabilities=(_codeact_tools(echo, allowed=("echo",)),),
        bindings=RunBindings.embedded(environment=_local_environment(tmp_path)),
    )
    expected = {"real": 2.0, "imag": 4.0}
    assert received == [expected]
    assert seen[0].content == seen[-1].content == expected
    if runner == "run_code":
        assert seen[1].content == [2.0, 4.0]
    assert result.state is not None
    restored = HarnessState.model_validate_json(result.state.model_dump_json())
    assert _values(restored) == {"complex.parts": expected}


@pytest.mark.parametrize("expression", ["z", '{"nested": [z]}'])
async def test_complex_output_rejection_resets_inline_state_but_preserves_stored_values(expression: str) -> None:
    result, seen, _ = await _run(
        [
            _code('await store(key="saved", value=7)\nz = 1 + 2j\n' + expression),
            _code("z"),
            _code('await load(key="saved")'),
        ]
    )
    for response in seen[:2]:
        assert isinstance(response, ToolReturnPart) and response.outcome == "failed"
    assert seen[2].content == 7
    assert _values(result.state) == {"saved": 7}


@pytest.mark.parametrize("boundary", ["argument", "result"])
async def test_complex_nested_tool_values_are_rejected(boundary: str) -> None:
    invoked: list[Any] = []

    def echo(value: Any) -> Any:
        invoked.append(value)
        return 1 + 2j if boundary == "result" else value

    argument = '{"nested": [1 + 2j]}' if boundary == "argument" else "1"
    _, seen, _ = await _run(
        [_code(f"temporary = 9\nawait echo(value={argument})"), _code("temporary"), _code("2 + 2")],
        capabilities=(_codeact_tools(echo, allowed=("echo",)),),
    )
    assert invoked == ([] if boundary == "argument" else [1])
    for response in seen[:2]:
        assert isinstance(response, ToolReturnPart) and response.outcome == "failed"
    assert seen[2].content == 4


async def test_monty_owns_python_scope_resolution() -> None:
    source = "def report():\n    return normalize(4)\ndef normalize(x):\n    return x + 1\nreport()"
    _, seen, _ = await _run([_code("saved = 7"), _code(source), _code("saved")])
    assert seen[1].content == 5
    assert seen[2].content == 7


async def test_conditional_function_survives_multiple_inline_feeds() -> None:
    _, seen, _ = await _run([_code("if True:\n    def helper(x):\n        return x + 1"), _code("helper(4)")])
    assert seen[1].content == 5


async def test_program_uses_same_values_but_fresh_interpreter(tmp_path: Path) -> None:
    (tmp_path / "save.codeact.py").write_text(
        'async def main(inputs):\n    await store(key="program.result", value=normalize(inputs["n"]))\n'
        '    return await load(key="program.result")\ndef normalize(n):\n    return n + 1\n'
    )
    _, seen, _ = await _run(
        [("run_program", {"path": "save.codeact.py", "inputs": {"n": 4}}), _code('await load(key="program.result")')],
        bindings=RunBindings.embedded(environment=_local_environment(tmp_path)),
    )
    assert [part.content for part in seen] == [5, 5]


async def test_concurrent_writes_do_not_lose_keys() -> None:
    result, seen, _ = await _run(
        [_code("import asyncio\nawait asyncio.gather(*[store(key=str(n), value=n) for n in range(20)])\nawait load()")]
    )
    assert seen[0].content == sorted(str(n) for n in range(20))
    assert _values(result.state) == {str(n): n for n in range(20)}


@pytest.mark.parametrize(
    ("config", "rejected"),
    [
        (CodeActConfig(max_state_entries=1), 'await store(key="second", value=2)'),
        (CodeActConfig(max_state_bytes=64), 'await store(key="first", value="x" * 100)'),
        (CodeActConfig(), 'await store(key="first", value=float("inf"))'),
        (CodeActConfig(), 'await store(key="first", value=1 + 2j)'),
        (CodeActConfig(), 'await store(key="first", value={"nested": [1 + 2j]})'),
        (CodeActConfig(), 'await store(key="", value=1)'),
        (CodeActConfig(), 'await store(key="x" * 257, value=1)'),
    ],
)
async def test_rejected_store_preserves_previous_value(config: CodeActConfig, rejected: str) -> None:
    result, seen, _ = await _run(
        [_code('await store(key="first", value=1)'), _code(rejected), _code('await load(key="first")')], config=config
    )
    assert isinstance(seen[1], ToolReturnPart) and seen[1].outcome == "failed"
    assert seen[2].content == 1
    assert _values(result.state) == {"first": 1}


async def test_context_projects_bounded_keys_not_values() -> None:
    state = _stored({f"result.{n:03d}": "private-value-not-for-context" for n in range(40)})
    _, seen, contexts = await _run([_code("await load()")], previous_state=state)
    assert "CodeAct stored keys (32 of 40)" in contexts[0]
    assert "private-value-not-for-context" not in contexts[0]
    assert len(seen[0].content) == 40
    _, _, contexts = await _run([], previous_state=_stored({"\U0001f600" * 250: 1}))
    index = contexts[0].split("CodeAct stored keys", 1)[1]
    assert len(index.encode()) < 4096


async def test_restored_values_cannot_restore_old_tool_authority() -> None:
    invoked: list[int] = []

    def hidden(value: int) -> int:
        invoked.append(value)
        return value

    result, seen, _ = await _run(
        [_code('await store(key="saved", value=2)\nawait hidden(value=1)')],
        previous_state=_stored({"tool.name": "hidden"}),
        capabilities=(_codeact_tools(hidden, allowed=()),),
    )
    assert not invoked
    assert isinstance(seen[0], ToolReturnPart) and seen[0].outcome == "failed"
    assert _values(result.state) == {"tool.name": "hidden", "saved": 2}


async def test_parent_budget_waits_for_counted_callback_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    counted = asyncio.Event()
    second_admission = asyncio.Event()
    calls: list[int] = []
    original = _ExecutionBudget.admit

    async def synchronized_admit(self, ctx):
        if self.admitted == 1:
            await counted.wait()
            second_admission.set()
        return await original(self, ctx)

    monkeypatch.setattr(_ExecutionBudget, "admit", synchronized_admit)

    class AfterHook(AbstractCapability[AgentContext]):
        async def after_tool_execute(self, ctx, *, call, tool_def, args, result):
            if call.tool_name == "work" and result == 1:
                assert ctx.usage.tool_calls == 1
                counted.set()
                await second_admission.wait()
            return result

    async def work(value: int) -> int:
        calls.append(value)
        return value

    result, seen, _ = await _run(
        [_code("import asyncio\nawait asyncio.gather(work(value=1), work(value=2))")],
        capabilities=(_codeact_tools(work, allowed=("work",)), AfterHook()),
        usage_limits=UsageLimits(tool_calls_limit=3),
    )
    assert seen[0].content == [1, 2]
    assert calls == [1, 2]
    assert result.usage.tool_calls == 3


async def test_parent_budget_rejects_real_overflow_and_wait_is_cancellable() -> None:
    ctx = RunContext(deps=None, model=FunctionModel(lambda messages, info: "unused"), usage=RunUsage())
    ctx.usage_limits = UsageLimits(tool_calls_limit=2)
    budget = _ExecutionBudget(CodeActConfig())
    ordinal = await budget.admit(ctx)
    waiting = asyncio.create_task(budget.admit(ctx))
    await asyncio.sleep(0)
    assert not waiting.done()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert budget.admitted == budget.pending == 1
    ctx.usage.tool_calls = 1
    await budget.release(ordinal)
    with pytest.raises(UsageLimitExceeded):
        await budget.admit(ctx)
    assert budget.pending == 0


@pytest.mark.parametrize("enabled", [True, False])
async def test_state_tool_surface_requires_capability_and_has_no_message(enabled: bool) -> None:
    async def model(messages, info):
        tools = {tool.name: tool for tool in info.function_tools}
        assert ({"run_code", "run_program", "store", "load", "forget"} <= tools.keys()) is enabled
        if enabled:
            assert set(tools["store"].parameters_json_schema["properties"]) == {"key", "value"}
            assert set(tools["load"].parameters_json_schema["properties"]) == {"key"}
        else:
            assert not ({"store", "load", "forget"} & tools.keys())
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(),) if enabled else (),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"


async def test_store_survives_cancellation_and_pending_callbacks_are_drained() -> None:
    started = asyncio.Event()
    drained = asyncio.Event()

    async def block() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()

    async def model(messages, info):
        yield {
            0: DeltaToolCall(
                name="run_code",
                json_args=json.dumps({"code": 'await store(key="completed", value=1)\nawait block()'}),
                tool_call_id="cancel-code",
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(_codeact_tools(block, allowed=("block",)), CodeActCapability()),
    )
    async with executable.stream("run", bindings=RunBindings.embedded()) as stream:

        async def consume():
            async for _ in stream:
                pass

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=5)
        stream.cancel()
        await asyncio.wait_for(consumer, timeout=5)
    assert drained.is_set()
    assert stream.result is not None and stream.result.status == "cancelled"
    assert _values(stream.result.state) == {"completed": 1}


async def test_restored_values_obey_current_limits_before_model_execution() -> None:
    reached_model = False

    async def model(messages, info):
        nonlocal reached_model
        reached_model = True
        yield "unreachable"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(CodeActConfig(max_state_entries=1)),),
    )
    with pytest.raises(ValueError, match="max_state_entries"):
        await executable.run("run", bindings=RunBindings.embedded(), previous_state=_stored({"one": 1, "two": 2}))
    assert not reached_model
