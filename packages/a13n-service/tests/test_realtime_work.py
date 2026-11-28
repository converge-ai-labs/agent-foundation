"""Realtime calls share durable tool and child execution with ordinary Service runs."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager

import pytest
from fastmcp import FastMCP
from pydantic_ai import BinaryImage
from pydantic_ai.realtime.codec import ResponseDone, ToolCall, ToolResult
from pydantic_ai.realtime.openai import OpenAIRealtimeModel
from websockets.asyncio.client import connect

from .http_support import serve_http
from .test_connections import create
from .test_connections import serve as serve_mcp
from .test_realtime import ScriptedRealtimeProvider, create_voice_agent

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def call(service, url, parent, **options):
    response = await service.client.post(f"{service.api}/realtime/sessions", json={"agent_id": parent["id"], **options})
    assert response.status_code == 201, response.text
    ticket = response.json()
    async with connect(
        url.replace("http://", "ws://") + ticket["websocket_path"],
        subprotocols=[ticket["subprotocol"], ticket["ticket"]],
    ) as socket:
        assert json.loads(await socket.recv())["type"] == "accepted"
        assert json.loads(await socket.recv())["type"] == "ready"
        yield socket, ticket
        await socket.send('{"type":"stop"}')
        frames = [json.loads(frame) async for frame in socket]
        assert frames[-1]["type"] == "sealed", frames


async def invoke(provider, name, arguments):
    await provider.connection.incoming.put(
        ToolCall("call_work", tool_name=name, args=json.dumps(arguments), response_usage_follows=True)
    )
    await provider.connection.incoming.put(ResponseDone())
    while not isinstance(result := await provider.connection.outgoing.get(), ToolResult):
        pass
    await provider.connection.incoming.put(ResponseDone())
    return result.output


async def child_notice(provider, run_id):
    while True:
        frame = await provider.connection.outgoing.get()
        if isinstance(frame, str) and "A delegated subagent run ended:" in frame:
            payload = json.loads(frame.split("A delegated subagent run ended:\n", 1)[1])
            if payload["child_run_id"] == run_id:
                return payload


async def test_retained_call_completes_between_voice_sessions(executing, runs_kit, monkeypatch):
    service = executing
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    await create_voice_agent(service, runs_kit)
    server = FastMCP("Retained work")
    first_started = asyncio.Event()
    finish = {name: asyncio.Event() for name in ("first", "third")}
    calls = []

    @server.tool
    async def slow(name: str) -> str:
        calls.append(name)
        if name == "first":
            first_started.set()
        await finish[name].wait()
        return "completed between conversations"

    async with serve_mcp(server.http_app(path="/mcp")) as mcp, serve_http(service.app, lifespan="off") as url:
        connection = await create(service, {"config": {"url": mcp + "/mcp"}})
        parent = await runs_kit.add_agent(
            service,
            "Voice worker",
            "voice",
            connection_tools=[{"connection_id": connection["id"], "permission": "allow"}],
            execution={"tools": {"mode": "auto", "foreground_wait_seconds": 0.05}},
        )
        async with asyncio.timeout(45):
            try:
                async with call(service, url, parent) as (_, first):
                    receipt = await invoke(provider, "slow", {"name": "first"})
                    await first_started.wait()
                    (record,) = await service.runtime.executions.summarize(first["thread_id"])
                    assert record.status == "running" and record.execution_id in str(receipt)
            finally:
                finish["first"].set()
            await service.runtime.executions.join()
            while not any(row["kind"] == "tool_result" for row in await runs_kit.inbox(service, first["thread_id"])):
                await asyncio.sleep(0.05)
            image = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV0sAAAAASUVORK5CYII="
            )
            staged = await service.client.post(
                f"{service.api}/uploads",
                files={"file": ("notice.png", image, "image/png")},
                headers=runs_kit.fresh_key(),
            )
            assert staged.status_code == 200, staged.text
            asset = await service.client.post(
                f"{service.api}/assets",
                json={"upload_id": staged.json()["upload_id"], "name": "notice.png"},
            )
            assert asset.status_code == 201, asset.text
            async with call(
                service,
                url,
                parent,
                thread_id=first["thread_id"],
                draft="Consider this image and the result",
                draft_asset_ids=[asset.json()["id"]],
            ) as (_, second):
                attachment = await provider.connection.outgoing.get()
                assert isinstance(attachment, BinaryImage) and attachment.data == image
                while True:
                    frame = await provider.connection.outgoing.get()
                    if isinstance(frame, str) and "A retained tool execution ended." in frame:
                        assert record.execution_id in frame and "Consider this image and the result" in frame
                        break
            notices = [row for row in await runs_kit.inbox(service, first["thread_id"]) if row["kind"] == "tool_result"]
            assert len(notices) == 1
            assert notices[0]["status"] == "consumed" and notices[0]["assigned_run_id"] == second["run_id"]
            # A new completion exercises the third call's live mailbox after its saved history was restored.
            try:
                async with call(service, url, parent, thread_id=first["thread_id"]):
                    assert "A retained tool execution ended." in str(provider.messages)
                    assert record.execution_id in str(provider.messages)
                    await provider.connection.incoming.put(
                        ToolCall("third_work", tool_name="slow", args='{"name":"third"}', response_usage_follows=True)
                    )
                    await provider.connection.incoming.put(ResponseDone())
                    while not isinstance(frame := await provider.connection.outgoing.get(), ToolResult):
                        assert not isinstance(frame, str) or record.execution_id not in frame
                    records = await service.runtime.executions.summarize(first["thread_id"])
                    latest = next(item for item in records if item.execution_id != record.execution_id)
                    assert latest.execution_id in frame.output
                    await provider.connection.incoming.put(ResponseDone())
                    finish["third"].set()
                    while True:
                        frame = await provider.connection.outgoing.get()
                        if isinstance(frame, str):
                            assert record.execution_id not in frame
                            if latest.execution_id in frame:
                                break
            finally:
                finish["third"].set()
            while not provider.connection.outgoing.empty():
                frame = provider.connection.outgoing.get_nowait()
                assert not isinstance(frame, str) or record.execution_id not in frame
            assert calls == ["first", "third"]


@pytest.mark.parametrize("permission", ["allow", "ask"], ids=["budget-exhaustion", "approval-resume"])
async def test_voice_spawn_uses_text_child_approval_and_budget(
    executing, runs_kit, scripted_model, monkeypatch, permission
):
    service = executing
    provider = ScriptedRealtimeProvider()
    monkeypatch.setattr(OpenAIRealtimeModel, "connect", provider.connect)
    await create_voice_agent(service, runs_kit)
    budget = 3
    text = await runs_kit.create_model(service, scripted_model)
    server = FastMCP("Child approval")
    calls = []

    @server.tool
    async def review() -> str:
        calls.append("review")
        return "reviewed"

    async with serve_mcp(server.http_app(path="/mcp")) as mcp, serve_http(service.app, lifespan="off") as url:
        connection = await create(service, {"config": {"url": mcp + "/mcp"}})
        parent = await runs_kit.add_agent(
            service,
            "Voice parent",
            "voice",
            subagents={"mode": "async", "dynamic": {"enabled": True, "allowed_models": [text], "default_model": text}},
            connection_tools=[{"connection_id": connection["id"], "permission": permission}],
        )
        if permission == "ask":
            scripted_model.call("review", {}, call_id="child_call")
        else:
            for index in range(budget):
                scripted_model.call("review", {}, call_id=f"child_{index}")
        async with asyncio.timeout(45), call(service, url, parent, max_usage={"requests": budget}):
            receipt = await invoke(
                provider, "spawn_subagent", {"name": "worker", "instructions": "Review.", "prompt": "Work"}
            )
            child_id = json.loads(receipt)["execution_id"]
            while (child := await runs_kit.get_run(service, child_id))["status"] not in {"waiting", "failed"}:
                await asyncio.sleep(0.05)
            assert child["options"]["max_usage"] == {"requests": budget}
            if permission == "allow":
                assert child["failure"]["code"] == "usage_limit_exceeded", child
                assert scripted_model.requests.qsize() == budget
                notice = await child_notice(provider, child_id)
                assert notice["status"] == "failed" and notice["failure"]["code"] == "usage_limit_exceeded"
                return
            assert child["status"] == "waiting" and calls == []
            assert [item["tool_call_id"] for item in child["pending"]["approvals"]] == ["child_call"]
            scripted_model.say("Reviewed")
            response = await service.client.post(
                f"{service.api}/runs/{child_id}/resume",
                json={"approvals": {"child_call": {"action": "approve"}}, "calls": {}},
                headers=runs_kit.fresh_key(),
            )
            assert response.status_code == 201, response.text
            successor = response.json()["id"]
            while (run := await runs_kit.get_run(service, successor))["status"] not in {"completed", "failed"}:
                await asyncio.sleep(0.05)
            assert run["status"] == "completed" and calls == ["review"]
            notice = await child_notice(provider, successor)
            assert notice["status"] == "completed" and "Reviewed" in str(notice["output"])
