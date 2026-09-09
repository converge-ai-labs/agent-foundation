"""Retained conversations produced by real local Service execution."""

from uuid import uuid4

import anyio

from .seed_client import Client


async def run(
    client: Client,
    base: str,
    agent: str,
    prompt: str,
    *,
    previous: dict | None = None,
    environment_id: str | None = None,
    asset_id: str | list[str] | None = None,
    expected: str | None = None,
) -> dict:
    body = {"agent_id": agent, "input": {"schema_version": "2", "content": [{"type": "text", "text": prompt}]}}
    if environment_id:
        body["environment"] = {"environment_id": environment_id}
    if asset_id:
        for value in [asset_id] if isinstance(asset_id, str) else asset_id:
            body["input"]["content"].append(
                {"type": "binary", "source": {"type": "asset", "asset_id": value}, "delivery": "environment_path"}
            )
    path = base + "/runs"
    if previous:
        # Ordinary continuation; the thread version comes from the current server state.
        thread = await client.request("GET", f"/api/v1/threads/{previous['thread_id']}")
        path = f"/api/v1/runs/{previous['id']}/continue"
        body = {"input": body["input"], "expected_thread_version": thread["version"]}
    receipt = await client.request("POST", path, expected=202, json=body)
    return await finish(client, receipt["run_id"], expected or ("failed" if "[fail]" in prompt else "completed"))


async def finish(client: Client, run_id: str, expected: str = "completed") -> dict:
    with anyio.fail_after(90):
        while True:
            result = await client.request("GET", f"/api/v1/runs/{run_id}")
            if result["status"] in {"completed", "failed", "cancelled", "waiting"} and result.get("sealed_at"):
                if result["status"] != expected:
                    raise RuntimeError(f"Seed Run ended as {result['status']}; expected {expected}")
                return result
            await anyio.sleep(0.1)


def text_input(prompt: str) -> dict:
    return {"schema_version": "2", "content": [{"type": "text", "text": prompt}]}


async def journeys(client: Client, base: str, resources: dict, completed: dict) -> dict:
    scenarios = {}
    agent = resources["agents"][3]  # This Agent intentionally has no Environment-dependent Skills.
    empty = await client.request("POST", base + "/threads", expected=201, json={"agent_id": agent})
    scenarios["empty_thread"] = empty["id"]
    failed = await run(client, base, agent, "[fail] This fictional provider request intentionally fails.")
    scenarios["failed_run"] = failed["id"]
    thread = await client.request("GET", f"/api/v1/threads/{failed['thread_id']}")
    receipt = await client.request(
        "POST", f"/api/v1/runs/{failed['id']}/retry", expected=202, json={"expected_thread_version": thread["version"]}
    )
    retried = await finish(client, receipt["run_id"], "failed")
    scenarios["failed_retry"] = retried["id"]
    receipt = await client.request(
        "POST",
        f"/api/v1/runs/{completed['id']}/fork",
        expected=202,
        json={"input": text_input("[long] Alternative proposal: keep this branch separate from the original review.")},
    )
    fork = await finish(client, receipt["run_id"])
    scenarios["forked_run"] = fork["id"]
    if fork["thread_id"] == completed["thread_id"]:
        raise RuntimeError("Fork scenario did not create a separate Thread")

    for resolve in (False, True):
        waiting = await run(
            client,
            base,
            resources["scenarios"]["agent_client_tool"],
            "[client] Please review the fictional rollout checklist.",
            expected="waiting",
        )
        pending = await client.request("GET", f"/api/v1/runs/{waiting['id']}/pending-actions")
        if len(pending["items"]) != 1 or pending["items"][0]["kind"] != "client_tool":
            raise RuntimeError("Waiting scenario did not retain its client-tool request")
        scenarios["waiting_for_client" if not resolve else "feedback_source"] = waiting["id"]
        if resolve:
            thread = await client.request("GET", f"/api/v1/threads/{waiting['thread_id']}")
            receipt = await client.request(
                "POST",
                f"/api/v1/runs/{waiting['id']}/feedback",
                expected=202,
                json={
                    "expected_thread_version": thread["version"],
                    "sealed_state_digest_sha256": waiting["sealed_state_digest_sha256"],
                    "resolutions": [
                        {
                            "call_id": pending["items"][0]["call_id"],
                            "action": "complete",
                            "result": {"decision": "approved-for-local-demo", "reason": "Fictional client feedback"},
                        }
                    ],
                },
            )
            resolved = await finish(client, receipt["run_id"])
            if "approved-for-local-demo" not in resolved["output_text"]:
                raise RuntimeError("Client feedback was not incorporated into the resumed output")
            scenarios["feedback_completed"] = resolved["id"]

    for queue in (False, True):
        interrupted = await interrupt_journey(client, base, agent, queue=queue)
        scenarios["interrupted_with_queue" if queue else "interrupted_run"] = interrupted["id"]
        if not queue:
            thread = await client.request("GET", f"/api/v1/threads/{interrupted['thread_id']}")
            receipt = await client.request(
                "POST",
                f"/api/v1/runs/{interrupted['id']}/retry",
                expected=202,
                json={"expected_thread_version": thread["version"]},
            )
            resumed = await finish(client, receipt["run_id"])
            scenarios["interrupted_retry_completed"] = resumed["id"]
    return scenarios


async def interrupt_journey(client: Client, base: str, agent: str, *, queue: bool) -> dict:
    receipt = await client.request(
        "POST",
        base + "/runs",
        expected=202,
        json={"agent_id": agent, "input": text_input("[interruptible] Stop this local draft before completion.")},
    )
    with anyio.fail_after(30):
        while True:
            active = await client.request("GET", f"/api/v1/runs/{receipt['run_id']}")
            if active["status"] == "running":
                break
            await anyio.sleep(0.05)
    thread = await client.request("GET", f"/api/v1/threads/{active['thread_id']}")
    if queue:
        await client.request(
            "POST",
            f"/api/v1/threads/{thread['id']}/runs",
            expected=202,
            json={
                "expected_thread_version": thread["version"],
                "input": text_input("Queued fictional follow-up: use a smaller release scope."),
            },
        )
        thread = await client.request("GET", f"/api/v1/threads/{thread['id']}")
    for _ in range(10):
        active = await client.request("GET", f"/api/v1/runs/{active['id']}")
        thread = await client.request("GET", f"/api/v1/threads/{thread['id']}")
        response = await client.http.post(
            f"/api/v1/runs/{active['id']}/interrupt",
            headers={"Idempotency-Key": uuid4().hex},
            json={"expected_run_version": active["version"], "expected_thread_version": thread["version"]},
        )
        if response.status_code == 202:
            break
        if response.status_code not in {409, 412}:
            raise RuntimeError(f"Interrupt scenario failed: HTTP {response.status_code}")
        await anyio.sleep(0.05)
    else:
        raise RuntimeError("Interrupt scenario could not obtain current versions")
    interrupted = await finish(client, active["id"], "cancelled")
    if queue:
        queued = await client.request("GET", f"/api/v1/threads/{thread['id']}/queued-submissions")
        if len(queued["items"]) != 1:
            raise RuntimeError("Interrupted conversation lost its queued follow-up")
    return interrupted
