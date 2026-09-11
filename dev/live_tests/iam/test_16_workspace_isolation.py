"""Case 16: same-Organization identities cannot cross Workspace resource boundaries."""

from uuid import uuid4

import httpx2
import pytest

from ..infrastructure.client import agent_input

pytestmark = pytest.mark.anyio


async def test_other_workspace_cannot_read_stream_or_control_run(round_two):
    lab, live = round_two, round_two.client
    case = await live.case("gate")
    receipt = await live.start(case)
    run_id, thread_id = receipt["run_id"], receipt["thread_id"]
    await lab.wait_evidence(case, "gate_ready", run_id=run_id)
    original = await live.run(run_id)
    thread = await live.thread(thread_id)
    other = live.config["other_identity"]
    async with httpx2.AsyncClient(
        base_url=live.config["control_url"],
        headers={"Authorization": "Bearer " + other["token"]},
        timeout=5,
        trust_env=False,
        follow_redirects=False,
    ) as outsider:
        # A successful own-Workspace query proves the denial is not just invalid authentication.
        own = await outsider.get(f"/api/v1/workspaces/{other['workspace_id']}/sessions")
        assert own.status_code == 200 and own.json()["items"] == []
        for path in (
            f"/api/v1/runs/{run_id}",
            f"/api/v1/runs/{run_id}/items",
            f"/api/v1/runs/{run_id}/attempts",
            f"/api/v1/threads/{thread_id}",
        ):
            response = await outsider.get(path)
            assert response.status_code in {403, 404}, (path, response.status_code)
            assert case["token"] not in response.text
        async with outsider.stream(
            "GET", f"/api/v1/runs/{run_id}/stream", headers={"Accept": "text/event-stream"}
        ) as response:
            assert response.status_code in {403, 404}
            assert "text/event-stream" not in response.headers.get("content-type", "")
        commands = {
            "interrupt": {"expected_run_version": original["version"], "expected_thread_version": thread["version"]},
            "steer": agent_input("Unauthorized change"),
            "continue": {
                "expected_thread_version": thread["version"],
                "input": agent_input("Unauthorized continuation"),
            },
            "fork": {"input": agent_input("Unauthorized fork")},
        }
        for action, body in commands.items():
            response = await outsider.post(
                f"/api/v1/runs/{run_id}/{action}", json=body, headers={"Idempotency-Key": uuid4().hex}
            )
            assert response.status_code in {403, 404}, (action, response.status_code)
    assert (await live.run(run_id))["status"] == "running"
    assert await live.thread(thread_id) == thread
    assert len(await live.collection(f"/api/v1/threads/{thread_id}/runs")) == 1
    await live.release(case)
    assert (await live.finish(run_id))["output_text"] == case["token"]
