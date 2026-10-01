from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_apps.connections import Connections as AppConnections
from a13n_harness_ui.mcp_runtime.connections import Connections, HostClient, Operation, operation_context
from a13n_harness_ui.mcp_runtime.inputs import Inputs, McpInputResponse
from a13n_harness_ui.mcp_runtime.projection import HostToolset
from fastmcp.client.transports import StdioTransport

pytestmark = pytest.mark.anyio

_SERVER = """
from fastmcp import FastMCP, Context
from mcp.types import InputRequiredResult, ElicitRequest, ElicitRequestFormParams
server = FastMCP("Input fixture")
count = 0
@server.tool
async def confirm(ctx: Context) -> InputRequiredResult | dict:
    global count
    if ctx.input_responses is None:
        return InputRequiredResult(input_requests={"confirm": ElicitRequest(params=ElicitRequestFormParams(
            message="Confirm this operation", requested_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        ))}, request_state="round-one")
    response = ctx.input_responses["confirm"]
    if isinstance(response, dict):
        response = response.get("result", response)
    else:
        response = response.model_dump()
    count += 1
    return {"count": count, "state": ctx.request_state, "response": response}
@server.tool
async def legacy_confirm(ctx: Context) -> str:
    answer = await ctx.elicit("Your name", str)
    return answer.action
@server.tool
async def state_only(ctx: Context) -> InputRequiredResult | str:
    global count
    if ctx.request_state is None:
        return InputRequiredResult(request_state="continue")
    count += 1
    return "completed"
@server.tool
def counter() -> int:
    global count
    count += 1
    return count
@server.resource("data://confirmation")
async def confirmation_resource(ctx: Context) -> str:
    answer = await ctx.elicit("Confirm resource", str)
    return answer.action
@server.prompt
async def confirmation_prompt(ctx: Context) -> str:
    answer = await ctx.elicit("Confirm prompt", str)
    return answer.action
server.run(transport="stdio", show_banner=False)
"""


def _transport(tmp_path: Path) -> StdioTransport:
    path = tmp_path / "input_server.py"
    path.write_text(_SERVER)
    return StdioTransport(command=sys.executable, args=[str(path)], keep_alive=False)


async def _pending(inputs: Inputs):
    async with asyncio.timeout(10):
        while True:
            pending = [item for item in inputs.requests({"thread-one"}) if item.state == "pending"]
            if pending:
                return pending[0]
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
async def test_retained_projection_does_not_own_client(tmp_path: Path, protocol: str) -> None:
    connections = Connections()
    try:
        connection = await connections.acquire(
            "thread-one", "counter", "recipe", _transport(tmp_path), protocol=protocol
        )
        for expected in (1, 2):
            async with HostToolset(connection) as toolset:
                assert await toolset.direct_call_tool("counter", {}) == expected
            assert connection.connected
        await connections.close_integration("thread-one", "counter")
        assert not connection.connected
        with pytest.raises(RuntimeError, match="explicit activation"):
            await connections.acquire("thread-one", "counter", "recipe", _transport(tmp_path))
        replacement = await connections.acquire("thread-one", "counter", "recipe", _transport(tmp_path), activate=True)
        assert replacement.generation != connection.generation
    finally:
        await connections.close()


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
async def test_input_answers_active_operation_without_business_lane_lock(tmp_path: Path, protocol: str) -> None:
    async def changed(thread_id: str) -> None:
        assert thread_id == "thread-one"

    inputs = Inputs(changed)
    connections = Connections(input_handler=inputs)
    try:
        connection = await connections.acquire("thread-one", "input", "recipe", _transport(tmp_path), protocol=protocol)
        operation = Operation(run_id="run-one", tool_call_id="call-one")

        async def call():
            token = operation_context.set(operation)
            try:
                return await connection.client.call_tool_mcp(
                    "confirm" if protocol != "legacy" else "legacy_confirm", {}
                )
            finally:
                operation_context.reset(token)

        task = asyncio.create_task(call())
        request = await _pending(inputs)
        assert request.run_id == "run-one"
        assert request.tool_call_id == "call-one"
        response = McpInputResponse(
            action="accept", content={"name": "Ada"} if protocol != "legacy" else {"value": "Ada"}
        )
        accepted = await inputs.respond({"thread-one"}, request.request_id, response)
        assert accepted.state == "accepted"
        assert await inputs.respond({"thread-one"}, request.request_id, response) == accepted
        with pytest.raises(HarnessUiError, match="differently"):
            await inputs.respond({"thread-one"}, request.request_id, McpInputResponse(action="decline"))
        result = await asyncio.wait_for(task, 10)
        assert not result.is_error
        if protocol != "legacy":
            assert result.structured_content["count"] == 1
            assert result.structured_content["state"] == "round-one"
        assert connection.connected
    finally:
        await inputs.close()
        await connections.close()


