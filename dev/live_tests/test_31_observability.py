"""Case 31: correlate exported spans, attempts, Items, SSE and model usage."""

import json

import pytest

from .stream import assert_stream

pytestmark = pytest.mark.anyio


def exported_spans(root):
    path = root / "workspace" / "otlp.jsonl"
    if not path.exists():
        return []
    spans = []
    for line in path.read_text().splitlines():
        for resource in json.loads(line).get("resourceSpans", []):
            for scope in resource.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    attributes = {
                        item["key"]: next(iter(item["value"].values())) for item in span.get("attributes", [])
                    }
                    spans.append({**span, "attrs": attributes, "scope": scope.get("scope", {}).get("name")})
    # OTLP retries may legitimately deliver the same span more than once.
    return list({(span["traceId"], span["spanId"]): span for span in spans}.values())


@pytest.mark.parametrize("export_fails", [False, True])
async def test_cross_layer_evidence_and_telemetry_failure_does_not_change_result(management, export_fails):
    journey, live = management, management.live
    await journey.lab.stop(journey.lab.workers[0])
    journey.lab.worker_environment.update(
        {
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_TRACES_SAMPLER": "always_on",
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": live.config["control_url"] + "/__live__/otlp/v1/traces",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "Authorization=Bearer%20" + live.config["token"],
            "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT": "1",
            "OTEL_BSP_SCHEDULE_DELAY": "100",
            "OTEL_BSP_EXPORT_TIMEOUT": "1000",
        }
    )
    if export_fails:
        (journey.lab.root / "workspace" / "telemetry_fail").touch()
    await journey.lab.start_worker()
    case = await journey.case()
    journey.plan(
        case, steps=[{"tool": "live_effect", "arguments": {"case_id": case["case_id"], "token": case["token"]}}]
    )
    receipt = await journey.start(case)
    result = await live.finish(receipt["run_id"])
    assert case["token"] in result["output_text"]
    attempts = await journey.lab.attempts(result["id"])
    assert len(attempts) == 1 and attempts[0]["status"] == "succeeded"
    assert attempts[0]["harness_run_id"]
    assert (await live.evidence(case))["effects"] == 1
    events = await live.events(result["id"])
    assert_stream(events, result["id"])
    completed = [event for event in events if event.kind == "run.completed"]
    assert len(completed) == 1
    usage = completed[0].data["payload"]["data"]["usage"]
    assert usage["model_requests"] == 2
    items = await live.collection(f"/api/v1/runs/{result['id']}/items")
    deltas = [
        event["payload"]["delta"]
        for item in items
        for event in item["content"]["events"]
        if event["event_type"] == "agui.text_message_content"
    ]
    assert "".join(deltas) == result["output_text"]
    assert case["token"] in json.dumps(items)

    async def spans():
        return {
            "spans": [
                span
                for span in exported_spans(journey.lab.root)
                if span["attrs"].get("a13n.service.run.id") == result["id"]
            ]
        }

    exported = (
        await live.wait(
            spans,
            lambda value: any(span["name"] == "a13n.service.run_attempt" for span in value["spans"]),
            "OTLP Attempt root",
        )
    )["spans"]
    root = next(span for span in exported if span["name"] == "a13n.service.run_attempt")
    harness = next(span for span in exported if span["name"] == "harness.run")
    assert root["attrs"]["a13n.run_attempt.outcome"] == "succeeded"
    assert harness["attrs"]["a13n.run.id"] == attempts[0]["harness_run_id"]
    assert all(span["traceId"] == root["traceId"] for span in exported)
    by_id = {span["spanId"]: span for span in exported}
    for span in exported:
        assert span["attrs"]["a13n.run_attempt.id"] == attempts[0]["id"]
        assert span["attrs"]["a13n.thread.id"] == result["thread_id"]
        if span != root:
            assert span["parentSpanId"] in by_id, "A cross-layer span lost its parent"
    chats = [span for span in exported if span["attrs"].get("gen_ai.operation.name") == "chat"]
    assert len(chats) == usage["model_requests"]
    assert sum(int(span["attrs"]["gen_ai.usage.input_tokens"]) for span in chats) == 20
    assert sum(int(span["attrs"]["gen_ai.usage.output_tokens"]) for span in chats) == 20
    assert any(span["attrs"].get("gen_ai.operation.name") == "execute_tool" for span in exported)
    assert live.config["token"] not in json.dumps(exported)
    assert await live.run(result["id"]) == result
