"""Human input completes the existing active receipt, not a deferred Run."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.mcp_runtime.inputs import McpInputResponse
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import RootOperationStatus
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration
from .test_mcp_runtime import _SERVER

pytestmark = pytest.mark.anyio


def input_configuration(tmp_path: Path, *, retained: bool) -> Path:
    root = _write_configuration(tmp_path)
    server = tmp_path / "input_server.py"
    server.write_text(_SERVER)
    directory = tmp_path / "mcp"
    directory.mkdir()
    (directory / "input.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "kind": "mcp_server",
                "id": "mcp-input",
                "name": "Input",
                "transport": {"command": sys.executable, "arguments": [str(server)]},
            }
        )
    )
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "mcp_servers: [mcp-input]\n")
    if retained:
        root.write_text(root.read_text() + "mcp:\n  host_owned_servers: [mcp-input]\n")
    return root


def install_input_model(monkeypatch):
    seen = []

    async def stream(messages, info):
        latest_user = max(
            index
            for index, message in enumerate(messages)
            if isinstance(message, ModelRequest) and any(isinstance(part, UserPromptPart) for part in message.parts)
        )
        returns = [
            part
            for message in messages[latest_user:]
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            seen.append(str(returns[-1].content))
            yield "Confirmed"
        else:
            name = next(
                tool.name for tool in info.function_tools if tool.name.endswith("confirm") and "legacy" not in tool.name
            )
            yield {0: DeltaToolCall(name=name, json_args="{}", tool_call_id="confirm-one")}

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    return seen


async def pending(app, thread_id):
    async with asyncio.timeout(15):
        while True:
            requests = await app.mcp_input_requests(thread_id)
            if any(item.state == "pending" for item in requests):
                return next(item for item in requests if item.state == "pending")
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("retained", [False, True])
async def test_input_completes_same_receipt_across_runs(tmp_path: Path, monkeypatch, retained: bool):
    root = input_configuration(tmp_path, retained=retained)
    seen = install_input_model(monkeypatch)
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=root, mcp_input_enabled=True
    ) as app:
        thread = await app.create_thread()
        generations = []
        for _index in range(2):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Confirm")
            request = await pending(app, thread.thread_id)
            assert request.run_id and request.tool_call_id == "confirm-one"
            active = await app.active_root_operation(thread.thread_id)
            assert active is not None and active.receipt.receipt_id == receipt.receipt_id
            async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
                assert request in watch.snapshot.mcp_inputs
            answer = McpInputResponse(action="accept", content={"name": "Ada"})
            await app.respond_mcp_input(thread.thread_id, request.request_id, answer)
            assert (await app.respond_mcp_input(thread.thread_id, request.request_id, answer)).state == "accepted"
            result = await app.wait_root_operation(receipt.receipt_id)
            assert result.status is RootOperationStatus.completed
            integrations = await app.mcp_status(thread.thread_id)
            if retained:
                assert len(integrations) == 1 and integrations[0].connected
                generations.append(integrations[0].generation)
            else:
                assert integrations == ()
        if retained:
            assert generations[0] == generations[1]
            await app.close_mcp_integration(thread.thread_id, "mcp-input")
            assert not (await app.mcp_status(thread.thread_id))[0].connected
    assert len(seen) == 2


async def test_headless_does_not_publish_or_wait_for_human_input(tmp_path: Path, monkeypatch):
    root = input_configuration(tmp_path, retained=False)
    install_input_model(monkeypatch)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Confirm")
        async with asyncio.timeout(15):
            await app.wait_root_operation(receipt.receipt_id)
        assert await app.mcp_input_requests(thread.thread_id) == ()


async def test_http_input_response_is_authenticated_scoped_and_not_run_admission(tmp_path: Path, monkeypatch):
    from contextlib import asynccontextmanager

    import httpx
    from a13n_harness_ui.webui import create_webui

    root = input_configuration(tmp_path, retained=True)
    install_input_model(monkeypatch)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:

        @asynccontextmanager
        async def borrowed():
            yield app

        server = create_webui(borrowed, api_key="input-test-key")
        async with (
            server.router.lifespan_context(server),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
        ):
            thread, other = await app.create_thread(), await app.create_thread()
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Confirm")
            request = await pending(app, thread.thread_id)
            base = f"/api/threads/{thread.thread_id}/mcp"
            assert (await client.get(base + "/inputs")).status_code == 401
            client.headers["Authorization"] = "Bearer input-test-key"
            assert (await client.get(base + "/inputs")).json()[0]["request_id"] == request.request_id
            response_path = base + f"/inputs/{request.request_id}/response"
            body = {"action": "accept", "content": {"name": "Ada"}}
            assert (
                await client.post(response_path, json=body, headers={"Origin": "https://untrusted.invalid"})
            ).status_code == 403
            wrong_scope = f"/api/threads/{other.thread_id}/mcp/inputs/{request.request_id}/response"
            assert (await client.post(wrong_scope, json=body)).status_code >= 400
            invalid = await client.post(response_path, json={"action": "accept", "content": {"name": 1}})
            assert invalid.status_code >= 400 and invalid.json()["error"]["code"] == "mcp_input_invalid"
            accepted = await client.post(response_path, json=body)
            assert accepted.status_code == 200 and accepted.json()["state"] == "accepted"
            assert (await client.post(response_path, json=body)).json() == accepted.json()
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
            assert (await client.post(base + "/integrations/mcp-input/close")).status_code == 204


async def test_terminal_input_keeps_active_job_and_restores_draft(tmp_path: Path, monkeypatch):
    from a13n_harness_ui.cli import CliRequest
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.interactive.rendering import Status
    from a13n_harness_ui.interactive.shell import CliShell

    root = input_configuration(tmp_path, retained=False)
    install_input_model(monkeypatch)
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=root, mcp_input_enabled=True
    ) as app:
        shell = CliShell(CliRequest(), directory=tmp_path)
        shell.backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        thread = await app.create_thread()
        shell.backend.thread_id = thread.thread_id
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Confirm")
        await pending(app, thread.thread_id)
        job = asyncio.create_task(app.wait_root_operation(receipt.receipt_id))
        shell.job = job
        shell.composer.buffer.text = "My unfinished draft"
        await shell._activate_mcp_inputs()
        assert shell.mcp_input is not None
        await shell._answer_mcp_input("Ada")
        assert shell.job is job and not job.done()
        await shell._answer_mcp_input("accept")
        assert shell.job is job
        assert shell.composer.buffer.text == "My unfinished draft"
        assert (await job).status is RootOperationStatus.completed


async def test_child_input_scope_and_sticky_retention_are_not_parent_selection(tmp_path: Path, monkeypatch):
    from a13n_harness_ui.errors import HarnessUiError
    from a13n_harness_ui.mcp_runtime.connections import Operation
    from fastmcp.client.transports import StdioTransport

    from .test_thread_repository import _initial

    root = input_configuration(tmp_path, retained=True)
    install_input_model(monkeypatch)
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=root, mcp_input_enabled=True
    ) as app:
        parent, unrelated = await app.create_thread(), await app.create_thread()
        parent = await app._store.threads.get(parent.thread_id)
        await app._store.threads.create(
            thread_id="child-input",
            parent_thread_id=parent.thread_id,
            configuration=parent.configuration,
            initial_state=_initial(),
        )
        connection = await app._mcp_connections.acquire(
            "child-input",
            "mcp-input",
            "child-binding",
            StdioTransport(command=sys.executable, args=[str(tmp_path / "input_server.py")]),
        )

        async def authorize():
            pass

        task = asyncio.create_task(
            connection.client.call_app_tool(
                "confirm", {}, authorize=authorize, operation=Operation(run_id="run-child", tool_call_id="child-call")
            )
        )
        request = await pending(app, parent.thread_id)
        assert request.thread_id == "child-input" and request.run_id == "run-child"
        assert await app.mcp_input_requests(unrelated.thread_id) == ()
        answer = McpInputResponse(action="accept", content={"name": "Ada"})
        with pytest.raises(HarnessUiError, match="unavailable"):
            await app.respond_mcp_input(unrelated.thread_id, request.request_id, answer)
        await app.respond_mcp_input(parent.thread_id, request.request_id, answer)
        assert not (await task).is_error

        # A child owns its saved generic selection, not today's parent route.
        await app._store.threads.update_configuration(
            thread_id=parent.thread_id,
            expected_version=parent.configuration.version,
            replacement=parent.configuration.model_copy(update={"version": 2, "mcp_server_ids": ()}),
        )
        await app._mcp_app_owners.retire_unselected(app._mcp_connections)
        assert connection.connected and not connection.retired
        child = await app._store.threads.get("child-input")
        await app._store.threads.update_configuration(
            thread_id="child-input",
            expected_version=child.configuration.version,
            replacement=child.configuration.model_copy(update={"version": 2, "mcp_server_ids": ()}),
        )
        await app._mcp_app_owners.retire_unselected(app._mcp_connections)
        assert connection.retired
