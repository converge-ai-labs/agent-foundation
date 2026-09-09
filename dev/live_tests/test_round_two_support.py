"""Guard fault isolation and fixture wire behavior without starting Foundation."""

import asyncio
import json
import signal
from contextlib import asynccontextmanager

import httpx2
import pytest
from fastapi import FastAPI

from .fixture_model import fixture_router
from .host import bearer_authenticator
from .round_two_lab import RoundTwoLab
from .round_two_resources import agent_config
from .tcp_proxy import TCPProxy


@asynccontextmanager
async def fixture_http(tmp_path):
    app = FastAPI()

    async def authenticated():
        return None

    app.include_router(fixture_router(tmp_path, authenticated))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://fixture") as client:
        yield client


@pytest.mark.anyio
async def test_fault_proxy_cuts_existing_and_new_connections_and_restores():
    async def echo(reader, writer):
        try:
            while data := await reader.read(1024):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    try:
        async with TCPProxy("127.0.0.1", server.sockets[0].getsockname()[1]).listen() as proxy:
            async with asyncio.timeout(5):
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.local_port)
                writer.write(b"before")
                assert await reader.readexactly(6) == b"before"
                proxy.cut()
                assert await reader.read() == b""
                writer.close()
                await writer.wait_closed()
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.local_port)
                assert await reader.read() == b""
                writer.close()
                await writer.wait_closed()
                assert proxy.rejected >= 1
                proxy.restore()
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.local_port)
                writer.write(b"after")
                assert await reader.readexactly(5) == b"after"
                writer.close()
                await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


def test_fault_controls_reject_unowned_process_and_remote_upstream(tmp_path):
    with pytest.raises(ValueError, match="not created"):
        RoundTwoLab(tmp_path, {}, {}).send(object(), signal.SIGKILL)
    with pytest.raises(ValueError, match="loopback"):
        TCPProxy("example.com", 5432)


@pytest.mark.anyio
async def test_fixture_error_repair_and_missing_tool_are_observable(tmp_path):
    case = {"case_id": "a" * 32, "scenario": "model_error", "token": "b" * 32}
    async with fixture_http(tmp_path) as client:
        assert (await client.put("/__live__/cases/" + case["case_id"], json=case)).status_code == 200
        request = {"stream": True, "messages": [{"role": "user", "content": "LIVE_TEST " + json.dumps(case)}]}
        failure = await client.post("/__live__/model/v1/chat/completions", json=request)
        assert failure.status_code == 401
        evidence = (await client.get("/__live__/cases/" + case["case_id"])).json()
        assert evidence["injected_errors"] == 1 and evidence["effects"] == 0
        await client.post("/__live__/cases/" + case["case_id"] + "/release")
        assert (await client.post("/__live__/model/v1/chat/completions", json=request)).status_code == 200
        tool_case = {**case, "case_id": "c" * 32, "scenario": "checkpoint"}
        await client.put("/__live__/cases/" + tool_case["case_id"], json=tool_case)
        request["messages"][0]["content"] = "LIVE_TEST " + json.dumps(tool_case)
        missing = await client.post("/__live__/model/v1/chat/completions", json=request)
        assert missing.status_code == 400 and "live_effect" in missing.text


@pytest.mark.anyio
async def test_checkpoint_fixture_waits_for_release_after_observing_tool_result(tmp_path):
    case = {"case_id": "d" * 32, "scenario": "checkpoint", "token": "e" * 32}
    messages = [{"role": "user", "content": "LIVE_TEST " + json.dumps(case)}]
    async with fixture_http(tmp_path) as client:
        await client.put("/__live__/cases/" + case["case_id"], json=case)
        response = await client.post(
            "/__live__/model/v1/chat/completions",
            json={"stream": True, "messages": messages, "tools": [{"function": {"name": "live_effect"}}]},
        )
        chunks = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ") and "[DONE]" not in line
        ]
        call = next(
            chunk["choices"][0]["delta"]["tool_calls"][0]
            for chunk in chunks
            if chunk["choices"] and "tool_calls" in chunk["choices"][0]["delta"]
        )
        assert json.loads(call["function"]["arguments"]) == {
            "case_id": case["case_id"],
            "token": case["token"],
            "fail": False,
        }
        messages.append({"role": "tool", "content": case["token"], "tool_call_id": call["id"]})
        pending = asyncio.create_task(
            client.post("/__live__/model/v1/chat/completions", json={"stream": True, "messages": messages})
        )
        try:
            async with asyncio.timeout(5):
                while not (await client.get("/__live__/cases/" + case["case_id"])).json()["checkpoint_ready"]:
                    await asyncio.sleep(0.01)
            assert not pending.done(), "Checkpoint barrier did not hold the model request"
            await client.post("/__live__/cases/" + case["case_id"] + "/release")
            assert (await pending).status_code == 200
            assert (await client.get("/__live__/cases/" + case["case_id"])).json()["checkpoint_requests"] == 1
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.anyio
async def test_second_identity_cannot_select_another_workspace_by_header():
    from starlette.requests import Request

    first = {"token": "first", "user_id": "usr_" + "a" * 24, "workspace_id": "ws_" + "a" * 24}
    second = {"token": "second", "user_id": "usr_" + "b" * 24, "workspace_id": "ws_" + "b" * 24}
    authenticate = bearer_authenticator({**first, "other_identity": second})
    actor = await authenticate(
        Request({"type": "http", "headers": [(b"authorization", b"Bearer second"), (b"x-workspace-id", b"ws_first")]})
    )
    assert actor.workspace_id == second["workspace_id"] and actor.principal.principal_id == second["user_id"]
    with pytest.raises(ValueError, match="distinct"):
        bearer_authenticator({**first, "other_identity": {**second, "token": "first"}})


def test_round_two_agent_definitions_match_the_service_contract():
    from a13n_service.agents.domain import AgentConfig

    for definition in (
        agent_config(),
        agent_config("live-timeout"),
        agent_config(retries={"tools": 1, "output": 1}),
        agent_config(subagent_mode="async", subagents={"child": {"agent_id": "ap_" + "a" * 24}}),
    ):
        AgentConfig.model_validate(definition)
