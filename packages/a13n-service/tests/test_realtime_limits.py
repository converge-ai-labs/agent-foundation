"""Realtime termination policy at the signaling, runtime and usage boundaries."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.live import HarnessLiveStream
from a13n_service.distribution import OSS
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.execute import _realtime_activity, _watch_realtime_limits
from a13n_service.runs.realtime import Channel
from a13n_service.runs.tables import RunRow
from a13n_service.runs.usage import totals
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    RealtimeInputSpeechStartEvent,
    RealtimeOutputSpeechStartEvent,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.realtime.codec import OutputTranscript, ResponseDone, SessionUsage
from pydantic_ai.realtime.openai import OpenAIRealtimeModel
from pydantic_ai.usage import RequestUsage
from websockets.asyncio.client import connect

from .http_support import serve_http
from .test_realtime import OPENAI_AUDIO_OFFER, ScriptedRealtimeProvider, ScriptedWebRTCProvider, create_voice_agent

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def _call(service, voice, **options):
    response = await service.client.post(f"{service.api}/realtime/sessions", json={"agent_id": voice["id"], **options})
    assert response.status_code == 201, response.text
    ticket = response.json()
    async with serve_http(service.app, lifespan="off") as url, asyncio.timeout(15):
        async with connect(
            url.replace("http://", "ws://", 1) + ticket["websocket_path"],
            subprotocols=[ticket["subprotocol"], ticket["ticket"]],
        ) as socket:
            if options.get("transport") == "webrtc":
                await socket.send(json.dumps({"type": "offer", "sdp": OPENAI_AUDIO_OFFER}))
            assert json.loads(await socket.recv())["type"] == "accepted"
            if options.get("transport") == "webrtc":
                assert json.loads(await socket.recv())["type"] == "answer"
            yield socket, ticket


async def test_ticket_rejects_duration_over_worker_ceiling(service, runs_kit):
    voice = await create_voice_agent(service, runs_kit)
    response = await service.client.post(
        f"{service.api}/realtime/sessions",
        json={
            "agent_id": voice["id"],
            "max_duration_seconds": service.runtime.settings.worker.realtime_max_duration_seconds + 1,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"]["field"] == "max_duration_seconds"


async def test_duration_warns_once_then_seals_with_checkpoint(executing, runs_kit, monkeypatch):
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", ScriptedRealtimeProvider().connect)
    metrics = Mock()
    monkeypatch.setattr("a13n_service.runs.realtime.CALLS_ENDED", metrics)
    execution = import_module("a13n_service.runs.execute")
    sending = asyncio.Event()
    send_cancelled = asyncio.Event()
    native_closed = asyncio.Event()
    original_close = HarnessLiveStream.close

    async def send(stream, text, *, respond):
        assert text == "Hello" and respond
        sending.set()
        try:
            await native_closed.wait()
        except asyncio.CancelledError:
            send_cancelled.set()
            raise
        raise AssertionError("The in-flight input outlived native close")

    async def close(stream):
        native_closed.set()
        await original_close(stream)

    async def near_expiry(channel, started, duration, idle):
        assert duration == 90
        await sending.wait()
        await _watch_realtime_limits(channel, asyncio.get_running_loop().time() - duration + 0.05, duration, idle)

    monkeypatch.setattr(HarnessLiveStream, "send", send)
    monkeypatch.setattr(HarnessLiveStream, "close", close)
    monkeypatch.setattr(execution, "_watch_realtime_limits", near_expiry)
    voice = await create_voice_agent(executing, runs_kit)
    async with _call(executing, voice, max_duration_seconds=90) as (socket, ticket):
        assert ticket["max_duration_seconds"] == 90
        assert json.loads(await socket.recv())["type"] == "ready"
        await socket.send('{"type":"text","text":"Hello"}')
        frames = [json.loads(frame) async for frame in socket]
    assert send_cancelled.is_set() and native_closed.is_set()
    assert frames == [
        {"type": "limit_warning", "reason": "duration_limit", "remaining_seconds": 60},
        {"type": "ending", "reason": "duration_limit"},
        {"type": "sealed", "status": "completed", "reason": "duration_limit", "run_id": ticket["run_id"]},
    ]
    run = await runs_kit.get_run(executing, ticket["run_id"])
    assert run["status"] == "completed"
    async with short_session(executing.runtime.storage) as session:
        assert (await session.get_one(RunRow, run["id"])).checkpoint is not None
    metrics.add.assert_called_once_with(1, {"transport": "websocket", "reason": "limit_exceeded"})


@pytest.mark.parametrize("transport", ["webrtc", "websocket"])
async def test_semantic_idle_finishes_both_transports(settings, serve, runs_kit, monkeypatch, transport):
    provider = ScriptedWebRTCProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    monkeypatch.setattr(OpenAIRealtimeModel, "answer_webrtc_offer", provider.answer)
    monkeypatch.setattr(OpenAIRealtimeModel, "connect_webrtc", provider.attach)
    monkeypatch.setattr(OpenAIRealtimeModel, "hang_up", provider.hang_up)
    worker = settings.worker.model_copy(update={"realtime_idle_seconds": 1, "scan_seconds": 0.05})
    async with serve(role="all", settings=settings.model_copy(update={"worker": worker})) as service:
        voice = await create_voice_agent(service, runs_kit)
        async with _call(service, voice, transport=transport) as (socket, ticket):
            assert json.loads(await socket.recv())["type"] == "ready"
            if transport == "websocket":
                # Raw PCM transport traffic does not count as speech.
                await socket.send(b"\x00\x00" * 480)
                await provider.connection.outgoing.get()
            assert json.loads(await socket.recv()) == {"type": "ending", "reason": "idle_timeout"}
            assert json.loads(await socket.recv()) == {
                "type": "sealed",
                "reason": "idle_timeout",
                "status": "completed",
                "run_id": ticket["run_id"],
            }
        assert provider.hangups == int(transport == "webrtc")


@pytest.mark.parametrize("protected", ["tool", "approval"])
async def test_idle_exempts_foreground_tool_and_pending_approval(protected):
    channel = Channel("principal", "workspace")
    loop = asyncio.get_running_loop()
    approval = None
    if protected == "tool":
        _realtime_activity(channel, FunctionToolCallEvent(ToolCallPart("lookup", tool_call_id="call-id")))
    else:
        approval = asyncio.create_task(channel.approve("call-id", "lookup", "{}"))
        await asyncio.sleep(0)
        assert channel.outgoing.get_nowait()["type"] == "approval_required"
    channel.last_activity = loop.time() - 10
    watcher = asyncio.create_task(_watch_realtime_limits(channel, loop.time(), 30, 1))
    try:
        await asyncio.sleep(0)
        assert not channel.closed.is_set()
        if approval is None:
            _realtime_activity(
                channel, FunctionToolResultEvent(ToolReturnPart("lookup", "done", tool_call_id="call-id"))
            )
        else:
            channel.approvals["call-id"].set_result(False)
            assert await approval is False
        channel.last_activity = loop.time() - 10
        channel.activity.set()
        await asyncio.wait_for(watcher, 1)
        assert channel.outgoing.get_nowait() == {"type": "ending", "reason": "idle_timeout"}
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        if approval is not None and not approval.done():
            approval.cancel()
            await asyncio.gather(approval, return_exceptions=True)


@pytest.mark.parametrize("event", [RealtimeInputSpeechStartEvent(), RealtimeOutputSpeechStartEvent()])
async def test_provider_speech_onset_is_semantic_activity(event):
    channel = Channel("principal", "workspace")
    _realtime_activity(channel, event)
    assert channel.activity.is_set() and channel.last_activity > 0


@pytest.mark.parametrize("concurrent_stop", [False, True])
async def test_soft_budget_reads_charged_usage_and_seals_failed(
    settings, serve, runs_kit, monkeypatch, concurrent_stop
):
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    reached = asyncio.Event()
    refuse = asyncio.Event()

    async def check(session, context):
        assert (await totals(session, context.run_id))["output_tokens"] == 7
        reached.set()
        await refuse.wait()
        raise ServiceError("forbidden", "Budget exhausted")

    worker_closed = asyncio.Event()
    execution = import_module("a13n_service.runs.execute")
    end = execution._Attempt._close_realtime

    async def close(attempt, stream, channel, receiving):
        await end(attempt, stream, channel, receiving)
        worker_closed.set()

    monkeypatch.setattr(execution._Attempt, "_close_realtime", close)
    policy = SimpleNamespace(
        accept=AsyncMock(),
        proceed=AsyncMock(),
        requires_per_dispatch_reservation=False,
        check_realtime_budget=check,
    )
    worker = settings.worker.model_copy(update={"scan_seconds": 0.05})
    async with serve(
        role="all", distribution=replace(OSS, admission=policy), settings=settings.model_copy(update={"worker": worker})
    ) as service:
        voice = await create_voice_agent(service, runs_kit)
        async with _call(service, voice) as (socket, ticket):
            assert json.loads(await socket.recv())["type"] == "ready"
            await socket.send('{"type":"text","text":"Hello"}')
            assert await provider.connection.outgoing.get() == "Hello"
            for event in (
                OutputTranscript("Reply", is_final=True),
                SessionUsage(RequestUsage(output_tokens=7)),
                ResponseDone(),
            ):
                await provider.connection.incoming.put(event)
            await reached.wait()
            if concurrent_stop:
                await socket.send('{"type":"stop"}')
                await worker_closed.wait()
            refuse.set()
            frames = [json.loads(frame) async for frame in socket]
        if not concurrent_stop:
            assert {"type": "ending", "reason": "usage_limit_exceeded"} in frames
        assert frames[-2]["type"] == "error" and frames[-2]["code"] == "usage_limit_exceeded"
        assert frames[-1] == {
            "type": "sealed",
            "status": "failed",
            "reason": "usage_limit_exceeded",
            "run_id": ticket["run_id"],
        }
        run = await runs_kit.get_run(service, ticket["run_id"])
        assert run["status"] == "failed" and run["failure"]["code"] == "usage_limit_exceeded"
