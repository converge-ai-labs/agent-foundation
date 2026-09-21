"""Live scans respect waiting, FIFO blockers, capacity and immutable consumption."""

import asyncio
from uuid import uuid4

import pytest

from .control_support import assert_absent

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("current_outcome", ["waiting", "failed", "cancelled"])
async def test_running_recovery_scans_leave_waiting_head_queue_untouched_until_explicit_progress(
    control, current_outcome
):
    journey, live = control, control.live
    case, waiting = await journey.waiting()
    cases = [await journey.case(), await journey.case()]
    rows = [await journey.queue(waiting, later) for later in cases]
    current = waiting
    if current_outcome != "waiting":
        if current_outcome == "failed":
            journey.plan(case, failure="401", failures=100)
        model = journey.arm("feedback", "model.request", role="control", case_id=case["case_id"], request=2)
        receipt = await journey.accept(
            *await journey.command(waiting, "feedback", resolutions=await journey.approve(waiting))
        )
        await journey.reached(model)
        if current_outcome == "cancelled":
            await journey.post(*await journey.command(receipt, "interrupt"), expected=202)
        journey.release(model)
        current = await live.finish(receipt["run_id"], current_outcome)
    before = await live.thread(waiting["thread_id"])
    runs = await journey.thread_runs(waiting)
    assert before["current_run_id"] == current["id"] and before["head_run_id"] == waiting["id"]
    # Observe completed scans, rather than disabling the task that must reject
    # this Thread. Three real scan cycles must find no eligible queue work.
    scans = journey.arm("waiting-scans", "queue.scanned", role="control", action="observe", times=3)
    for hit in range(1, 4):
        evidence = await journey.reached(scans, hit=hit)
        assert evidence["examined"] == evidence["completed"] == evidence["deferred"] == 0
        assert await journey.queued(waiting) == rows
        assert await live.thread(waiting["thread_id"]) == before
        assert await journey.thread_runs(waiting) == runs
        assert all(journey.observations(later) == [] for later in cases)
    await journey.post(*await journey.command(current, "consume"), expected=409)
    assert await journey.queued(waiting) == rows and await live.thread(waiting["thread_id"]) == before
    if current_outcome == "waiting":
        successor = await journey.accept(*await journey.command(waiting, "feedback"))
    else:
        journey.plan(case)
        successor = await journey.accept(*await journey.command(current, "retry"))
    parent = await live.finish(successor["run_id"])
    for row, later in zip(rows, cases, strict=True):
        result = await journey.finish_queue(row)
        assert result["parent_run_id"] == parent["id"] and result["output_text"] == later["token"]
        parent = result
    assert await live.run(waiting["id"]) == waiting and await live.run(current["id"]) == current
    assert len(await journey.thread_runs(waiting)) == len(runs) + 3


