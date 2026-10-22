"""Runtime-owned Shell commands survive foreground and logical Run closure."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import ToolExecutionCapability
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
)
from a13n_harness.tool_execution import ToolExecutionConfig, ToolExecutionRuntime
from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .environment_helpers import DirectLocalSource

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fixture")]


def source(root):
    return DirectLocalSource(
        DirectLocalEnvironmentConfiguration(
            root=DirectLocalRootConfiguration(path=root),
            shell_profiles=(DirectLocalShellProfile(profile_id="default", executable=Path("/bin/sh")),),
        ),
        environment_id="runtime-shell",
    )


def executable(calls, results, *, capabilities=(), wait_seconds=0, mode="auto"):
    index = 0

    async def model(messages, info):
        nonlocal index
        returns = [part.content for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if index > 0:
            results.append(returns[-1])
        if index == len(calls):
            yield "done"
            return
        name, arguments = calls[index]
        index += 1
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=f"call-{index}")}

    return HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
            ToolExecutionCapability(ToolExecutionConfig(mode=mode, foreground_wait_seconds=wait_seconds)),
            *capabilities,
        ),
    )


def bindings(runtime, **kwargs):
    async def allow(invocation, metadata, *, context):
        return InvocationPolicyDecision.allow()

    return RunBindings.embedded(
        tool_execution=runtime, capabilities=(InvocationPolicyCapability(evaluator=allow),), **kwargs
    )


@pytest.mark.parametrize("mode", ["inline", "auto"])
async def test_shell_survives_run_close_and_next_run_controls_original_process(tmp_path, mode):
    runtime = ToolExecutionRuntime()
    first_results, next_results = [], []
    try:
        async with asyncio.timeout(15):
            first = await executable(
                [("shell_exec", {"command": "printf ready; read line; printf 'done:%s' \"$line\""})],
                first_results,
                mode=mode,
            ).run("start", environment=source(tmp_path), bindings=bindings(runtime))
            assert first.output_or_raise() == "done"
            receipt = first_results[0]
            assert receipt["type"] == "tool_execution_receipt"
            execution_id = receipt["execution_id"]
            thread_id = first.state.thread_id
            while runtime.resource(thread_id, execution_id) is None:
                record = await runtime.info(thread_id, execution_id)
                assert not record.terminal, record
                await asyncio.sleep(0.01)
            assert (await runtime.info(thread_id, execution_id)).status == "running"
            assert runtime.resource("other-thread", execution_id) is None
            second = await executable(
                [
                    ("shell_info", {"process_id": execution_id}),
                    ("shell_wait", {"process_id": execution_id, "timeout_seconds": 0}),
                    ("shell_input", {"process_id": execution_id, "data": "accepted\n"}),
                ],
                next_results,
            ).run("continue", previous_state=first.state, environment=source(tmp_path), bindings=bindings(runtime))
            assert second.output_or_raise() == "done"
            assert next_results[0]["status"]["phase"] == "running", next_results
            assert next_results[1]["stdout"]["text"] == "ready", next_results
            assert next_results[2]["accepted_bytes"] == len("accepted\n"), next_results
            settled = await runtime.wait(thread_id, execution_id)
            assert settled.record.status == "succeeded", settled
            assert settled.result["status"]["exit_code"] == 0
            assert settled.result["stdout"]["text"] == "readydone:accepted"
            assert runtime.resource(thread_id, execution_id) is None
            assert len(await runtime.summarize(thread_id)) == 1
    finally:
        await runtime.close()


@pytest.mark.parametrize("selection", ["only", "default", "alias", "cwd"])
async def test_inline_foreground_only_target_does_not_handoff(tmp_path, selection):
    from a13n_harness.environment import EnvironmentAction, EnvironmentMount, EnvironmentPermissionSet
    from a13n_harness.environment.advanced import create_environment_runtime

    foreground = tmp_path / "foreground"
    foreground.mkdir()
    mounts = {
        "foreground": EnvironmentMount(
            source=source(foreground),
            mount_path="/foreground",
            permission_ceiling=EnvironmentPermissionSet(operations=frozenset({EnvironmentAction.SHELL_EXEC})),
        )
    }
    if selection != "only":
        process = tmp_path / "process"
        process.mkdir()
        mounts["process"] = EnvironmentMount(source=source(process))
    environment = create_environment_runtime(
        mounts=mounts, default_mount="process" if selection in {"alias", "cwd"} else "foreground"
    )
    arguments = {"command": "sleep 0.05; printf finished"}
    if selection == "alias":
        arguments["alias"] = "foreground"
    elif selection == "cwd":
        arguments["cwd"] = "/foreground"
    runtime = ToolExecutionRuntime()
    results = []
    try:
        first = await executable([("shell_exec", arguments)], results, mode="inline").run(
            "foreground", bindings=bindings(runtime, environment=environment)
        )
        assert results[0]["stdout"]["text"] == "finished", results
        records = await runtime.summarize(first.thread_id)
        assert len(records) == 1
        assert records[0].status == "succeeded" and not records[0].foreground_released
    finally:
        await runtime.close()


async def test_inline_quick_shell_returns_final_result(tmp_path):
    runtime = ToolExecutionRuntime()
    results = []
    try:
        first = await executable(
            [("shell_exec", {"command": "printf finished"})], results, mode="inline", wait_seconds=5
        ).run("quick", environment=source(tmp_path), bindings=bindings(runtime))
        assert results[0]["stdout"]["text"] == "finished", results
        assert "process_id" not in results[0]
        records = await runtime.summarize(first.thread_id)
        assert len(records) == 1 and not records[0].foreground_released
    finally:
        await runtime.close()


async def start_live(runtime, root, *, environment=None):
    results = []
    first = await executable(
        [("shell_exec", {"command": "printf ready; read line; printf '%s' \"$line\""})], results
    ).run("start", environment=source(root) if environment is None else environment, bindings=bindings(runtime))
    execution_id = results[0]["execution_id"]
    async with asyncio.timeout(10):
        while runtime.resource(first.thread_id, execution_id) is None:
            record = await runtime.info(first.thread_id, execution_id)
            assert not record.terminal, record
            await asyncio.sleep(0.01)
    return first, execution_id, runtime.resource(first.thread_id, execution_id)


@pytest.mark.parametrize("wait_tool", ["shell_wait", "tool_execution_wait"])
@pytest.mark.parametrize("immediate", [False, True])
async def test_steering_releases_control_wait_without_stopping_original_shell(
    tmp_path, monkeypatch, wait_tool, immediate
):
    from a13n_harness.capabilities.steering import steering_input_ids
    from a13n_harness.toolsets.process_manager import _ProcessController

    runtime = ToolExecutionRuntime()
    waiting = asyncio.Event()
    original_wait = _ProcessController.wait if wait_tool == "shell_wait" else runtime.wait

    async def observe(*args, **kwargs):
        if kwargs.get("timeout_seconds") == 30:
            waiting.set()
        return await original_wait(*args, **kwargs)

    try:
        async with asyncio.timeout(10):
            first, execution_id, resource = await start_live(runtime, tmp_path)
            if wait_tool == "shell_wait":
                monkeypatch.setattr(_ProcessController, "wait", observe)
            else:
                monkeypatch.setattr(runtime, "wait", observe)
            arguments = {
                "process_id" if wait_tool == "shell_wait" else "execution_id": execution_id,
                "timeout_seconds": 30,
            }
            results = []
            async with executable([(wait_tool, arguments)], results, mode="inline").stream(
                "wait", previous_state=first.state, environment=source(tmp_path), bindings=bindings(runtime)
            ) as stream:

                async def consume():
                    return [event async for event in stream]

                consumer = asyncio.create_task(consume())
                try:
                    await waiting.wait()
                    await stream.steer("continue now", input_id="guidance", immediate=immediate, best_effort=True)
                    if immediate:
                        await asyncio.wait_for(asyncio.shield(consumer), timeout=2)
                        assert steering_input_ids(stream.result.state.message_history) == ("guidance",)
                        status = results[0]["status"]
                        assert (status["phase"] if wait_tool == "shell_wait" else status) == "running"
                    else:
                        assert not consumer.done()
                    assert (await runtime.info(first.thread_id, execution_id)).status == "running"
                    await resource.controller.write_input(resource.process_id, "finished\n", close_stdin=False)
                    await consumer
                    if not immediate:
                        assert results[0]["status"]["phase"] == "exited"
                        assert results[0]["stdout"]["text"] == "readyfinished"
                    settled = await runtime.wait(first.thread_id, execution_id)
                    assert settled.record.status == "succeeded"
                    assert settled.result["stdout"]["text"] == "readyfinished"
                    assert len(await runtime.summarize(first.thread_id)) == 1
                finally:
                    consumer.cancel()
                    await asyncio.gather(consumer, return_exceptions=True)
    finally:
        await runtime.close()


async def test_retained_controls_use_current_guard_and_ceiling(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from a13n_harness.environment import EnvironmentAction, EnvironmentError, EnvironmentMount, EnvironmentPermissionSet
    from a13n_harness.providers.environment.direct_local.processes import LocalProcessManager

    guarded, writes = [], []
    inflight = 0
    original_write = LocalProcessManager.write_stdin

    async def observe_write(self, handle, data, **kwargs):
        writes.append(data)
        return await original_write(self, handle, data, **kwargs)

    monkeypatch.setattr(LocalProcessManager, "write_stdin", observe_write)

    @asynccontextmanager
    async def deny_input(action):
        nonlocal inflight
        guarded.append(action)
        if action == EnvironmentAction.PROCESS_WRITE_STDIN:
            raise EnvironmentError("Input denied by current Host.", code="environment_denied")
        inflight += 1
        try:
            yield
        except EnvironmentError as exc:
            if action == EnvironmentAction.PROCESS_INSPECT or exc.details.get("dispatch_stage") == "pre_dispatch":
                inflight -= 1
            raise
        else:
            inflight -= 1

    def environment(*, denied=frozenset(), guard=None):
        return EnvironmentMount(
            source=source(tmp_path),
            permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction) - denied),
            operation_guard=guard,
            working_directory="/",
        )

    runtime = ToolExecutionRuntime()
    try:
        first, execution_id, resource = await start_live(
            runtime, tmp_path, environment=environment(denied={EnvironmentAction.PROCESS_KILL})
        )
        results = []
        await executable(
            [("shell_input", {"process_id": execution_id, "data": "forbidden\n", "close_stdin": True})], results
        ).run(
            "denied", previous_state=first.state, environment=environment(guard=deny_input), bindings=bindings(runtime)
        )
        assert results[0]["error"]["code"] == "environment_denied", results
        assert EnvironmentAction.PROCESS_WRITE_STDIN in guarded
        assert EnvironmentAction.PROCESS_CLOSE_STDIN in guarded
        assert writes == []
        assert inflight == 0
        # Run A's ceiling cannot veto a control newly authorized by Run B.
        await executable([("shell_signal", {"process_id": execution_id, "signal": "kill"})], results).run(
            "kill", previous_state=first.state, environment=environment(), bindings=bindings(runtime)
        )
        assert results[-1]["accepted"], results
        async with asyncio.timeout(10):
            final = await runtime.wait(first.thread_id, execution_id)
        assert final.record.status == "succeeded", final
        assert resource._closed and not resource.controller._entries
    finally:
        await runtime.close()


async def test_changed_target_cannot_control_retained_process(tmp_path):
    from contextlib import asynccontextmanager

    from a13n_harness.environment import EnvironmentMount

    guarded = []

    @asynccontextmanager
    async def record_admission(action):
        guarded.append(action)
        yield

    runtime = ToolExecutionRuntime()
    other = tmp_path / "other"
    other.mkdir()
    try:
        first, execution_id, _resource = await start_live(runtime, tmp_path)
        results = []
        await executable([("shell_input", {"process_id": execution_id, "data": "rejected\n"})], results).run(
            "wrong target",
            previous_state=first.state,
            environment=EnvironmentMount(source=source(other), operation_guard=record_admission, working_directory="/"),
            bindings=bindings(runtime),
        )
        assert results[0]["ok"] is False, results
        assert results[0]["error"]["code"] == "environment_stale_mount"
        assert guarded == []
        assert (await runtime.info(first.thread_id, execution_id)).status == "running"
    finally:
        await runtime.close()


async def test_retained_wait_does_not_block_input(tmp_path):
    from a13n_harness.environment import EnvironmentAction

    runtime = ToolExecutionRuntime()
    try:
        first, execution_id, resource = await start_live(runtime, tmp_path)
        # Exercise simultaneous controls on the same exact process. A wait lease
        # must not serialize the input that allows the command to terminate.
        environment = resource.controller._environment
        waiting = asyncio.Event()

        async def wait():
            async with resource.access(
                environment,
                actions=frozenset(
                    {
                        EnvironmentAction.PROCESS_WAIT,
                        EnvironmentAction.PROCESS_READ_OUTPUT,
                        EnvironmentAction.PROCESS_INSPECT,
                    }
                ),
                alias=None,
                timeout_seconds=10,
            ) as (controller, ref, processes):
                waiting.set()
                return await controller.wait(
                    ref, stdout_offset=0, stderr_offset=0, timeout_seconds=10, processes=processes
                )

        waiter = asyncio.create_task(wait())
        await waiting.wait()
        async with asyncio.timeout(2):
            async with resource.access(
                environment,
                actions=frozenset({EnvironmentAction.PROCESS_WRITE_STDIN, EnvironmentAction.PROCESS_INSPECT}),
                alias=None,
                timeout_seconds=0,
            ) as (controller, ref, processes):
                await controller.write_input(ref, "unblocked\n", close_stdin=False, processes=processes)
            observed = await waiter
        assert observed["stdout"]["text"] == "readyunblocked"
        assert (await runtime.wait(first.thread_id, execution_id)).record.status == "succeeded"
    finally:
        await runtime.close()


async def test_current_permissions_reject_input_before_any_bytes(tmp_path, monkeypatch):
    from a13n_harness.environment import EnvironmentAction, EnvironmentMount, EnvironmentPermissionSet
    from a13n_harness.environment.advanced import create_environment_runtime
    from a13n_harness.providers.environment.direct_local.processes import LocalProcessManager

    runtime = ToolExecutionRuntime()
    try:
        first, execution_id, _resource = await start_live(runtime, tmp_path)
        writes = []
        original = LocalProcessManager.write_stdin

        async def observe(self, handle, data, **kwargs):
            writes.append(data)
            return await original(self, handle, data, **kwargs)

        monkeypatch.setattr(LocalProcessManager, "write_stdin", observe)
        environment = create_environment_runtime(
            mounts={
                "local": EnvironmentMount(
                    source=source(tmp_path),
                    permission_ceiling=EnvironmentPermissionSet(
                        operations=frozenset(EnvironmentAction) - {EnvironmentAction.PROCESS_CLOSE_STDIN}
                    ),
                    working_directory="/",
                )
            },
            default_mount="local",
        )
        results = []
        await executable(
            [("shell_input", {"process_id": execution_id, "data": "forbidden\n", "close_stdin": True})], results
        ).run("denied", previous_state=first.state, bindings=bindings(runtime, environment=environment))
        assert results[0]["error"]["code"] == "environment_denied", results
        assert writes == []
    finally:
        await runtime.close()


@pytest.mark.parametrize("mode", ["inline", "auto"])
async def test_codeact_shell_calls_return_native_results_without_nested_executions(tmp_path, monkeypatch, mode):
    from a13n_harness.capabilities import CodeActCapability
    from a13n_harness.codeact.state import CodeActStateOwner
    from a13n_harness.environment.dynamic import _DynamicEnvironmentRunCapability
    from a13n_harness.toolsets.codeact import CodeActPolicyToolset, CodeActToolPolicy

    original = _DynamicEnvironmentRunCapability._toolset_for_current_environment

    def enabled(self, ctx):
        return CodeActPolicyToolset(
            wrapped=original(self, ctx),
            policy=CodeActToolPolicy(tools={name: True for name in ("shell_exec", "shell_input", "shell_wait")}),
        )

    monkeypatch.setattr(_DynamicEnvironmentRunCapability, "_toolset_for_current_environment", enabled)
    runtime = ToolExecutionRuntime()
    results = []
    try:
        first = await executable(
            [
                (
                    "run_code",
                    {
                        "code": (
                            'started = await shell_exec(command="read line; printf %s \\"$line\\"")\n'
                            'await shell_input(process_id=started["process_id"], data="nested\\n")\n'
                            'finished = await shell_wait(process_id=started["process_id"], timeout_seconds=5)\n'
                            'finished["stdout"]["text"]'
                        )
                    },
                )
            ],
            results,
            capabilities=(CodeActCapability(),),
            mode=mode,
        ).run("nested", environment=source(tmp_path), bindings=bindings(runtime, codeact_state=CodeActStateOwner()))
        records = await runtime.summarize(first.thread_id)
        if mode == "inline":
            assert results[0] == "nested"
            assert not records[0].foreground_released
        else:
            async with asyncio.timeout(10):
                settled = await runtime.wait(first.thread_id, results[0]["execution_id"])
            assert settled.record.status == "succeeded", settled
            assert settled.result.return_value == "nested", settled.result
        assert len(records) == 1 and records[0].tool_name == "run_code"
    finally:
        await runtime.close()


async def test_cancel_during_admission_starts_once_then_terminates(tmp_path, monkeypatch):
    from a13n_harness.toolsets.process_manager import _ProcessController

    admitted, publish = asyncio.Event(), asyncio.Event()
    entries = []
    original = _ProcessController._admit

    async def delayed(self, *args, **kwargs):
        entry = await original(self, *args, **kwargs)
        entries.append((self, entry))
        admitted.set()
        await publish.wait()
        return entry

    monkeypatch.setattr(_ProcessController, "_admit", delayed)
    runtime = ToolExecutionRuntime()
    results = []
    try:
        first = await executable([("shell_exec", {"command": "read line"})], results).run(
            "start", environment=source(tmp_path), bindings=bindings(runtime)
        )
        await asyncio.wait_for(admitted.wait(), 5)
        execution_id = results[0]["execution_id"]
        await runtime.cancel(first.thread_id, execution_id)
        await runtime.cancel(first.thread_id, execution_id)
        publish.set()
        async with asyncio.timeout(10):
            settled = await runtime.wait(first.thread_id, execution_id)
        assert settled.record.status == "cancelled", settled
        assert len(entries) == 1
        assert not entries[0][0]._entries
    finally:
        publish.set()
        await runtime.close()


async def test_shell_settles_only_after_output_capture_closes(tmp_path, monkeypatch):
    from a13n_harness.providers.environment.direct_local.processes import LocalProcessManager

    cleanup_started, cleanup_release = asyncio.Event(), asyncio.Event()
    original = LocalProcessManager._cleanup_group

    async def delayed_cleanup(self, pid):
        cleanup_started.set()
        await cleanup_release.wait()
        await original(self, pid)

    monkeypatch.setattr(LocalProcessManager, "_cleanup_group", delayed_cleanup)
    runtime = ToolExecutionRuntime()
    results = []
    try:
        first = await executable([("shell_exec", {"command": "sleep 30 & printf exited"})], results).run(
            "capture", environment=source(tmp_path), bindings=bindings(runtime)
        )
        await asyncio.wait_for(cleanup_started.wait(), 5)
        execution_id = results[0]["execution_id"]
        assert (await runtime.info(first.thread_id, execution_id)).status == "running"
        cleanup_release.set()
        async with asyncio.timeout(10):
            settled = await runtime.wait(first.thread_id, execution_id)
        assert settled.record.status == "succeeded", settled
        assert settled.result["stdout"]["text"] == "exited"
        assert settled.result["stdout"]["observation_closed"]
        assert settled.result["stderr"]["observation_closed"]
    finally:
        cleanup_release.set()
        await runtime.close()
