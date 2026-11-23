"""Operations-first launch upgrades current preferences and navigates native child evidence."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from a13n_harness.tool_execution import StoredToolExecution
from a13n_service.infra.telemetry import correlation_attributes
from a13n_service.providers.traces import SpanPage
from a13n_service.runs.claim import claim
from a13n_service.runs.findings import analysis, preset
from a13n_service.runs.findings import service as findings
from a13n_service.runs.findings.schemas import AnalysisCreate
from a13n_service.runs.seal import release_attempt
from a13n_service.runs.tool_executions.store import Store

from . import test_traces
from .test_builtin_agents import prepared
from .test_findings import TRACE, Backend, actor, analysis_input, finding, reviewed, target
from .test_tool_execution_store import _record, _save
from .test_trace_search import metrics_page, roots_page
from .test_traces import langfuse_row

trace_backend = test_traces.backend
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


@pytest.fixture
def analyst_tools(service, scripted_model, runs_kit):
    """Drive a managed analysis using results returned to the model by its own platform tools."""

    @asynccontextmanager
    async def run(runtime, created):
        async def call(name, arguments, call_id):
            scripted_model.call(name, arguments, call_id=call_id)
            request = await scripted_model.request()
            returned = next(message for message in request["messages"] if message.get("tool_call_id") == call_id)
            return json.loads(returned["content"])

        async with runs_kit.platform_runtime(service, runtime) as served:
            running = await runs_kit.attempt(service, runtime=served)
            try:
                await scripted_model.request()
                yield call
                scripted_model.say("Analysis complete.")
                await running
                assert (await runs_kit.get_run(service, created.run_id))["status"] == "completed"
            finally:
                if not running.done():
                    running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    return run


async def test_native_runtime_navigates_child_handles_search_and_continuation(
    service, scripted_model, runs_kit, analyst_tools
):
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
    async with analyst_tools(runtime, created) as call:
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


async def test_finder_reads_recovered_run_and_retained_tool_result(service, scripted_model, runs_kit, analyst_tools):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run = (await runs_kit.start_thread(service, agent, "Check the shipment status"))["run"]
    (lease,) = await claim(service.runtime, worker_id="first-worker", worker_build="test", limit=1)
    store, record = Store(service.runtime), _record(lease)
    await _save(store, lease, StoredToolExecution(record))
    completed = record.model_copy(
        update={"status": "succeeded", "result_available": True, "completed_at": datetime.now(UTC)}
    )
    await _save(store, lease, StoredToolExecution(completed, {"shipment": "dispatched"}))
    await release_attempt(service.runtime, lease, status="failed")
    scripted_model.say("Shipment has left the warehouse")
    await (await runs_kit.attempt(service))
    await scripted_model.request()

    runtime = replace(service.runtime, traces=Backend(service, run["id"]))
    service.app.state.runtime = runtime
    created, _ = await analysis.start(
        runtime,
        actor(service),
        service.tenant.workspace_id,
        AnalysisCreate(trace_id=TRACE),
        request_key="recovered-run",
    )
    async with analyst_tools(runtime, created) as call:
        detail = await call("get_run", {"run_id": run["id"]}, "run")
        assert detail["summary"]["status"] == "completed"
        for section, expected in (
            ("input", "Check the shipment status"),
            ("output", "Shipment has left the warehouse"),
        ):
            content = await call("read_content", {"reference": detail["content"][section]}, section)
            assert expected in content["fragment"]["text"]
        items = await call("list_run_items", {"run_id": run["id"], "kind": "text_message"}, "items")
        assert any("Shipment has left the warehouse" in item["text"]["text"] for item in items["page"]["items"]), items[
            "page"
        ]
        attempts = await call("list_run_attempts", {"run_id": run["id"]}, "attempts")
        first, second = attempts["items"]
        assert first["status"] == "failed" and second["replaces_attempt_id"] == first["id"]
        assert (second["start_reason"], second["status"]) == ("recovery", "succeeded")
        thread_id = detail["summary"]["thread_id"]
        executions = await call("list_tool_executions", {"run_id": run["id"], "thread_id": thread_id}, "executions")
        (execution,) = executions["items"]
        assert execution["attempt_id"] == first["id"] and execution["result_available"]
        result = await call(
            "get_tool_execution_result",
            {"thread_id": thread_id, "execution_id": execution["execution_id"]},
            "result",
        )
        assert "dispatched" in result["fragment"]["text"]


async def test_finder_discovers_related_trace_and_reads_omitted_feedback(
    service, scripted_model, runs_kit, trace_backend, analyst_tools, monkeypatch
):
    await runs_kit.pause_sweeps(service)
    agent, run = await target(service, scripted_model, runs_kit)
    await scripted_model.request()
    prior = await reviewed(service, runs_kit, finding(agent, run))
    monkeypatch.setattr(findings, "CONTEXT_ITEMS", 0)
    attributes = correlation_attributes(service.tenant.organization_id, service.tenant.workspace_id, run_id=run["id"])
    now = datetime.now(UTC)

    def root(trace_id):
        return {
            **langfuse_row("b" * 16, {"attributes": attributes}, trace_id=trace_id),
            "startTime": (now - timedelta(seconds=5)).isoformat(),
            "endTime": now.isoformat(),
        }

    selected, related = root(TRACE), root("c" * 32)
    runtime = replace(service.runtime, traces=trace_backend.langfuse())
    service.app.state.runtime = runtime
    trace_backend.answer(roots_page(selected))
    created, _ = await analysis.start(
        runtime, actor(service), service.tenant.workspace_id, AnalysisCreate(trace_id=TRACE), request_key="related"
    )
    assert [item.trace_id for item in created.selected_traces] == [TRACE]
    assert json.loads(await analysis_input(service, created.run_id))["feedback"] == {"items": [], "truncated": True}
    async with analyst_tools(runtime, created) as call:
        trace_backend.answer(roots_page(selected, related))
        listed = await call("list_traces", {"run_id": run["id"]}, "list")
        assert {item["trace_id"] for item in listed["items"]} == {TRACE, related["traceId"]}
        trace_backend.answer(roots_page(related), metrics_page())
        ranked = await call(
            "search_traces",
            {
                "run_id": run["id"],
                "started_after": (now - timedelta(hours=1)).isoformat(),
                "started_before": now.isoformat(),
                "order_by": "duration",
            },
            "rank",
        )
        discovered = ranked["items"][0]["root"]["trace_id"]
        assert discovered == related["traceId"]
        history = await call("list_findings", {"agent_id": agent["id"]}, "history")
        (match,) = history["items"]
        assert match["agent_revision_id"] == run["agent_revision_id"]
        feedback = await call("get_finding", {"finding_id": match["id"]}, "review")
        assert feedback["closed"] and feedback["assessment"] == "false_positive"
        assert feedback["assessment_note"] == prior["assessment_note"]
        path = "/api/v1/finding-analyses"
        assert (await service.client.get(path)).json()["items"][0]["read_trace_ids"] == []
        trace_backend.answer(roots_page(related))
        detail = await call("get_trace", {"trace_id": discovered}, "read")
        assert detail["summary"]["trace_id"] == discovered
        assert (await service.client.get(path)).json()["items"][0]["read_trace_ids"] == [discovered]


@pytest.mark.parametrize("same_agent", [True, False])
async def test_native_submission_binds_read_trace_to_its_actual_run_and_replays_without_backend(
    service, scripted_model, runs_kit, same_agent
):
    from a13n_service.infra.errors import ServiceError
    from a13n_service.runs.findings.schemas import FindingCreate
    from a13n_service.runs.findings.service import create_finding

    await runs_kit.pause_sweeps(service)
    agent, run = await target(service, scripted_model, runs_kit)
    other = agent if same_agent else await runs_kit.add_agent(service, "Other target", "scripted")
    scripted_model.say("Other run completed")
    other_run = (await runs_kit.start_thread(service, other, "another task"))["run"]
    await (await runs_kit.attempt(service))
    runtime = replace(service.runtime, traces=Backend(service, run["id"]))
    service.app.state.runtime = runtime
    created, _ = await analysis.start(
        runtime, actor(service), service.tenant.workspace_id, AnalysisCreate(trace_id=TRACE), request_key="binding"
    )
    scripted_model.call("get_trace", {"trace_id": TRACE}, call_id="read")
    forged = finding(other, other_run)
    forged["source_key"] = "wrong-run"
    scripted_model.call("create_finding", forged, call_id="forged")
    valid = finding(agent, run)
    scripted_model.call("create_finding", valid, call_id="corrected")
    scripted_model.say("The corrected evidence belongs to the original run.")
    async with runs_kit.platform_runtime(service, runtime) as served:
        await (await runs_kit.attempt(service, runtime=served))
    assert (await runs_kit.get_run(service, created.run_id))["status"] == "completed"
    rows = (await service.client.get("/api/v1/findings")).json()["items"]
    assert len(rows) == 1 and rows[0]["evidence"][0]["run_id"] == run["id"]
    while not scripted_model.requests.empty():
        request = scripted_model.requests.get_nowait()
    returned = next(message for message in request["messages"] if message.get("tool_call_id") == "forged")
    assert "trace must belong to the named Run" in returned["content"]
    # An identical committed submission replays without a backend, but changed content still conflicts.
    offline = replace(runtime, traces=None)
    replay = await create_finding(
        offline,
        actor(service),
        service.tenant.workspace_id,
        FindingCreate.model_validate(valid),
        source_run_id=created.run_id,
    )
    assert replay.id == rows[0]["id"]
    with pytest.raises(ServiceError) as conflict:
        await create_finding(
            offline,
            actor(service),
            service.tenant.workspace_id,
            FindingCreate.model_validate({**valid, "title": "Changed diagnosis"}),
            source_run_id=created.run_id,
        )
    assert conflict.value.code == "conflict"
