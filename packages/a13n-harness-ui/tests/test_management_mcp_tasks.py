"""Exercise SEP-2663 over the owner WebUI mount against real root Runs and SQLite."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import a13n_harness_ui.model_accounts.codex_transport as codex
import httpx
import pytest
from a13n_harness_ui.app import HarnessUiApp, open_harness_ui_app
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.webui import create_webui
from anyio import Event, fail_after, sleep
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_interactive import _seed, no_real_model_requests  # noqa: F401 (autouse guard)

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]
_EXTENSION = "io.modelcontextprotocol/tasks"
_VERSION = "2026-07-28"
_ENDPOINT = "/api/automation/mcp"


@asynccontextmanager
async def _server(config: Path, data_root: Path) -> AsyncIterator[tuple[httpx.AsyncClient, HarnessUiApp]]:
    opened = None

    @asynccontextmanager
    async def factory():
        nonlocal opened
        settings = HarnessUiSettings(storage=StorageSettings(data_root=data_root), pricing_auto_update=False)
        async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
            opened = app
            yield app

    server = create_webui(factory, api_key="test-only-key", host="127.0.0.1")
    async with server.router.lifespan_context(server):
        assert opened is not None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://localhost") as client:
            yield client, opened


async def _rpc(
    client: httpx.AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    tasks: bool = True,
    auth: bool = True,
    version: str = _VERSION,
) -> httpx.Response:
    params = dict(params or {})
    headers = {"accept": "application/json, text/event-stream", "mcp-protocol-version": version}
    if auth:
        headers["authorization"] = "Bearer test-only-key"
    if version == _VERSION:
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientCapabilities": {"extensions": {_EXTENSION: {}}} if tasks else {},
        }
        headers["mcp-method"] = method
        name = params.get("taskId", params.get("name"))
        if name is not None:
            headers["mcp-name"] = name
    return await client.post(
        _ENDPOINT, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )


def _result(response: httpx.Response) -> dict[str, Any]:
    assert response.is_success, response.text
    data = response.json()
    assert "result" in data, data
    return data["result"]


async def _tool(client: httpx.AsyncClient, name: str, *, tasks: bool = True, **args: Any) -> dict[str, Any]:
    return _result(await _rpc(client, "tools/call", {"name": name, "arguments": args}, tasks=tasks))


async def _get(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    return _result(await _rpc(client, "tasks/get", {"taskId": task_id}))


async def _terminal(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    with fail_after(10):
        while True:
            task = await _get(client, task_id)
            if task["status"] != "working":
                return task
            await sleep(0.02)


async def test_tasks_discover_poll_and_reopen_old_run_after_followup(tmp_path, monkeypatch):
    config = await _seed(tmp_path, monkeypatch)
    data = tmp_path / "data"
    finish = Event()
    outputs = iter(["first output", "second output"])

    async def stream(messages, info):
        await finish.wait()
        yield next(outputs)

    monkeypatch.setattr(codex, "SubscriptionCodexModel", lambda *a, **kw: FunctionModel(stream_function=stream))
    async with _server(config, data) as (client, app):
        discovered = _result(await _rpc(client, "server/discover", tasks=False))
        assert discovered["capabilities"]["extensions"][_EXTENSION] == {}
        assert "tasks" not in discovered["capabilities"]
        listed = _result(await _rpc(client, "tools/list"))
        assert "ctx" not in next(tool for tool in listed["tools"] if tool["name"] == "create_thread")[
            "inputSchema"
        ].get("properties", {})
        first = await _tool(client, "create_thread", prompt="start")
        assert first["resultType"] == "task"
        assert first["status"] == "working"
        assert first["ttlMs"] is None
        params = {"taskId": first["taskId"]}
        assert (await _rpc(client, "tasks/get", params, auth=False)).status_code == 401
        assert (await _rpc(client, "tasks/get", params, tasks=False)).json()["error"]["code"] == -32021
        working = await _get(client, first["taskId"])
        assert working == {**first, "resultType": "complete"}
        finish.set()
        completed = await _terminal(client, first["taskId"])
        payload = completed["result"]["structuredContent"]
        thread_id = payload["thread_id"]
        assert payload == {
            "ok": True,
            "thread_id": thread_id,
            "status": "completed",
            "output": "first output",
            "output_omitted": False,
        }
        assert completed["status"] == "completed"
        assert (await app.get_thread(thread_id)).thread.last_execution.run_id == first["taskId"]
        second = await _tool(client, "send_thread_message", thread_id=thread_id, message="continue")
        assert second["resultType"] == "task"
        assert second["taskId"] != first["taskId"]
        second_result = await _terminal(client, second["taskId"])
        assert second_result["result"]["structuredContent"]["output"] == "second output"
        assert await _get(client, first["taskId"]) == completed

    async with _server(config, data) as (client, _):
        assert await _get(client, first["taskId"]) == completed
        assert await _get(client, second["taskId"]) == second_result
        for removed in ("tasks/result", "tasks/payload", "tasks/list"):
            assert (await _rpc(client, removed, params)).json()["error"]["code"] == -32601


async def test_unnegotiated_calls_keep_the_receipt_and_are_not_tasks(tmp_path, monkeypatch):
    config = await _seed(tmp_path, monkeypatch)

    async def stream(messages, info):
        yield "done"

    monkeypatch.setattr(codex, "SubscriptionCodexModel", lambda *a, **kw: FunctionModel(stream_function=stream))
    async with _server(config, tmp_path / "data") as (client, app):
        first = _result(
            await _rpc(
                client,
                "tools/call",
                {"name": "create_thread", "arguments": {"prompt": "start"}},
                tasks=False,
                version="2025-11-25",
            )
        )
        payload = first["structuredContent"]
        receipt = payload["receipt"]
        thread_id = payload["thread_id"]
        assert payload == {"ok": True, "thread_id": thread_id, "receipt": receipt}
        assert set(receipt) == {"receipt_id", "thread_id", "submitted_at"}
        assert json.loads(first["content"][0]["text"]) == payload
        finished = await app.wait_root_operation(receipt["receipt_id"])
        assert finished.run_id is not None
        assert (await _rpc(client, "tasks/get", {"taskId": finished.run_id})).json()["error"]["code"] == -32602
        second = await _tool(client, "send_thread_message", tasks=False, thread_id=thread_id, message="continue")
        assert second["resultType"] == "complete"
        payload = second["structuredContent"]
        assert set(payload) == {"ok", "mode", "receipt"}
        assert payload["mode"] == "run"
        assert payload["receipt"]["thread_id"] == thread_id


async def test_tasks_cancel_only_the_matching_run_and_report_execution_failure(tmp_path, monkeypatch):
    config = await _seed(tmp_path, monkeypatch)
    blocked = Event()
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "first output"
            return
        if calls == 2:
            await blocked.wait()
        raise RuntimeError("private model exception")

    monkeypatch.setattr(codex, "SubscriptionCodexModel", lambda *a, **kw: FunctionModel(stream_function=stream))
    async with _server(config, tmp_path / "data") as (client, _):
        first = await _tool(client, "create_thread", prompt="start")
        completed = await _terminal(client, first["taskId"])
        thread_id = completed["result"]["structuredContent"]["thread_id"]
        second = await _tool(client, "send_thread_message", thread_id=thread_id, message="wait")
        assert _result(await _rpc(client, "tasks/cancel", {"taskId": first["taskId"]}))["resultType"] == "complete"
        assert (await _get(client, second["taskId"]))["status"] == "working"
        assert _result(await _rpc(client, "tasks/cancel", {"taskId": second["taskId"]}))["resultType"] == "complete"
        assert (await _terminal(client, second["taskId"]))["status"] == "cancelled"
        blocked.set()
        third = await _tool(client, "send_thread_message", thread_id=thread_id, message="fail")
        failed = await _terminal(client, third["taskId"])
        assert failed["status"] == "completed"
        assert failed["result"]["isError"] is True
        payload = failed["result"]["structuredContent"]
        assert payload["status"] == "failed"
        assert payload["thread_id"] == thread_id
        assert "private model exception" not in payload["message"]


async def test_task_finishes_with_webui_guidance_for_pending_approval(tmp_path, monkeypatch):
    config = await _seed(tmp_path, monkeypatch)

    async def stream(messages, info):
        yield {
            0: DeltaToolCall(
                name="ask_user_question",
                tool_call_id="approval",
                json_args=json.dumps(
                    {
                        "questions": [
                            {
                                "header": "Option",
                                "question": "Which option?",
                                "options": [
                                    {"label": "One", "description": "First"},
                                    {"label": "Two", "description": "Second"},
                                ],
                            }
                        ]
                    }
                ),
            )
        }

    monkeypatch.setattr(codex, "SubscriptionCodexModel", lambda *a, **kw: FunctionModel(stream_function=stream))
    async with _server(config, tmp_path / "data") as (client, app):
        created = await _tool(client, "create_thread", prompt="request approval")
        assert created["resultType"] == "task"
        finished = await _terminal(client, created["taskId"])
        assert finished["status"] == "completed"
        assert "inputRequests" not in finished
        payload = finished["result"]["structuredContent"]
        assert payload["status"] == "suspended"
        assert "WebUI" in payload["message"]
        assert (await app.get_thread(payload["thread_id"])).deferred_requests
