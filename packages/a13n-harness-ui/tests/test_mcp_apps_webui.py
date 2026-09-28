from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

import httpx
import pytest
import yaml
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.webui import create_webui
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration
from .test_mcp_apps_connections import _SERVER

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("admission_wait", ["lock", "capture"])
async def test_http_original_activation_approval_reconciliation_and_close_use_real_session(
    tmp_path, monkeypatch, admission_wait
):
    root = _write_configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    document["webui"] = {"mcp_apps": {"enabled": True, "servers": ["mcp-counter"]}}
    root.write_text(yaml.safe_dump(document))
    script = tmp_path / "counter.py"
    script.write_text(_SERVER)
    (tmp_path / "mcp").mkdir(exist_ok=True)
    (tmp_path / "mcp/counter.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "kind": "mcp_server",
                "id": "mcp-counter",
                "name": "Counter",
                "transport": {"command": sys.executable, "arguments": [str(script)]},
            }
        )
    )
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["mcp_servers"] = []
    agent_path.write_text(yaml.safe_dump(agent))
    calls = 0
    received = []
    message_started, finish_message = asyncio.Event(), asyncio.Event()

    async def model(messages, info):
        nonlocal calls
        calls += 1
        received.append(str(messages))
        if calls == 1:
            tool = next(tool for tool in info.function_tools if "counter" in tool.name)
            yield {0: DeltaToolCall(name=tool.name, json_args="{}", tool_call_id="original")}
        else:
            if calls == 5:
                message_started.set()
                await finish_message.wait()
            yield "The counter is ready."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)

    @asynccontextmanager
    async def opened():
        async with open_harness_ui_app(
            _settings(tmp_path / "state"), configuration_path=root, host_mode="webui"
        ) as app:
            yield app

    async with opened() as app:

        @asynccontextmanager
        async def borrowed():
            yield app

        server = create_webui(borrowed, api_key="test-key")
        async with (
            server.router.lifespan_context(server),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
        ):
            thread = await app.create_thread()
            assert thread.configuration.mcp_server_ids == ()
            proxy = await app.inspect_agent_tool_proxy(thread.configuration.agent_source.id)
            assert next(item for item in proxy.sources if item.resource_id == "mcp-counter").enabled
            inspection = await app.inspect_thread_configuration(thread.thread_id)
            assert inspection.next_run.configuration.mcp_server_ids == ()
            assert next(
                item for item in inspection.next_tool_proxy.sources if item.resource_id == "mcp-counter"
            ).enabled
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Open a counter")
            result = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=20)
            assert result.status.value == "completed", result
            inspection = await app.inspect_thread_configuration(thread.thread_id)
            assert inspection.next_run.configuration.mcp_server_ids == ()
            assert inspection.captured.mcp_server_ids == ("mcp-counter",)
            history = await app.get_thread_transcript(thread_id=thread.thread_id)
            reference = next(ref for entry in history.entries for part in entry.parts for ref in part.mcp_apps)
            base = f"/api/threads/{thread.thread_id}/apps"
            body = reference.model_dump(mode="json")
            assert (await client.post(f"{base}/activate", json=body)).status_code == 401
            client.headers["Authorization"] = "Bearer test-key"
            assert (
                await client.post(f"{base}/activate", json=body, headers={"Origin": "https://untrusted.invalid"})
            ).status_code == 403
            original = await client.post(f"{base}/open", json=body)
            assert original.status_code == 200, original.text
            assert original.json()["snapshot"]["result"]["structuredContent"]["count"] == 1
            assert original.json()["sandbox_url"]
            response = await client.post(f"{base}/activate", json=body)
            assert response.status_code == 200, response.text
            view = response.json()
            view_base = f"{base}/{view['view_id']}"
            resource = await client.post(f"{view_base}/resources/read", json={"uri": "data://counter/current"})
            assert resource.status_code == 200, resource.text
            assert resource.json()["contents"][0]["text"] == "item=current;count=1"
            assert (
                await client.post(f"{view_base}/resources/read", json={"uri": "file:///etc/passwd"})
            ).status_code >= 400
            # A changed current policy, not the original Run's policy, owns follow-up approval.
            agent["capabilities"] = [
                {
                    "capability": "ToolPermissionsCapability",
                    "configuration": {
                        "rules": {"mcp/mcp-counter/counter": "ask"},
                    },
                }
            ]
            agent_path.write_text(yaml.safe_dump(agent))
            await app.reload_configuration()
            request = {"request_key": "one", "name": "counter", "arguments": {}}
            accepted = await client.post(f"{view_base}/tools", json=request)
            assert accepted.status_code == 200, accepted.text

            async def settle(key):
                async with asyncio.timeout(10):
                    while True:
                        response = await client.get(f"{view_base}/operations/{key}")
                        assert response.status_code == 200, response.text
                        value = response.json()
                        if value["status"] not in {"checking", "running"}:
                            return value
                        await asyncio.sleep(0.01)

            pending = await settle("one")
            assert pending["status"] == "approval_required"
            assert pending["tool_id"] == "mcp/mcp-counter/counter"
            assert (
                await client.post(f"{view_base}/operations/one/decision", json={"approve": True})
            ).status_code == 200
            completed = await settle("one")
            assert completed["status"] == "completed", completed
            assert completed["result"]["structuredContent"]["count"] == 2
            assert (await client.post(f"{view_base}/tools", json=request)).json() == completed
            assert (
                await client.post(f"{view_base}/operations/one/decision", json={"approve": True})
            ).status_code >= 400
            assert calls == 2  # App tools and reads did not run the conversational Model.
            context = await client.put(
                f"{view_base}/context",
                json={
                    "content": [{"type": "text", "text": "selected-map-boundary-unique"}],
                    "structuredContent": {"zoom": 7},
                },
            )
            assert context.status_code == 200, context.text
            context_ref = context.json()["reference"]
            assert (
                await client.put(
                    f"{view_base}/context",
                    json={"content": [{"type": "image", "data": "AA==", "mimeType": "image/png"}]},
                )
            ).status_code >= 400
            ordinary = await client.post(
                f"/api/threads/{thread.thread_id}/submit", json={"parts": ["Without App selection"]}
            )
            assert ordinary.status_code == 200, ordinary.text
            await app.wait_root_operation(ordinary.json()["receipt_id"], timeout_seconds=20)
            assert "selected-map-boundary-unique" not in received[-1]
            selected = await client.post(
                f"/api/threads/{thread.thread_id}/submit",
                json={"parts": ["Explain the selected area"], "app_context": [context_ref]},
            )
            assert selected.status_code == 200, selected.text
            await app.wait_root_operation(selected.json()["receipt_id"], timeout_seconds=20)
            assert "selected-map-boundary-unique" in received[-1]
            assert "external data" in received[-1]
            assert calls == 4
            assert (await client.delete(f"{view_base}/context")).status_code == 204
            stale = await client.post(
                f"/api/threads/{thread.thread_id}/submit",
                json={"parts": ["Do not submit discarded context"], "app_context": [context_ref]},
            )
            assert stale.status_code >= 400
            message = {
                "request_key": "message-one",
                "content": [{"type": "text", "text": "User confirmed App follow-up"}],
            }
            sent = await client.post(f"{view_base}/messages", json=message)
            assert sent.status_code == 200, sent.text

            async def message_receipt(key):
                async with asyncio.timeout(10):
                    while True:
                        response = await client.get(f"{view_base}/messages/{key}")
                        assert response.status_code == 200, response.text
                        value = response.json()
                        if value["status"] != "submitting":
                            return value
                        await asyncio.sleep(0.01)

            accepted_message = await message_receipt("message-one")
            assert accepted_message["status"] == "accepted", accepted_message
            assert (await client.post(f"{view_base}/messages", json=message)).json() == accepted_message
            async with asyncio.timeout(10):
                await message_started.wait()
            try:
                busy = await client.post(f"{view_base}/messages", json={**message, "request_key": "message-busy"})
                assert busy.status_code == 200, busy.text
                assert (await message_receipt("message-busy"))["status"] == "failed"
            finally:
                finish_message.set()
            await app.wait_root_operation(accepted_message["receipt"]["receipt_id"], timeout_seconds=20)
            assert "User confirmed App follow-up" in received[-1]
            assert "User-confirmed App message" in received[-1]
            assert (await client.delete(view_base)).status_code == 204
            assert (await client.get(f"{view_base}/operations/one")).json() == completed
            assert (await client.post(f"{base}/open", json=body)).json()["snapshot"]["result"]["structuredContent"][
                "count"
            ] == 1
            connection = app._mcp_apps.connections.get(thread.thread_id, "mcp-counter")
            assert connection.connected
            assert calls == 5  # Only ordinary user input and one confirmed App message started new turns.
            response = await client.post(f"{base}/activate", json=body)
            assert response.status_code == 200, response.text
            view_base = f"{base}/{response.json()['view_id']}"

            # The outer App check is not admission: root submission can wait on
            # the Thread admission fence while configuration revokes its authority.
            entered_submission = asyncio.Event()
            submit_prompt = app._root_runs.submit_prompt

            async def observed_submit(**kwargs):
                entered_submission.set()
                return await submit_prompt(**kwargs)

            capture_finished, release_capture = asyncio.Event(), asyncio.Event()
            capture = app._root_runs._executor.capture

            async def paused_capture(**kwargs):
                admission = await capture(**kwargs)
                capture_finished.set()
                await release_capture.wait()
                return admission

            monkeypatch.setattr(app._root_runs, "submit_prompt", observed_submit)
            if admission_wait == "capture":
                monkeypatch.setattr(app._root_runs._executor, "capture", paused_capture)
            revoked_message = {
                "request_key": "message-revoked-at-admission",
                "content": [{"type": "text", "text": "Must not reach the model after revocation"}],
            }

            async def revoke_after(waiting):
                sent = await client.post(f"{view_base}/messages", json=revoked_message)
                assert sent.status_code == 200, sent.text
                async with asyncio.timeout(10):
                    await waiting.wait()
                document["webui"]["mcp_apps"]["enabled"] = False
                root.write_text(yaml.safe_dump(document))
                await app.reload_configuration()

            if admission_wait == "lock":
                async with app._root_runs._thread_fence(thread.thread_id):
                    await revoke_after(entered_submission)
            else:
                try:
                    await revoke_after(capture_finished)
                finally:
                    release_capture.set()
            rejected = await message_receipt(revoked_message["request_key"])
            assert rejected["status"] == "failed", rejected
            assert rejected["receipt"] is None
            assert (await client.post(f"{view_base}/messages", json=revoked_message)).json() == rejected
            assert await app._root_runs.active(thread.thread_id) is None
            assert calls == 5
            assert all("Must not reach the model after revocation" not in value for value in received)
            assert connection.retired
            inspection = await app.inspect_thread_configuration(thread.thread_id)
            assert inspection.next_run.configuration.mcp_server_ids == ()
            assert not next(
                item for item in inspection.next_tool_proxy.sources if item.resource_id == "mcp-counter"
            ).enabled
            assert inspection.captured.mcp_server_ids == ("mcp-counter",)
