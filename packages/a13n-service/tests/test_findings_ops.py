"""Operations-first launch upgrades current preferences and navigates native child evidence."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from a13n_service.providers.traces import SpanPage
from a13n_service.runs.findings import analysis, preset
from a13n_service.runs.findings.schemas import AnalysisCreate

from .test_builtin_agents import prepared
from .test_findings import TRACE, Backend, actor, finding, target

pytestmark = pytest.mark.anyio


async def test_start_upgrades_existing_finder_using_preferences_edited_during_selection(
    service, scripted_model, runs_kit, monkeypatch
):
    await runs_kit.pause_sweeps(service)
    _, target_run = await target(service, scripted_model, runs_kit)
    current_definition = preset.configuration

    def legacy(model):
        config = current_definition(model).model_dump(mode="json")
        config["toolsets"]["execution"]["tools"]["get_trace_span"]["enabled"] = False
        config["execution"]["tools"]["mode"] = "auto"
        return type(current_definition(model)).model_validate(config)

    with monkeypatch.context() as old:
        old.setattr(preset, "configuration", legacy)
        head, path, original = await prepared(service, "finding-agent")
    with patch("a13n_service.runs.submit.require_message_source", new=AsyncMock()):
        accepted = await runs_kit.start_thread(service, head.json(), "Historical accepted analyst")
    backend = Backend(service, target_run["id"])
    query = backend.query
    updated = None

    async def edit_during_selection(request):
        nonlocal updated
        if updated is None:
            response = await service.client.post(
                f"{path}/revisions",
                json={
                    "config": {
                        **original["config"],
                        "instructions": "Keep the newest requirements.",
                        "model_settings": {"max_tokens": 1792, "temperature": 0.3},
                    }
                },
                headers={"If-Match": head.headers["etag"]},
            )
            assert response.status_code == 201, response.text
            updated = response.json()
        return await query(request)

    monkeypatch.setattr(backend, "query", edit_during_selection)
    runtime = replace(service.runtime, traces=backend)
    service.app.state.runtime = runtime
    body = AnalysisCreate(trace_id=TRACE)
    created, fresh = await analysis.start(
        runtime, actor(service), service.tenant.workspace_id, body, request_key="upgrade"
    )
    assert fresh
    run_agent = (await service.client.get(f"/api/v1/runs/{created.run_id}/agent")).json()
    config = run_agent["config"]
    assert config["model"] == original["config"]["model"]
    assert config["model_settings"] == {"max_tokens": 1792, "temperature": 0.3}
    assert config["instructions"] == "Keep the newest requirements."
    assert config["execution"]["tools"]["mode"] == "inline"
    assert config["toolsets"]["execution"]["tools"]["get_trace_span"] == {
        "enabled": True,
        "permission": "allow",
        "config": {},
    }
    historical = (await service.client.get(f"/api/v1/runs/{accepted['run']['id']}/agent")).json()
    assert historical["config"] == original["config"]
    # A matching revision is reused, including after selecting older user preferences.
    refreshed, _, latest = await prepared(service, "finding-agent")
    assert latest["id"] == run_agent["revision"]["id"]
    assert len((await service.client.get(f"{path}/revisions")).json()["items"]) == 3
    restored = await service.client.post(
        f"{path}/revisions/{updated['id']}/set-default", headers={"If-Match": refreshed.headers["etag"]}
    )
    assert restored.status_code == 200
    _, _, reused = await prepared(service, "finding-agent")
    assert reused["id"] == latest["id"]
    assert len((await service.client.get(f"{path}/revisions")).json()["items"]) == 3
    replay, fresh = await analysis.start(
        replace(runtime, traces=None), actor(service), service.tenant.workspace_id, body, request_key="upgrade"
    )
    assert not fresh and replay.id == created.id


async def test_generic_finder_launch_is_routed_to_managed_analysis_and_copies_remain_independent(
    service, scripted_model, runs_kit
):
    await runs_kit.create_model(service, scripted_model)
    head, path, _ = await prepared(service, "finding-agent")
    rejected = await service.client.post(
        "/api/v1/threads",
        json={"agent_id": head.json()["id"], "payload": {"content": [{"type": "text", "text": "Generic chat"}]}},
        headers={"Idempotency-Key": "generic-finder"},
    )
    assert rejected.status_code == 400, rejected.text
    assert "finding-analyses" in rejected.text
    copied = await service.client.post(f"{path}/duplicate", json={"name": "Independent analyst"})
    assert copied.status_code == 201
    scripted_model.say("Independent chat")
    started = await runs_kit.start_thread(service, copied.json(), "Chat")
    async with runs_kit.platform_runtime(service, service.runtime) as runtime:
        await (await runs_kit.attempt(service, runtime=runtime))
    run = await runs_kit.get_run(service, started["run"]["id"])
    assert run["status"] == "completed" and run["finding_analysis_id"] is None


async def test_native_runtime_navigates_child_handles_search_and_continuation(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent, target_run = await target(service, scripted_model, runs_kit)
    await scripted_model.request()  # The target's already completed model request.
    backend = Backend(service, target_run["id"])
    child = backend.root.model_copy(
        update={
            "id": "c" * 16,
            "parent_id": backend.root.id,
            "kind": "tool",
            "name": "quote",
            "input": {"package": "unspecified"},
            "output": "x" * 4500 + "unsupported-quote $12 " + "y" * 4500,
        }
    )

    async def query(request):
        backend.requests.append(request)
        spans = [backend.root] if request.roots else [backend.root, child]
        if request.span_id:
            spans = [span for span in spans if span.id == request.span_id]
        return SpanPage(items=spans, next_cursor=None)

    backend.query = query
    runtime = replace(service.runtime, traces=backend)
    service.app.state.runtime = runtime
    created, _ = await analysis.start(
        runtime, actor(service), service.tenant.workspace_id, AnalysisCreate(trace_id=TRACE), request_key="child-chain"
    )
    async with runs_kit.platform_runtime(service, runtime) as served:
        running = await runs_kit.attempt(service, runtime=served)
        try:
            await scripted_model.request()

            async def call(name, arguments, call_id):
                scripted_model.call(name, arguments, call_id=call_id)
                request = await scripted_model.request()
                returned = next(message for message in request["messages"] if message.get("tool_call_id") == call_id)
                return json.loads(returned["content"])

            root = await call("get_trace", {"trace_id": TRACE}, "root")
            page = await call("list_trace_spans", {"trace_id": TRACE}, "steps")
            assert child.id in {item["id"] for item in page["items"]}
            detail = await call("get_trace_span", {"trace_id": TRACE, "span_id": child.id}, "child")
            reference = detail["content"]["output"]
            fragment = await call("read_content", {"reference": reference, "limit": 100}, "fragment")
            assert fragment["fragment"]["has_more"] and fragment["continuation"]
            continued = await call("read_content", {"reference": fragment["continuation"], "limit": 100}, "continued")
            assert continued["fragment"]["offset"] == 100
            found = await call(
                "search_content", {"reference": reference, "query": "unsupported-quote", "limit": 200}, "search"
            )
            assert "unsupported-quote" in found["fragment"]["text"]
            body = finding(agent, target_run)
            body["evidence"][0]["span_ids"] = [root["summary"]["id"], child.id]
            row = await call("create_finding", body, "submit")
            assert row["analysis_id"] == created.id and row["source_run_id"] == created.run_id
            scripted_model.say("Child payload establishes the unsupported quote.")
            await running
            assert (await runs_kit.get_run(service, created.run_id))["status"] == "completed"
        finally:
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)
