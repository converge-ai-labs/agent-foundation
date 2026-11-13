"""Service startup policy preserves Attempt ownership and Host preparation failures."""

import json

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from a13n_harness.mcp import InitializationRetryClient
from a13n_service.providers.tools.mcp import McpConfig, mcp_capability, mcp_client
from a13n_service.resources.connections.schemas import ConnectionSelection
from mcp import MCPError
from pydantic_ai.models.function import FunctionModel
from starlette.responses import JSONResponse

from .test_connections import create, opened, remote, run_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def backoff(monkeypatch):
    delays = []

    async def sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("a13n_harness.mcp.sleep", sleep)
    return delays


@pytest.mark.parametrize("apps", [False, True])
async def test_service_client_retries_only_initialization(apps, backoff):
    attempts = 0
    business = 0

    async def peer(request):
        nonlocal attempts, business
        body = json.loads(request.content)
        if body["method"] == "server/discover":
            attempts += 1
            if attempts < 3:
                raise httpx2.ConnectError("temporarily unavailable", request=request)
            result = {
                "resultType": "complete",
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}},
                "cacheScope": "private",
                "ttlMs": 0,
            }
        elif body["method"] == "tools/call":
            business += 1
            return httpx2.Response(503, text="lost business response")
        else:
            raise AssertionError(body["method"])
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(peer)) as http:
        client = mcp_client("https://mcp.example.test/mcp", "con_one", http, timeout=5, apps=apps)
        assert isinstance(client, InitializationRetryClient)
        async with client:
            # Direct SDK calls still need the Service's dispatch identity.
            from a13n_service.providers.tools import ToolDispatch
            from a13n_service.providers.tools.mcp import _dispatch

            async def check(dispatch):
                pass

            token = _dispatch.set((ToolDispatch("con_one", None, "change", "call_one"), check))
            try:
                with pytest.raises(MCPError, match="Server returned an error response"):
                    await client.call_tool("change", {})
            finally:
                _dispatch.reset(token)
    assert attempts == 3 and backoff == [1, 2]
    assert business == 1


@pytest.mark.parametrize("deferred", [False, True])
async def test_skip_is_run_local_with_healthy_tools_and_later_recovery(service, backoff, deferred):
    async with remote() as failed, remote() as healthy:
        broken = await create(service, {"config": {"url": failed.url + "/mcp", "on_error": "skip"}})
        ready = await create(service, {"config": {"url": healthy.url + "/mcp"}})
        original = failed.app
        unavailable = True
        refusals = 0

        async def endpoint(scope, receive, send):
            nonlocal refusals
            if unavailable and scope["type"] == "http" and scope["method"] == "POST":
                refusals += 1
                await JSONResponse({"error": "unavailable"}, status_code=503)(scope, receive, send)
                return
            await original(scope, receive, send)

        failed.app = endpoint
        selections = [ConnectionSelection(connection_id=item["id"]) for item in (broken, ready)]
        async with opened(service, selections, {}, []) as capabilities:

            async def model(messages, info):
                text = str(messages)
                assert broken["id"] in text and "omitted for this Run" in text
                names = {tool.name for tool in info.function_tools}
                assert ("call_tool" in names) if deferred else ("echo" in names)
                yield "continued"

            features = (
                (
                    ToolSearchCapability(
                        groups={
                            "broken": ToolSearchGroup(capabilities[0], "Broken"),
                            "healthy": ToolSearchGroup(capabilities[1], "Healthy"),
                        }
                    ),
                )
                if deferred
                else capabilities
            )
            executable = HarnessBuilder().build(
                AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=features
            )
            result = await executable.run("continue", bindings=RunBindings.embedded())
            assert result.output_or_raise() == "continued"
            assert refusals == 3 and backoff == [1, 2]
            unavailable = False
            seen = await run_tool((capabilities[0],), "echo", {"text": "recovered"})
            assert "recovered" in str(seen["results"])
        assert failed.echoed == ["recovered"]


async def test_default_raise_and_preparation_failure_are_not_skipped(backoff):
    assert McpConfig(url="https://mcp.example.test/mcp").on_error == "raise"

    async def prepare():
        raise PermissionError("Host credential binding failed")

    async def check(dispatch):
        pass

    capability = mcp_capability("con_one", prepare, tools=None, check=check, on_error="skip")

    async def model(messages, info):
        raise AssertionError("Preparation must fail before the model runs")
        yield "unreachable"

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(capability,)
    )
    with pytest.raises(PermissionError, match="Host credential"):
        await executable.run("continue", bindings=RunBindings.embedded())
    assert backoff == []