@pytest.mark.parametrize("repair", ["patch", "delete"])
async def test_recoverable_queue_head_blocks_its_tail_but_not_other_threads(control, repair):
    journey, live = control, control.live
    agent = await journey.agent(instructions="FIRST_REVISION")
    case = await journey.case()
    model = journey.arm("blocked-source", "model.request", role="control", case_id=case["case_id"], request=1)
    source = await journey.start(case, agent_id=agent["agent"]["id"])
    await journey.reached(model)
    cases = [await journey.case(), await journey.case()]
    rows = [
        await journey.queue(source, cases[0], expected_default_revision_id=agent["revision"]["id"]),
        await journey.queue(source, cases[1]),
    ]
    changed = await journey.revision(agent["agent"], instructions="SECOND_REVISION")
    journey.release(model)
    completed = await live.finish(source["run_id"])
    before = await live.thread(source["thread_id"])
    deferred = journey.arm(
        "blocked-scans",
        "queue.consume_finished",
        role="control",
        action="observe",
        times=3,
        consumer="recovery",
        thread_id=source["thread_id"],
        succeeded=False,
    )
    # Cancellation excludes completion-time handoff: this other Thread must
    # progress through the same periodic recovery task as the blocked Thread.
    other_case = await journey.case()
    other_model = journey.arm("other-source", "model.request", role="control", case_id=other_case["case_id"], request=1)
    other = await journey.start(other_case)
    await journey.reached(other_model)
    other_later = await journey.case(effect=True)
    other_row = await journey.queue(other, other_later)
    await journey.post(*await journey.command(other, "interrupt"), expected=202)
    journey.release(other_model)
    await live.finish(other["run_id"], "cancelled")
    other_finished = await journey.finish_queue(other_row)
    assert other_finished["parent_run_id"] is None
    assert other_later["token"] in other_finished["output_text"]
    assert journey.effects(other_later) == [other_later["token"]]
    await journey.reached(deferred, hit=3)
    assert await journey.queued(source) == rows
    assert await live.thread(source["thread_id"]) == before
    assert await journey.thread_runs(source) == [completed]
    assert all(journey.observations(later) == [] for later in cases)
    # Explicit consumption must preserve the same repairable head as recovery scans.
    await journey.post(
        f"/api/v1/threads/{source['thread_id']}/queued-submissions/consume",
        {"expected_thread_version": before["version"], "expected_queue_version": before["queue_version"]},
        expected=409,
    )
    assert await journey.queued(source) == rows
    assert await live.thread(source["thread_id"]) == before
    assert await journey.thread_runs(source) == [completed]
    assert all(journey.observations(later) == [] for later in cases)
    path = f"/api/v1/queued-submissions/{rows[0]['queued_submission_id']}"
    if repair == "patch":
        await live.request(
            "PATCH",
            path,
            json={
                "expected_version": rows[0]["version"],
                "submission": {**rows[0]["submission"], "expected_default_revision_id": changed["revision"]["id"]},
            },
            headers={"Idempotency-Key": uuid4().hex},
        )
        expected = list(zip(rows, cases, strict=True))
    else:
        deleted = await live.http.delete(
            path,
            params={"expected_version": rows[0]["version"]},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert deleted.status_code == 204 and deleted.content == b""
        assert (await live.http.get(path)).status_code == 404
        expected = [(rows[1], cases[1])]
    parent = completed
    for row, later in expected:
        result = await journey.finish_queue(row)
        assert result["parent_run_id"] == parent["id"] and result["output_text"] == later["token"]
        assert result["agent_revision_id"] == changed["revision"]["id"]
        parent = result
    assert len(await journey.thread_runs(source)) == len(expected) + 1
    if repair == "delete":
        assert journey.observations(cases[0]) == []


@pytest.mark.parametrize("control", [{"queue": {"max_queued": 2}}], indirect=True)
@pytest.mark.parametrize("release_capacity", ["delete", "consume"])
async def test_queue_last_slot_has_one_winner_and_released_capacity_accepts_rejected_intent(control, release_capacity):
    journey, live = control, control.live
    _, waiting = await journey.waiting()
    cases = [await journey.case() for _ in range(3)]
    path, initial_body = await journey.command(waiting, "submit", case=cases[0])
    initial_key = uuid4().hex
    initial = await journey.post(path, initial_body, key=initial_key, expected=202)
    prefix = initial["queued_submission"]
    before = await live.thread(waiting["thread_id"])
    commands = [await journey.command(waiting, "submit", case=case) for case in cases[1:]]
    keys = [uuid4().hex, uuid4().hex]
    admission = journey.arm("last-slot", "queue.enqueue_prepared", role="control", times=2, thread_id=before["id"])
    async with (
        journey.post_in_flight(*commands[0], key=keys[0]) as first,
        journey.post_in_flight(*commands[1], key=keys[1]) as second,
    ):
        await journey.reached(admission, hit=2)
        assert not first.done() and not second.done()
        assert await journey.queued(waiting) == [prefix]
        journey.release(admission)
        replies = await asyncio.gather(first, second)
    assert sorted(reply.status_code for reply in replies) == [202, 409], [reply.text for reply in replies]
    winner = next(index for index, reply in enumerate(replies) if reply.status_code == 202)
    loser = 1 - winner
    admitted = replies[winner].json()["queued_submission"]
    rows = await journey.queued(waiting)
    assert [row["queued_submission_id"] for row in rows] == [
        prefix["queued_submission_id"],
        admitted["queued_submission_id"],
    ]
    assert [row["position"] for row in rows] == [1, 2]
    full = await live.thread(before["id"])
    assert full["version"] == before["version"] and full["queue_version"] == before["queue_version"] + 1
    assert await journey.thread_runs(waiting) == [waiting]
    assert await journey.post(path, initial_body, key=initial_key, expected=202) == {
        **initial,
        "queue_version": full["queue_version"],
    }
    assert await journey.post(*commands[winner], key=keys[winner], expected=202) == replies[winner].json()
    assert await live.thread(before["id"]) == full
    feedback = None
    if release_capacity == "delete":
        deleted = await live.http.delete(
            f"/api/v1/queued-submissions/{prefix['queued_submission_id']}",
            params={"expected_version": prefix["version"]},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert deleted.status_code == 204 and deleted.content == b""
        expected = []
    else:
        model = journey.arm("prefix-model", "model.request", role="control", case_id=cases[0]["case_id"], request=1)
        feedback = await journey.accept(*await journey.command(waiting, "feedback"))
        await journey.reached(model)
        await journey.consumed_queue(prefix)
        expected = [(prefix, cases[0])]
    available = await live.thread(before["id"])
    assert len(await journey.queued(waiting)) == 1
    retry_body = {**commands[loser][1], "expected_thread_version": available["version"]}
    retried = await journey.post(path, retry_body, key=keys[loser], expected=202)
    assert retried["outcome"] == "queued" and retried["queued_submission"]["position"] == 2
    assert (await live.thread(before["id"]))["queue_version"] == available["queue_version"] + 1
    expected += [(admitted, cases[winner + 1]), (retried["queued_submission"], cases[loser + 1])]
    if feedback is None:
        feedback = await journey.accept(*await journey.command(waiting, "feedback"))
    else:
        journey.release(model)
    parent = await live.finish(feedback["run_id"])
    for row, case in expected:
        result = await journey.finish_queue(row)
        assert result["parent_run_id"] == parent["id"] and result["output_text"] == case["token"]
        assert len(journey.observations(case)) == 1
        parent = result
    assert len(await journey.thread_runs(waiting)) == len(expected) + 2
    if release_capacity == "delete":
        assert journey.observations(cases[0]) == []


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
@pytest.mark.parametrize("head", ["none", "completed"])
async def test_consumed_queue_entry_never_requeues_when_its_run_terminates_and_control_restarts(control, outcome, head):
    journey, live = control, control.live
    case = await journey.case()
    source_model = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=1)
    source = await journey.start(case)
    await journey.reached(source_model)
    first_case = await journey.case(**({"failure": "401", "failures": 100} if outcome == "failed" else {}))
    later = await journey.case()
    first = await journey.queue(source, first_case)
    second = await journey.queue(source, later)
    first_model = journey.arm("first", "model.request", role="control", case_id=first_case["case_id"], request=1)
    if head == "none":
        await journey.post(*await journey.command(source, "interrupt"), expected=202)
    journey.release(source_model)
    parent = await live.finish(source["run_id"], "cancelled" if head == "none" else "completed")
    await journey.reached(first_model)
    consumed = await journey.consumed_queue(first)
    first_run = await live.run(consumed["consumed_run_id"])
    expected_parent = parent["id"] if head == "completed" else None
    assert first_run["parent_run_id"] == expected_parent and first_run["status"] == "running"
    recovery = journey.arm(
        "second-recovery", "control.queue_recovery", role="control", times=100, thread_id=source["thread_id"]
    )
    if outcome == "cancelled":
        await journey.post(*await journey.command(first_run, "interrupt"), expected=202)
    journey.release(first_model)
    terminal = await live.finish(first_run["id"], outcome)
    terminal_observations = journey.observations(first_case)
    assert terminal_observations
    assert await journey.queue_row(first) == consumed
    remaining = await journey.queue_row(second)
    assert remaining["state"] == "queued" and remaining["position"] == 1
    await journey.restart_control()
    before = await live.thread(source["thread_id"])
    assert await journey.queue_row(first) == consumed and await journey.queue_row(second) == remaining
    path = f"/api/v1/queued-submissions/{first['queued_submission_id']}"
    for method, body in [
        ("PATCH", {"expected_version": consumed["version"], "submission": consumed["submission"]}),
        ("DELETE", {"expected_version": consumed["version"]}),
    ]:
        payload = {"params": body} if method == "DELETE" else {"json": body}
        await live.request(method, path, expected=409, **payload, headers={"Idempotency-Key": uuid4().hex})
        assert await journey.queue_row(first) == consumed and await live.thread(source["thread_id"]) == before
    next_model = journey.arm("second", "model.request", role="control", case_id=later["case_id"], request=1)
    journey.release(recovery)
    await journey.reached(next_model)
    consumed_second = await journey.consumed_queue(second)
    following = await live.run(consumed_second["consumed_run_id"])
    assert following["parent_run_id"] == expected_parent and following["retry_of_run_id"] is None
    assert await journey.queue_row(first) == consumed
    assert len(await journey.thread_runs(source)) == 3
    assert journey.observations(first_case) == terminal_observations
    assert_absent(journey.observations(later), [first_case["token"]])
    journey.release(next_model)
    result = await journey.finish_queue(second)
    assert result["output_text"] == later["token"]
    assert await live.run(terminal["id"]) == terminal and await journey.queue_row(first) == consumed
    assert journey.observations(first_case) == terminal_observations