async def test_host_validation_rejects_bad_response_then_allows_correct_answer(tmp_path: Path) -> None:
    async def changed(thread_id: str) -> None:
        pass

    inputs = Inputs(changed)
    connections = Connections(input_handler=inputs)
    try:
        connection = await connections.acquire("thread-one", "input", "recipe", _transport(tmp_path))
        task = asyncio.create_task(connection.client.call_tool_mcp("confirm", {}))
        request = await _pending(inputs)
        with pytest.raises(HarnessUiError, match="requested form"):
            await inputs.respond(
                {"thread-one"}, request.request_id, McpInputResponse(action="accept", content={"name": 1})
            )
        assert inputs.requests({"thread-one"})[0].state == "pending"
        with pytest.raises(HarnessUiError, match="unavailable"):
            await inputs.respond({"another-thread"}, request.request_id, McpInputResponse(action="decline"))
        await inputs.respond(
            {"thread-one"}, request.request_id, McpInputResponse(action="accept", content={"name": "Ada"})
        )
        assert not (await task).is_error
    finally:
        await inputs.close()
        await connections.close()


async def test_close_does_not_deadlock_waiting_input(tmp_path: Path) -> None:
    async def changed(thread_id: str) -> None:
        pass

    inputs = Inputs(changed)
    connections = Connections(input_handler=inputs)
    connection = await connections.acquire("thread-one", "input", "recipe", _transport(tmp_path))
    task = asyncio.create_task(connection.client.call_tool_mcp("confirm", {}))
    await _pending(inputs)
    await asyncio.wait_for(connections.close(), 5)
    assert task.done()
    assert inputs.requests({"thread-one"})[0].state == "unavailable"


async def test_expired_input_is_reconciled_without_replay(tmp_path: Path) -> None:
    async def changed(thread_id: str) -> None:
        pass

    inputs = Inputs(changed, timeout_seconds=0.1)
    connections = Connections(input_handler=inputs)
    try:
        connection = await connections.acquire("thread-one", "input", "recipe", _transport(tmp_path))
        result = await connection.client.call_tool_mcp("confirm", {})
        assert not result.is_error
        assert inputs.requests({"thread-one"})[0].state == "expired"
        assert result.structured_content["count"] == 1
    finally:
        await connections.close()


async def test_generic_projection_does_not_advertise_apps(tmp_path: Path) -> None:
    connections = AppConnections()
    try:
        connection = await connections.acquire(
            "thread-one", "generic", "recipe", _transport(tmp_path), client_factory=HostClient, extensions=()
        )
        assert type(connection.client) is HostClient
        assert connection.client.server_info.name == "Input fixture"
    finally:
        await connections.close()


async def test_state_only_mrtr_round_rechecks_retired_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.mcp_runtime.connections import Connection
    from mcp.types import CallToolRequest

    original = Connection._before_request
    rounds = []

    async def check(self, request):
        if isinstance(request, CallToolRequest) and request.params.name == "state_only":
            rounds.append(request.params.request_state)
            if request.params.request_state is not None:
                self.retire()
        await original(self, request)

    monkeypatch.setattr(Connection, "_before_request", check)
    connections = Connections()
    try:
        connection = await connections.acquire(
            "thread-one", "input", "recipe", _transport(tmp_path), protocol="2026-07-28"
        )
        async with HostToolset(connection):
            with pytest.raises(RuntimeError, match="retired"):
                await connection.client.call_tool_mcp("state_only", {})
            assert len(rounds) == 2
            assert rounds[0] is None and isinstance(rounds[1], str) and rounds[1]
            assert (await connection.client.call_tool_mcp("counter", {})).structured_content["result"] == 1
    finally:
        await connections.close()


async def test_sdk_send_boundary_checks_every_business_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from a13n_harness_ui.mcp_runtime.connections import Connection
    from mcp.types import CallToolRequest

    original = Connection._before_request
    rounds = []

    async def check(self, request):
        if isinstance(request, CallToolRequest):
            rounds.append(request.params.input_responses is not None)
        await original(self, request)

    monkeypatch.setattr(Connection, "_before_request", check)

    async def changed(thread_id: str) -> None:
        pass

    inputs = Inputs(changed)
    connections = Connections(input_handler=inputs)
    try:
        connection = await connections.acquire("thread-one", "input", "recipe", _transport(tmp_path))
        checks = 0

        async def authorize():
            nonlocal checks
            checks += 1

        task = asyncio.create_task(
            connection.client.call_app_tool("confirm", {}, authorize=authorize, operation=Operation(view_id="view-one"))
        )
        pending = await _pending(inputs)
        assert pending.view_id == "view-one" and pending.run_id is None
        await inputs.respond(
            {"thread-one"}, pending.request_id, McpInputResponse(action="accept", content={"name": "Ada"})
        )
        assert not (await task).is_error
        assert rounds == [False, True]
        assert checks >= 5  # lane admission, both SDK sends, before/after the human wait
    finally:
        await connections.close()


