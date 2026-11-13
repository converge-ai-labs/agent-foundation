"""Retained runners preserve explicit ordering without joining unrelated background work."""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from a13n_harness import AgentContext, HarnessBuilder, RunBindings
from a13n_harness._tool_execution import TOOL_EXECUTION_METADATA_KEY
from a13n_harness.capabilities import CodeActCapability, CodeActConfig
from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
from a13n_harness.codeact import runtime as codeact_runtime
from a13n_harness.codeact.runtime import CodeActRunState
from a13n_harness.codeact.state import CodeActStateOwner
from a13n_harness.tool_execution import ToolExecutionConfig, ToolExecutionRuntime
from pydantic_ai import RunContext, Tool
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tool_manager import ToolManager

from .test_codeact import _codeact_tools, _local_environment, _tool_returns

pytestmark = pytest.mark.anyio


def _runner_args(tmp_path, runner, code):
    if runner == "run_code":
        return {"code": code}
    path = tmp_path / "check.codeact.py"
    path.write_text("async def main(inputs):\n    " + code + "\n")
    return {"path": "/workspace/check.codeact.py"}


@pytest.mark.parametrize("runner", ["run_code", "run_program"])
@pytest.mark.parametrize("global_sequential", [False, True])
async def test_post_receipt_runner_only_joins_explicit_thread_ordering(tmp_path, runner, global_sequential):
    runtime = ToolExecutionRuntime()
    entered, finish, checked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests = 0
    receipt = None
    args = _runner_args(tmp_path, runner, "return await marker()" if runner == "run_program" else "await marker()")

    async def background() -> str:
        entered.set()
        await finish.wait()
        return "server stopped"

    async def marker() -> int:
        checked.set()
        return 42

    async def model(messages, info):
        nonlocal requests, receipt
        requests += 1
        if requests == 1:
            yield {0: DeltaToolCall(name="background", tool_call_id="background", json_args="{}")}
        elif requests == 2:
            receipt = _tool_returns(messages, "background")[-1].content
            assert receipt["type"] == "tool_execution_receipt"
            await entered.wait()
            yield {0: DeltaToolCall(name=runner, tool_call_id="check", json_args=json.dumps(args))}
        else:
            prior = next(record for record in runtime.active if record.tool_name == "background")
            assert prior.status == "running" and prior.foreground_released
            if global_sequential:
                pending = next(record for record in runtime.active if record.tool_name == runner)
                assert pending.status == "queued"
                assert not checked.is_set()
            else:
                await asyncio.wait_for(checked.wait(), 5)
            yield "independent work selected"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(),
            ToolExecutionCapability(ToolExecutionConfig(mode="auto", foreground_wait_seconds=0)),
            _codeact_tools(
                Tool(background, metadata={TOOL_EXECUTION_METADATA_KEY: "owned"}), marker, allowed=("marker",)
            ),
        ),
    )
    try:
        with ToolManager.parallel_execution_mode("sequential" if global_sequential else "parallel"):
            result = await executable.run(
                "start a server then check it",
                bindings=RunBindings.embedded(
                    tool_execution=runtime, codeact_state=CodeActStateOwner(), environment=_local_environment(tmp_path)
                ),
            )
        assert result.output_or_raise() == "independent work selected"
        finish.set()
        await runtime.join()
        records = await runtime.summarize(result.thread_id)
        assert {record.status for record in records} == {"succeeded"}
        assert checked.is_set()
        assert len(records) == 2
    finally:
        finish.set()
        await runtime.close()


@pytest.mark.parametrize("first_runner", ["run_program", "run_code"])
@pytest.mark.parametrize("cancel_first", [False, True])
async def test_cross_step_program_overlaps_retained_runner_and_keeps_its_owner(tmp_path, first_runner, cancel_first):
    entered = {label: asyncio.Event() for label in ("A", "B")}
    finish = {label: asyncio.Event() for label in entered}
    cleaned = {label: asyncio.Event() for label in entered}
    calls = []
    usages = []
    requests = 0
    receipts = {}
    runtime = ToolExecutionRuntime()
    program = tmp_path / "hold.codeact.py"
    program.write_text(
        'async def main(inputs):\n    value = await hold(label=inputs["label"])\n    await store(key=inputs["label"], value=value)\n    return value\n'
    )

    async def hold(ctx: RunContext[AgentContext], label: str) -> str:
        usages.append(ctx.usage)
        calls.append(label)
        entered[label].set()
        try:
            await finish[label].wait()
            return label
        finally:
            cleaned[label].set()

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            args = (
                {"code": 'saved = await hold(label="A")\nawait store(key="A", value=saved)\nsaved'}
                if first_runner == "run_code"
                else {"path": "/workspace/hold.codeact.py", "inputs": {"label": "A"}}
            )
            yield {0: DeltaToolCall(name=first_runner, tool_call_id="A", json_args=json.dumps(args))}
        elif requests == 2:
            receipts["A"] = _tool_returns(messages, first_runner)[-1].content
            await asyncio.wait_for(entered["A"].wait(), 5)
            yield {
                0: DeltaToolCall(
                    name="run_program",
                    tool_call_id="B",
                    json_args=json.dumps({"path": "/workspace/hold.codeact.py", "inputs": {"label": "B"}}),
                )
            }
        else:
            receipts["B"] = _tool_returns(messages, "run_program")[-1].content
            await asyncio.wait_for(entered["B"].wait(), 5)
            assert not cleaned["A"].is_set()
            assert len(runtime.active) == 2
            yield "both live"

    values = CodeActStateOwner()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(),
            ToolExecutionCapability(ToolExecutionConfig(mode="auto", foreground_wait_seconds=0)),
            _codeact_tools(hold, allowed=("hold",)),
        ),
    )
    try:
        result = await executable.run(
            "overlap",
            bindings=RunBindings.embedded(
                tool_execution=runtime, codeact_state=values, environment=_local_environment(tmp_path)
            ),
        )
        assert result.output_or_raise() == "both live"
        if cancel_first:
            await runtime.cancel(result.thread_id, receipts["A"]["execution_id"])
        else:
            finish["A"].set()
        a = await runtime.wait(result.thread_id, receipts["A"]["execution_id"], timeout_seconds=5)
        assert a.record.status == ("cancelled" if cancel_first else "succeeded"), a.record.error
        assert cleaned["A"].is_set() and not cleaned["B"].is_set()
        assert not (await runtime.info(result.thread_id, receipts["B"]["execution_id"])).terminal
        finish["B"].set()
        b = await runtime.wait(result.thread_id, receipts["B"]["execution_id"], timeout_seconds=5)
        assert b.record.status == "succeeded", b.record.error
        assert b.result.return_value == "B"
        assert sorted(calls) == ["A", "B"]
        assert usages[0] is usages[1]
        assert usages[0].tool_calls == (4 if cancel_first else 6)
        assert not runtime.active
        assert (await values.snapshot()).values == ({"B": "B"} if cancel_first else {"A": "A", "B": "B"})
    finally:
        for event in finish.values():
            event.set()
        await runtime.close()