@pytest.mark.parametrize("on_error", [None, "skip"])
async def test_worker_startup_policy_preserves_attempt_recovery(service, scripted_model, runs_kit, backoff, on_error):
    from a13n_service.infra.db import transaction
    from a13n_service.runs.tables import RunRow
    from sqlalchemy import update

    await runs_kit.pause_sweeps(service)
    async with remote() as failed, remote() as healthy:
        config = {"url": failed.url + "/mcp", "tools": ["ping"]}
        if on_error is not None:
            config["on_error"] = on_error
        broken = await create(service, {"config": config})
        ready = await create(service, {"config": {"url": healthy.url + "/mcp", "tools": ["echo"]}})
        original = failed.app
        unavailable = True
        refusals = 0

        async def endpoint(scope, receive, send):
            nonlocal refusals
            if unavailable and scope["type"] == "http" and scope["method"] == "POST":
                refusals += 1
                await JSONResponse({"error": "unavailable"}, status_code=503)(scope, receive, send)
                return
            await original(scope, receive, send)

        failed.app = endpoint
        agent = await runs_kit.create_agent(
            service,
            scripted_model,
            connection_tools=[{"connection_id": item["id"], "permission": "allow"} for item in (broken, ready)],
            toolsets={"files": {"enabled": False}, "shell": {"enabled": False}},
        )
        submitted = await runs_kit.start_thread(service, agent, "Continue with healthy tools")
        run_id = submitted["run"]["id"]
        scripted_model.call("echo", {"text": "healthy"}, call_id="call_healthy")
        scripted_model.say("Finished")
        await (await runs_kit.attempt(service))
        assert refusals == 3 and backoff == [1, 2]
        first = await runs_kit.get_run(service, run_id)
        if on_error is None:
            assert first["status"] == "accepted" and first["attempts"] == 1
            assert scripted_model.requests.empty() and healthy.echoed == []
            unavailable = False
            async with transaction(service.runtime.storage) as session:
                await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))
            await (await runs_kit.attempt(service))
        else:
            request = await scripted_model.request()
            assert broken["id"] in str(request["messages"]) and "omitted for this Run" in str(request["messages"])
        final = await runs_kit.get_run(service, run_id)
        assert final["status"] == "completed" and final["output"] == "Finished"
        assert final["attempts"] == (2 if on_error is None else 1)
        assert healthy.echoed == ["healthy"]


async def test_ready_disconnection_does_not_reopen_attempt_client(service, monkeypatch):
    import a13n_service.resources.connections.runtime as runtime

    clients = []
    original = runtime.mcp_client

    def capture(*args, **kwargs):
        client = original(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(runtime, "mcp_client", capture)
    async with remote() as server:
        connection = await create(service, {"config": {"url": server.url + "/mcp"}})
        async with opened(service, [ConnectionSelection(connection_id=connection["id"])], {}, []) as capabilities:
            await run_tool(capabilities, "echo", {"text": "once"})
            await clients[0].close()
            with pytest.raises(RuntimeError, match="disconnected"):
                await run_tool(capabilities, "echo", {"text": "not sent"})
            assert len(clients) == 1 and not clients[0].is_connected()
        assert server.echoed == ["once"]


@pytest.mark.parametrize("ready", [False, True])
async def test_each_logical_run_prepares_attempt_credentials_before_isolation(service, monkeypatch, ready):
    from a13n_service.resources.connections.oauth import McpAuthentication
    from fastmcp import Client

    prepare = McpAuthentication.prepare
    negotiate = Client._negotiate
    preparations = 0

    async def checked_prepare(self):
        nonlocal preparations
        preparations += 1
        if preparations == 2:
            raise PermissionError("credential renewal failed")
        await prepare(self)

    async def startup(self, *args, **kwargs):
        if not ready:
            raise ValueError("server unavailable")
        return await negotiate(self, *args, **kwargs)

    monkeypatch.setattr(McpAuthentication, "prepare", checked_prepare)
    monkeypatch.setattr(Client, "_negotiate", startup)
    async with remote() as server:
        connection = await create(service, {"config": {"url": server.url + "/mcp", "on_error": "skip"}})
        async with opened(service, [ConnectionSelection(connection_id=connection["id"])], {}, []) as capabilities:
            calls = 0

            async def model(messages, info):
                nonlocal calls
                calls += 1
                yield "continued"

            executable = HarnessBuilder().build(
                AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=capabilities
            )
            assert (await executable.run("first", bindings=RunBindings.embedded())).output_or_raise() == "continued"
            with pytest.raises(PermissionError, match="credential renewal failed"):
                await executable.run("second", bindings=RunBindings.embedded())
            assert preparations == 2 and calls == 1
