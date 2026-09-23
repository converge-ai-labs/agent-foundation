from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml
from a13n_harness_ui.composition.service import RunCompositionService
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from anyio import Event, fail_after, sleep
from pydantic_ai.models.function import FunctionModel

from .test_app import _write_configuration
from .test_interactive_protocol import listener

pytestmark = pytest.mark.anyio
HEADERS = {"Authorization": "Bearer test-only-key"}


async def settled(api: httpx.AsyncClient, receipt: str) -> dict:
    with fail_after(10):
        while True:
            response = await api.get(f"/api/operations/{receipt}")
            assert response.status_code == 200, response.text
            value = response.json()
            if value["status"] not in {"preparing", "running"}:
                return value
            await sleep(0.01)


async def test_captured_configuration_never_uses_current_source_or_previous_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_configuration(tmp_path, instructions="private-instruction-not-for-inspection")
    second_model = tmp_path / "models/second.yaml"
    second_model.write_text((tmp_path / "models/primary.yaml").read_text().replace("model-primary", "model-second"))
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp/docs.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "kind": "mcp_server",
                "id": "mcp-docs",
                "name": "Docs",
                "transport": {"command": "never-start-this", "environment": {"SECRET": "private-mcp-value"}},
            }
        )
    )
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent.update(mcp_servers=[], tool_proxy={"groups": {"docs": {"description": "Docs", "mcp_servers": ["mcp-docs"]}}})
    agent_path.write_text(yaml.safe_dump(agent))
    started, release, publishing, publish_release = Event(), Event(), Event(), Event()
    calls = 0
    original_publish = RunCompositionService.publish

    async def publish(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            publishing.set()
            await publish_release.wait()
        return await original_publish(self, *args, **kwargs)

    async def model(messages, info):
        started.set()
        await release.wait()
        yield "configuration test complete"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    monkeypatch.setattr(RunCompositionService, "publish", publish)
    async with listener(tmp_path, configuration_path=root) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            preview = await api.post("/api/threads/configuration-preview", json={})
            assert preview.status_code == 200, preview.text
            assert preview.json()["provenance"] == {
                "project_id": "global",
                "local_roots": "project",
                "agent_source": "global",
                "default_model_id": "agent",
                "environment_profile_id": "builtin",
                "environment_bindings": "builtin",
                "default_environment": "builtin",
                "harness_plugin_ids": "global",
                "environment_run_extension_ids": "global",
                "mcp_server_ids": "agent",
            }
            created = (await api.post("/api/threads", json={})).json()
            thread_id = created["thread_id"]
            prefix = f"/api/threads/{thread_id}"
            before = (await api.get(prefix + "/configuration")).json()
            assert before["captured"] is None and before["capture_source"] == "none"
            assert before["next_run"]["provenance"]["default_model_id"] == "agent"
            assert set(before["next_run"]["provenance"].values()) == {"thread", "agent"}
            assert before["next_run"]["provenance"]["environment_bindings"] == "thread"
            assert before["next_run"]["provenance"]["default_environment"] == "thread"
            static = await api.get("/api/agents/agent-assistant/tool-proxy")
            assert static.status_code == 200, static.text
            assert static.json()["sources"][0]["presentation"] == "dormant"
            assert (await api.get("/api/catalog")).json()
            first = (await api.post(prefix + "/submit", json={"prompt": "first"})).json()["receipt_id"]
            with fail_after(10):
                await started.wait()
            captured = (await api.get(f"/api/operations/{first}/configuration")).json()
            assert captured["agent"]["model_id"] == "model-primary"
            assert captured["mcp_server_ids"] == [] and captured["tool_proxy"]["groups"] == {}
            assert "private-" not in json.dumps(captured) and "authentication" in captured["omitted_fields"]
            agent["model"] = "model-second"
            saved = await api.put(
                "/api/configuration/sources/agents/assistant.yaml", json={"content": yaml.safe_dump(agent)}
            )
            assert saved.status_code == 200, saved.text
            mutation = {"expected_version": 1, "patch": {"mcp_server_ids": []}}
            updated = await api.patch(prefix + "/configuration", json=mutation)
            assert updated.status_code == 200, updated.text
            assert (await api.patch(prefix + "/configuration", json=mutation)).status_code == 409
            during = (await api.get(prefix + "/configuration")).json()
            assert during["capture_source"] == "active_operation" and during["receipt_id"] == first
            assert during["captured"] == captured
            assert during["next_model_id"] == "model-second" and during["next_run"]["configuration"]["version"] == 2
            assert during["next_generation_digest"] != captured["generation_digest"]
            # Invalid candidate source never replaces the last accepted generation.
            bad = await api.put("/api/configuration/sources/agents/assistant.yaml", json={"content": "not: [valid"})
            assert bad.status_code == 400, bad.text
            assert (await api.get(prefix + "/configuration")).json()["next_generation_digest"] == during[
                "next_generation_digest"
            ]
            release.set()
            assert (await settled(api, first))["status"] == "completed"
            saved_first = (await api.get(prefix + "/configuration")).json()
            assert saved_first["capture_source"] == "selected_continuation" and saved_first["captured"] == captured
            import asyncio

            submitting = asyncio.create_task(api.post(prefix + "/submit", json={"prompt": "second"}))
            try:
                with fail_after(10):
                    await publishing.wait()
                assert not submitting.done()  # A receipt requires a complete admission capture.
            finally:
                publish_release.set()
            second = (await submitting).json()["receipt_id"]
            preparing = (await api.get(f"/api/operations/{second}/configuration")).json()
            assert preparing["agent"]["model_id"] == "model-second"
            assert (await settled(api, second))["status"] == "completed"
            final = (await api.get(prefix + "/configuration")).json()
            assert final["captured"]["agent"]["model_id"] == "model-second"
            assert final["captured"]["thread_configuration_version"] == 2
            assert final["captured"]["composition_id"] != captured["composition_id"]
            # Both exact current-process receipts remain inspectable independently.
            assert (await api.get(f"/api/operations/{first}/configuration")).json() == captured
    async with listener(tmp_path, configuration_path=root) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            restored = (await api.get(prefix + "/configuration")).json()
            assert restored["captured"] == final["captured"]
            assert restored["capture_source"] == "selected_continuation" and restored["receipt_id"] is None


async def test_inspection_accounts_and_notes_use_existing_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_configuration(tmp_path)
    auth = tmp_path / "grok-auth.json"
    scope = "https://issuer.example::test-client"
    auth.write_text(
        json.dumps(
            {
                scope: {
                    "key": "private-access-token",
                    "auth_mode": "oidc",
                    "create_time": datetime.now(UTC).isoformat(),
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "user_id": "account-1",
                    "refresh_token": "private-refresh-token",
                    "oidc_issuer": "https://issuer.example",
                    "oidc_client_id": "test-client",
                }
            }
        )
    )
    monkeypatch.setenv("GROK_AUTH_PATH", str(auth))
    async with listener(tmp_path, configuration_path=root) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            account = await api.get("/api/auth/accounts/grok")
            assert account.status_code == 200, account.text
            assert account.json()["availability"] == "available" and "private-" not in account.text
            assert (await api.delete("/api/auth/accounts/grok")).json() is True
            assert (await api.get("/api/auth/accounts/grok")).json()["availability"] == "absent"
            assert (await api.delete("/api/auth/accounts/grok")).json() is False
            assert (await api.get("/api/auth/accounts/invalid")).status_code == 422
            thread_id = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread_id}"
            detail = (await api.get(prefix)).json()
            notes = await api.get(prefix + "/notes")
            assert notes.status_code == 200, notes.text
            assert notes.json()["continuation_id"] == detail["continuation_id"]
            stale = await api.get(prefix + "/notes", params={"expected_continuation_id": "wrong"})
            assert stale.status_code == 409, stale.text
            context = await api.get(prefix + "/context-usage")
            assert context.status_code == 200 and context.json()["latest_request_tokens"] is None
            usage = await api.get(prefix + "/usage")
            assert usage.status_code == 200 and usage.json()["combined"]["model_requests"] == 0
            assert (await api.get("/api/configuration/sources/mcp/unknown.yaml")).status_code == 404