async def test_close_is_bounded_for_cancellation_resistant_operation(tmp_path: Path) -> None:
    connections = Connections(cleanup_timeout_seconds=0.05)
    connection = await connections.acquire("thread-one", "input", "recipe", _transport(tmp_path))
    entered, release = asyncio.Event(), asyncio.Event()

    async def resistant():
        async with connection.operation():
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue

    task = asyncio.create_task(resistant())
    await entered.wait()
    try:
        async with asyncio.timeout(1):
            await connections.close()
        assert not connection.connected
        assert not task.done()
    finally:
        release.set()
        await task


async def test_response_after_expiry_cannot_win_the_timer_race(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    async def changed(thread_id):
        pass

    inputs = Inputs(changed)
    connections = Connections(input_handler=inputs)
    try:
        connection = await connections.acquire("thread-one", "input", "recipe", _transport(tmp_path))
        task = asyncio.create_task(connection.client.call_tool_mcp("confirm", {}))
        pending = await _pending(inputs)
        request = inputs._requests[pending.request_id]
        request.view = request.view.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
        with pytest.raises(HarnessUiError, match="no longer pending"):
            await inputs.respond(
                {"thread-one"}, pending.request_id, McpInputResponse(action="accept", content={"name": "Ada"})
            )
        assert not (await task).is_error
        assert inputs.requests({"thread-one"})[0].state == "expired"
    finally:
        await connections.close()


def test_form_schema_allows_titled_selections_but_not_remote_or_sensitive_inputs():
    from a13n_harness_ui.mcp_runtime.inputs import validate_schema

    options = [{"const": "a", "title": "Alpha"}, {"const": "b", "title": "Beta"}]
    validate_schema(
        {
            "type": "object",
            "properties": {
                "one": {"type": "string", "oneOf": options},
                "many": {"type": "array", "items": {"anyOf": options}},
            },
        }
    )
    for field in (
        {"type": "string", "format": "password"},
        {"type": "string", "$ref": "https://invalid.test/schema"},
        {"type": "string", "oneOf": [{"type": "number"}]},
    ):
        with pytest.raises(ValueError):
            validate_schema({"type": "object", "properties": {"value": field}})


@pytest.mark.parametrize("method", ["resource", "prompt"])
async def test_resource_and_prompt_inputs_route_to_active_operation(tmp_path: Path, method: str) -> None:
    async def changed(thread_id):
        pass

    inputs = Inputs(changed)
    connections = Connections(input_handler=inputs)
    try:
        connection = await connections.acquire("thread-one", "input", "recipe", _transport(tmp_path), protocol="legacy")

        async def call():
            token = operation_context.set(Operation(run_id="run-read", tool_call_id="call-read"))
            try:
                if method == "resource":
                    return await connection.client.read_resource_mcp("data://confirmation")
                return await connection.client.get_prompt_mcp("confirmation_prompt")
            finally:
                operation_context.reset(token)

        task = asyncio.create_task(call())
        request = await _pending(inputs)
        assert request.run_id == "run-read" and request.tool_call_id == "call-read"
        await inputs.respond(
            {"thread-one"}, request.request_id, McpInputResponse(action="accept", content={"value": "yes"})
        )
        result = await task
        assert "accept" in result.model_dump_json()
        assert connection.connected
    finally:
        await connections.close()


async def test_close_settles_startup_waiter_even_if_client_resists_cancellation(tmp_path: Path) -> None:
    from a13n_harness_ui.mcp_runtime.connections import Connection

    entered, release = asyncio.Event(), asyncio.Event()

    class ResistantClient(HostClient):
        async def __aenter__(self):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return self

        async def __aexit__(self, *args):
            pass

    connection = Connection(
        "thread-one",
        "input",
        "recipe",
        _transport(tmp_path),
        client_factory=ResistantClient,
        cleanup_timeout_seconds=0.05,
    )
    task = asyncio.create_task(connection.start())
    await entered.wait()
    try:
        async with asyncio.timeout(1):
            await connection.close()
            with pytest.raises(RuntimeError, match="startup"):
                await task
        assert not connection.connected
    finally:
        release.set()
        await connection._task
