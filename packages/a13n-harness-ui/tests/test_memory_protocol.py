"""Memory observation uses ordinary authenticated HTTP/focus boundaries, never an editor."""

from __future__ import annotations

import json

import httpx
import pytest
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from anyio import Event, fail_after, sleep
from pydantic_ai.models.function import FunctionModel
from websockets.asyncio.client import connect

from .test_configuration_protocol import HEADERS
from .test_interactive_protocol import listener
from .test_memory import _seed

pytestmark = pytest.mark.anyio


async def test_memory_http_reads_without_host_sharing_and_rejects_known_id_writes(tmp_path, monkeypatch):
    root, _, _, _ = await _seed(tmp_path)
    started, release = Event(), Event()
    memory_calls = 0

    async def model(messages, info):
        nonlocal memory_calls
        if info.function_tools and all(tool.name.startswith("memory_") for tool in info.function_tools):
            memory_calls += 1
            started.set()
            await release.wait()
        yield "Observed organization"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, sharing=False, configuration_path=root) as (http, ws):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            assert (await api.get("/api/memory/files", headers={"Authorization": "Bearer wrong"})).status_code == 401
            assert [entry["path"] for entry in (await api.get("/api/memory/files")).json()] == ["MEMORY.md"]
            assert (
                "Keep stable preferences"
                in (await api.get("/api/memory/file", params={"path": "MEMORY.md"})).json()["text"]
            )
            assert (await api.get("/api/memory/file", params={"path": "../a13n-harness-ui.yaml"})).status_code == 400
            assert (await api.get("/api/memory/files", params={"project_id": "unknown"})).status_code == 404
            assert (await api.get("/api/threads", params={"memory": True})).json()["total"] == 0
            assert memory_calls == 0
            # Only foreground input offers an automatic organization opportunity.
            ordinary = (await api.post("/api/threads", json={})).json()["thread_id"]
            submitted = await api.post(f"/api/threads/{ordinary}/submit", json={"parts": ["Hello"]})
            assert submitted.status_code == 200, submitted.text
            with fail_after(10):
                await started.wait()
            memory = (await api.get("/api/threads", params={"memory": True, "projectless": True})).json()["threads"][0]
            tid = memory["thread_id"]
            prefix = f"/api/threads/{tid}"
            detail = (await api.get(prefix)).json()
            assert detail["available_actions"] == []
            assert tid not in {row["thread_id"] for row in (await api.get("/api/threads")).json()["threads"]}
            captured = (await api.get(prefix + "/configuration")).json()["captured"]
            assert captured["agent"]["source_kind"] == "memory"
            assert captured["environment_profile_id"] is None
            writes = [
                await api.post(prefix + "/submit", json={"parts": ["Manual"]}),
                await api.patch(
                    prefix + "/metadata",
                    json={"expected_version": memory["metadata_version"], "patch": {"title": "Changed"}},
                ),
                await api.patch(
                    prefix + "/configuration", json={"expected_version": 1, "patch": {"agent_id": "agent-assistant"}}
                ),
                await api.post(prefix + "/attachments", params={"name": "note.txt"}, content=b"Forbidden"),
                await api.post(f"/api/operations/{detail['thread']['root_activity']['receipt_id']}/cancel"),
            ]
            for response in writes:
                assert response.status_code == 400, response.text
                assert response.json()["error"]["code"] == "memory_thread_read_only"
            async with connect(ws + prefix + "/draft/connect", proxy=None, origin=http) as client:
                await client.send(json.dumps({"api_key": "test-only-key"}))
                with fail_after(5):
                    frame = json.loads(await client.recv())
                assert frame["error"]["code"] == "memory_thread_read_only"
            release.set()
            with fail_after(10):
                while (await api.get(prefix)).json()["thread"]["root_activity"]["state"] != "inactive":
                    await sleep(0.01)
            assert "Observed organization" in (await api.get(prefix + "/transcript")).text
            assert (await api.get(prefix + "/usage")).json()["root"]["model_requests"] == 1
            assert memory_calls == 1