async def test_sidekick_settings_save_applies_to_future_webui_runs_and_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.models.function import DeltaToolCall

    root = _write_configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    started, release = Event(), Event()
    seen = []

    async def model(messages, info):
        seen.append(info.instructions or "")
        if len(seen) == 1:
            started.set()
            await release.wait()
            yield {0: DeltaToolCall(name="get_thread", json_args="{}", tool_call_id="call-context")}
        else:
            yield "Settings inspected"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=root) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            created = (await api.post("/api/threads", json={})).json()
            prefix = f"/api/threads/{created['thread_id']}"
            first = (await api.post(prefix + "/submit", json={"prompt": "Inspect settings"})).json()["receipt_id"]
            with fail_after(10):
                await started.wait()
            assert "Sidekick is enabled" in seen[0] and "Agent 'agent-assistant'" in seen[0]
            captured = (await api.get(f"/api/operations/{first}/configuration")).json()
            assert captured["webui_sidekick"] == {"agent": None, "model": None}
            invalid = {**document, "webui": {"sidekick": {"agent": "agent-missing"}}}
            assert (
                await api.put(
                    "/api/configuration/sources/a13n-harness-ui.yaml", json={"content": yaml.safe_dump(invalid)}
                )
            ).status_code == 400
            document["webui"] = {"sidekick": None}
            saved = await api.put(
                "/api/configuration/sources/a13n-harness-ui.yaml", json={"content": yaml.safe_dump(document)}
            )
            assert saved.status_code == 200, saved.text
            assert (await api.get(f"/api/operations/{first}/configuration")).json() == captured
            release.set()
            assert (await settled(api, first))["status"] == "completed"
            assert "Sidekick is enabled" in seen[1]  # Later requests in the same Run retain captured instructions.
            second = (await api.post(prefix + "/submit", json={"prompt": "Inspect again"})).json()["receipt_id"]
            assert (await settled(api, second))["status"] == "completed"
            assert "Sidekick is enabled" not in seen[2]
            assert (await api.get(f"/api/operations/{second}/configuration")).json()["webui_sidekick"] is None
            assert (await api.get("/api/threads")).json()["total"] == 1  # Settings never create work.
    async with listener(tmp_path, configuration_path=root) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            saved = (await api.get("/api/configuration/sources/a13n-harness-ui.yaml")).json()
            assert yaml.safe_load(saved["content"])["webui"]["sidekick"] is None
            assert (await api.get("/api/threads")).json()["total"] == 1
            third = (await api.post(prefix + "/submit", json={"prompt": "Still disabled"})).json()["receipt_id"]
            assert (await settled(api, third))["status"] == "completed"
            assert (await api.get(f"/api/operations/{third}/configuration")).json()["webui_sidekick"] is None
            assert "Sidekick is enabled" not in seen[-1]