async def test_concurrent_cold_programs_initialize_one_pool_without_serializing_feeds(monkeypatch):
    original = codeact_runtime.AsyncMonty
    initializing, allow_init = asyncio.Event(), asyncio.Event()
    entered = {label: asyncio.Event() for label in ("A", "B")}
    finish = asyncio.Event()
    pools = []

    @asynccontextmanager
    async def pool(**kwargs):
        pools.append(kwargs)
        initializing.set()
        await allow_init.wait()
        async with original(**kwargs) as instance:
            yield instance

    monkeypatch.setattr(codeact_runtime, "AsyncMonty", pool)
    state = CodeActRunState(CodeActConfig())

    async def dispatch(name, kwargs, ordinal):
        entered[kwargs["label"]].set()
        await finish.wait()
        return kwargs["label"]

    async def run(label):
        return await state.execute_program(
            "async def main(inputs):\n    return await hold(label=inputs['label'])\nawait main(inputs)",
            script_name="test.codeact.py",
            inputs={"inputs": {"label": label}},
            dispatch=dispatch,
            admit=AsyncMock(return_value=1),
            release=AsyncMock(),
            valid_names={"hold"},
            sequential_names=set(),
            global_sequential=False,
        )

    tasks = [asyncio.create_task(run("A"))]
    try:
        await initializing.wait()
        tasks.append(asyncio.create_task(run("B")))
        await asyncio.sleep(0)  # Let the second caller reach the still-pending initialization.
        allow_init.set()
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 5)
        assert len(pools) == 1
        finish.set()
        results = await asyncio.gather(*tasks)
        assert [result.completed.output for result in results] == ["A", "B"]
    finally:
        allow_init.set()
        finish.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await state.close()


async def test_cross_step_busy_inline_restart_preserves_the_active_feed():
    runtime = ToolExecutionRuntime()
    entered, finish = asyncio.Event(), asyncio.Event()
    requests = 0

    async def hold() -> int:
        entered.set()
        await finish.wait()
        return 41

    async def execution(call_id):
        records = await runtime.summarize(thread_id)
        record = next(record for record in records if record.tool_call_id == call_id)
        return await runtime.wait(thread_id, record.execution_id, timeout_seconds=5)

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            code, restart = "saved = await hold()\nsaved", False
        elif requests == 2:
            await asyncio.wait_for(entered.wait(), 5)
            code, restart = "saved = 0", True
        elif requests == 3:
            busy = await execution("code-2")
            assert busy.record.status == "failed"
            assert json.loads(busy.record.error)["error"]["inline_state"] == "unchanged"
            assert "Wait for its active execution" in json.loads(busy.record.error)["error"]["message"]
            assert not (await execution_without_wait("code-1")).terminal
            finish.set()
            assert (await execution("code-1")).result.return_value == 41
            code, restart = "saved + 1", False
        else:
            assert (await execution("code-3")).result.return_value == 42
            yield "bindings preserved"
            return
        yield {
            0: DeltaToolCall(
                name="run_code",
                tool_call_id=f"code-{requests}",
                json_args=json.dumps({"code": code, "restart": restart}),
            )
        }

    async def execution_without_wait(call_id):
        return next(record for record in await runtime.summarize(thread_id) if record.tool_call_id == call_id)

    from a13n_harness import HarnessState

    previous = HarnessState.new()
    thread_id = previous.thread_id
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(),
            ToolExecutionCapability(ToolExecutionConfig(mode="auto", foreground_wait_seconds=0)),
            _codeact_tools(hold, allowed=("hold",)),
        ),
    )
    try:
        result = await executable.run(
            "busy restart",
            previous_state=previous,
            bindings=RunBindings.embedded(tool_execution=runtime, codeact_state=CodeActStateOwner()),
        )
        assert result.output_or_raise() == "bindings preserved"
    finally:
        finish.set()
        await runtime.close()
