"""Queue edits compete with prepared handoffs and retained relational recovery."""

import asyncio
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def test_late_steer_invalidates_prepared_queue_handoff_until_source_drains_inbox(control):
    journey, live = control, control.live
    case = await journey.case(effect=True)
    model = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=2)
    source = await journey.start(case)
    await journey.reached(model)
    later = await journey.case()
    queued = await journey.queue(source, later)
    handoff = journey.arm("handoff", "queue.before_commit", run_id=source["run_id"])
    journey.release(model)
    hit = await journey.reached(handoff)
    steer, token = await journey.steer(source["run_id"])
    assert (await journey.queue_row(queued))["state"] == "queued"
    journey.release(handoff)
    result = await live.finish(source["run_id"])
    assert result["output_text"] == "STEERS:" + token
    attempts = await journey.lab.attempts(result["id"])
    assert len(attempts) == 2 and attempts[1]["start_reason"] == "pending_input"
    row = (await journey.inbox(source["thread_id"]))[0]
    assert row["id"] == steer["steer_id"] and row["status"] == "consumed" and row["consumed_by_run_id"] == result["id"]
    following = await journey.finish_queue(queued)
    assert following["parent_run_id"] == result["id"] and following["output_text"] == later["token"]
    assert (await live.http.get(f"/api/v1/runs/{hit['successor_run_id']}")).status_code == 404
    assert len(await journey.thread_runs(source)) == 2 and journey.effects(case) == [case["token"]]


async def mutation(journey, source, rows, operation, replacement):
    first = rows[0]
    path = f"/api/v1/queued-submissions/{first['queued_submission_id']}"
    if operation == "patch":
        return (
            "PATCH",
            path,
            {
                "expected_version": first["version"],
                "submission": {
                    **first["submission"],
                    "input": journey.live.start_body(replacement)["input"],
                },
            },
        )
    if operation == "delete":
        return "DELETE", path, {"expected_version": first["version"]}
    thread = await journey.live.thread(source["thread_id"])
    return (
        "POST",
        f"/api/v1/threads/{source['thread_id']}/queued-submissions/reorder",
        {
            "expected_queue_version": thread["queue_version"],
            "queued_submission_ids": [row["queued_submission_id"] for row in reversed(rows)],
        },
    )


@pytest.mark.parametrize("operation", ["patch", "delete", "reorder"])
@pytest.mark.parametrize("winner", ["mutation", "consumption"])
async def test_queue_mutation_and_completion_handoff_use_one_committed_intent(control, operation, winner):
    journey, live = control, control.live
    case = await journey.case(effect=True)
    model = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=2)
    source = await journey.start(case)
    await journey.reached(model)
    cases = [await journey.case(), await journey.case()]
    rows = [await journey.queue(source, later) for later in cases]
    replacement = await journey.case()
    method, path, body = await mutation(journey, source, rows, operation, replacement)
    handoff = (
        journey.arm("handoff", "queue.before_commit", run_id=source["run_id"])
        if winner == "mutation"
        else journey.arm("consumed-model", "model.request", role="control", case_id=cases[0]["case_id"], request=1)
    )
    recovery = journey.arm("scanner", "control.queue_recovery", role="control", thread_id=source["thread_id"])
    journey.release(model)
    hit = await journey.reached(handoff)
    before = await live.thread(source["thread_id"])
    key = uuid4().hex
    payload = {"params": body} if method == "DELETE" else {"json": body}
    response = await live.http.request(method, path, **payload, headers={"Idempotency-Key": key})
    success = 204 if method == "DELETE" else 200
    assert response.status_code == (success if winner == "mutation" else 409), response.text
    if winner == "mutation":
        after = await live.thread(source["thread_id"])
        assert after["version"] == before["version"] and after["current_run_id"] == source["run_id"]
        assert after["queue_version"] == before["queue_version"] + 1
        replay = await live.http.request(method, path, **payload, headers={"Idempotency-Key": key})
        assert replay.status_code == success and replay.content == response.content
        if method == "DELETE":
            assert response.content == b""
        assert (await live.http.get(f"/api/v1/runs/{hit['successor_run_id']}")).status_code == 404
    else:
        assert await live.thread(source["thread_id"]) == before
        consumed = await journey.queue_row(rows[0])
        assert consumed["state"] == "consumed" and consumed["consumed_run_id"] == before["current_run_id"]
    journey.release(handoff)
    journey.release(recovery)
    parent = await live.finish(source["run_id"])
    expected = list(zip(rows, cases, strict=True))
    if winner == "mutation":
        if operation == "patch":
            expected[0] = (rows[0], replacement)
        elif operation == "delete":
            expected = expected[1:]
        else:
            expected.reverse()
    for row, expected_case in expected:
        result = await journey.finish_queue(row)
        assert result["output_text"] == expected_case["token"] and result["parent_run_id"] == parent["id"]
        parent = result
    assert len(await journey.thread_runs(source)) == len(expected) + 1
    assert journey.effects(case) == [case["token"]]
    if winner == "mutation" and operation in {"patch", "delete"}:
        assert journey.observations(cases[0]) == [], "A stale prepared queue input was executed"
    if winner == "mutation" and operation == "delete":
        assert (await live.http.get(f"/api/v1/queued-submissions/{rows[0]['queued_submission_id']}")).status_code == 404


