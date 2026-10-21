"""Real App steering uses best effort without a user configuration switch."""

import asyncio

import pytest
from a13n_harness import HarnessRunStream
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import AgentReconstructor
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import RootOperationStatus
from pydantic_ai import Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("phase", ["startup", "tool"])
async def test_one_steer_request_waits_and_delivers_once(tmp_path, monkeypatch, phase):
    path = _write_configuration(tmp_path)
    entered, release, waiting, published = (asyncio.Event() for _ in range(4))
    calls, requests, streams = [], [], []
    reconstruct = AgentReconstructor.reconstruct

    async def slow():
        calls.append("slow")
        entered.set()
        await release.wait()
        return 42

    def with_tool(self, reference, **kwargs):
        capabilities = kwargs.pop("root_capabilities", ())
        return reconstruct(self, reference, root_capabilities=(*capabilities, Capability(tools=[Tool(slow)])), **kwargs)

    monkeypatch.setattr(AgentReconstructor, "reconstruct", with_tool)

    async def model(messages, info):
        requests.append(messages)
        prompts = [part.content for message in messages for part in message.parts if isinstance(part, UserPromptPart)]
        if "change course" in prompts:
            assert prompts.count("change course") == 1
            if phase == "tool":
                results = [
                    part.content
                    for message in messages
                    for part in message.parts
                    if isinstance(part, ToolReturnPart) and part.tool_call_id == "slow-call"
                ]
                assert results == [42]
            yield "changed"
        elif phase == "tool" and len(requests) == 1:
            yield {0: DeltaToolCall(name="slow", json_args="{}", tool_call_id="slow-call")}
        else:
            yield "working"
            await asyncio.Event().wait()

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    original_wait = HarnessRunStream._wait_for_steering

    async def observe_wait(self, ready):
        waiting.set()
        await original_wait(self, ready)

    monkeypatch.setattr(HarnessRunStream, "_wait_for_steering", observe_wait)
    async with asyncio.timeout(10), open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        running = app._root_runs._running

        async def publish(receipt_id, stream, input_files=None):
            # Publishing before __aenter__ used to expose an unavailable context.
            assert stream.context.environment is not None
            streams.append(stream)
            await running(receipt_id, stream, input_files)
            published.set()
            if phase == "startup":
                await release.wait()

        monkeypatch.setattr(app._root_runs, "_running", publish)
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        await published.wait()
        if phase == "tool":
            await entered.wait()
        steering = asyncio.create_task(app.steer_root_operation(receipt_id=receipt.receipt_id, message="change course"))
        if phase == "startup":
            await waiting.wait()
            assert not steering.done()
            assert not streams[0].context._steering._pending_requests
            release.set()
        result = await steering
        assert result.accepted and result.enqueue_id
        if phase == "tool":
            assert not waiting.is_set()
            assert len(streams[0].context._steering._pending_requests) == 1
            assert not streams[0].context._tool_executions.handoff_requested.is_set()
            release.set()
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed
        assert calls == (["slow"] if phase == "tool" else [])
        assert streams[0].result.output == "changed"
