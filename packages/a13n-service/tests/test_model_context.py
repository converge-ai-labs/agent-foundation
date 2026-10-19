"""The IDs sent to models resolve to Service Runs, including inline and continued execution."""

import json
import re

import pytest

pytestmark = pytest.mark.anyio


def _contexts(request: dict, tag: str) -> list[dict]:
    texts = []
    for message in request["messages"]:
        if message["role"] != "user":
            continue
        content = message["content"]
        texts.append(content if isinstance(content, str) else "\n".join(p.get("text", "") for p in content))
    return [json.loads(value) for value in re.findall(rf'<{tag} source="[^"]+">\n([^\n]+)\n', "\n".join(texts))]


async def test_service_identity_reaches_root_inline_child_and_followup(executing, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    agent = await runs_kit.delegating(executing, scripted_model, "inline")
    scripted_model.call("delegate", {"subagent": "helper", "prompt": "compute"}, call_id="call_d", to="coordinator")
    scripted_model.say("42", to="worker")
    scripted_model.say("The helper said 42", to="coordinator")
    submitted = await runs_kit.start_thread(executing, agent, "ask the helper")
    run = await runs_kit.sealed(executing, submitted["run"]["id"])
    assert run["status"] == "completed", run
    requests = [await scripted_model.request() for _ in range(3)]
    harness_ids = []
    for request in requests:
        service_context = _contexts(request, "service-context")[-1]
        harness_context = _contexts(request, "agent-context")[-1]
        assert service_context == {"service_run_id": run["id"]}
        assert "run_id" not in harness_context
        harness_ids.append(harness_context["harness_run_id"])
        assert harness_ids[-1] != service_context["service_run_id"]
    # The inline child has its own Harness execution, but queries the containing Service Run.
    assert harness_ids[0] == harness_ids[2] and harness_ids[1] != harness_ids[0]
    projected_run_id = _contexts(requests[0], "service-context")[-1]["service_run_id"]
    assert (await runs_kit.get_run(executing, projected_run_id))["id"] == run["id"]
    attempts = (await executing.client.get(f"{executing.api}/runs/{projected_run_id}/attempts")).json()["items"]
    assert attempts[0]["harness_run_id"] == harness_ids[0]
    assert (await executing.client.get(f"{executing.api}/runs/{harness_ids[0]}")).status_code == 404

    scripted_model.say("Follow-up answer", to="coordinator")
    response = await runs_kit.submit(executing, run["thread_id"], runs_kit.message(agent, "follow up"))
    assert response.status_code == 201, response.text
    followup = await scripted_model.request()
    contexts = _contexts(followup, "service-context")
    assert contexts[0] == {"service_run_id": run["id"]}
    current_run_id = contexts[-1]["service_run_id"]
    assert current_run_id != run["id"]
    current = await runs_kit.sealed(executing, current_run_id)
    assert current["status"] == "completed" and current["thread_id"] == run["thread_id"]
    assert _contexts(followup, "agent-context")[-1]["harness_run_id"] not in harness_ids