@pytest.mark.parametrize("same_key", [False, True])
async def test_explicit_consumers_and_background_recovery_accept_one_queued_run(control, same_key):
    journey, live, lab = control, control.live, control.lab
    case = await journey.case()
    model = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=1)
    source = await journey.start(case)
    await journey.reached(model)
    later = await journey.case()
    row = await journey.queue(source, later)
    recovery = journey.arm(
        "recovery", "control.queue_recovery", role="control", times=100, thread_id=source["thread_id"]
    )
    await journey.post(*await journey.command(source, "interrupt"), expected=202)
    journey.release(model)
    cancelled = await live.finish(source["run_id"], "cancelled")
    await lab.stop(lab.workers[0])
    thread = await live.thread(source["thread_id"])
    path = f"/api/v1/threads/{source['thread_id']}/queued-submissions/consume"
    body = {"expected_thread_version": thread["version"], "expected_queue_version": thread["queue_version"]}
    keys = [uuid4().hex, uuid4().hex]
    if same_key:
        keys[1] = keys[0]
    replies = await journey.race([(path, body, key) for key in keys])
    assert sorted(reply.status_code for reply in replies) == ([202, 202] if same_key else [202, 409]), [
        reply.text for reply in replies
    ]
    accepted = next(reply.json() for reply in replies if reply.status_code == 202)
    if same_key:
        assert replies[0].json() == replies[1].json()
    assert accepted["queued_submission"]["consumed_run_id"] == accepted["run"]["run_id"]
    assert (await journey.queue_row(row))["state"] == "consumed"
    live.track(accepted["run"])
    journey.release(recovery)
    await lab.start_worker()
    result = await live.finish(accepted["run"]["run_id"])
    assert result["lineage_kind"] == "root" and result["parent_run_id"] is None
    assert result["output_text"] == later["token"] and len(await journey.thread_runs(source)) == 2
    assert await live.run(cancelled["id"]) == cancelled
    assert len(await lab.attempts(result["id"])) == 1


async def test_concurrent_enqueue_allocates_fifo_positions_without_advancing_thread(control):
    journey, live = control, control.live
    _, waiting = await journey.waiting()
    before = await live.thread(waiting["thread_id"])
    cases = [await journey.case() for _ in range(4)]
    path = f"/api/v1/threads/{waiting['thread_id']}/runs"
    bodies = [{"expected_thread_version": before["version"], "input": live.start_body(case)["input"]} for case in cases]
    keys = [uuid4().hex for _ in cases]
    commands = [*zip(bodies, keys, strict=True), (bodies[0], keys[0])]
    responses = await asyncio.gather(
        *(live.http.post(path, json=body, headers={"Idempotency-Key": key}) for body, key in commands)
    )
    assert all(response.status_code == 202 for response in responses), [response.text for response in responses]
    first, repeated = responses[0].json(), responses[-1].json()
    assert first["outcome"] == repeated["outcome"] == "queued"
    assert first["queued_submission"] == repeated["queued_submission"]
    rows = await journey.queued(waiting)
    assert [row["position"] for row in rows] == [1, 2, 3, 4]
    assert len({row["queued_submission_id"] for row in rows}) == 4
    after = await live.thread(waiting["thread_id"])
    assert after["version"] == before["version"] and after["queue_version"] == before["queue_version"] + 4
    assert after["current_run_id"] == after["head_run_id"] == waiting["id"]
    assert await journey.thread_runs(waiting) == [waiting]
    replay = await journey.post(path, bodies[1], key=keys[0], expected=202)
    assert replay == {**first, "queue_version": after["queue_version"]}
    successor = await journey.accept(*await journey.command(waiting, "feedback"))
    parent = await live.finish(successor["run_id"])
    for row in rows:
        result = await journey.finish_queue(row)
        assert {"structured_content": None, **result["input"]} == row["submission"]["input"]
        assert result["parent_run_id"] == parent["id"]
        parent = result


async def test_queue_stale_versions_and_invalid_reorders_leave_intent_unchanged(control):
    journey, live = control, control.live
    _, waiting = await journey.waiting()
    rows = [await journey.queue(waiting, await journey.case()) for _ in range(2)]
    thread = await live.thread(waiting["thread_id"])
    path = f"/api/v1/queued-submissions/{rows[0]['queued_submission_id']}"
    for method, body in [
        ("PATCH", {"expected_version": rows[0]["version"] + 1, "submission": rows[0]["submission"]}),
        ("DELETE", {"expected_version": rows[0]["version"] + 1}),
    ]:
        payload = {"params": body} if method == "DELETE" else {"json": body}
        await live.request(method, path, expected=409, **payload, headers={"Idempotency-Key": uuid4().hex})
    reorder = f"/api/v1/threads/{waiting['thread_id']}/queued-submissions/reorder"
    for ids, version, status in [
        ([row["queued_submission_id"] for row in rows], thread["queue_version"] + 1, 409),
        ([rows[0]["queued_submission_id"]], thread["queue_version"], 409),
        ([rows[0]["queued_submission_id"]] * 2, thread["queue_version"], 400),
    ]:
        await journey.post(reorder, {"expected_queue_version": version, "queued_submission_ids": ids}, expected=status)
        assert await journey.queued(waiting) == rows
        assert await live.thread(thread["id"]) == thread
        assert await journey.thread_runs(waiting) == [waiting]
