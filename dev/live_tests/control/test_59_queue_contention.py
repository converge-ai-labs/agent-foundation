"""8/32 enqueuers and two ordered reorder/handoff racers over 4/16 queued intents."""

import asyncio
from uuid import uuid4

import pytest

from .contention_support import (
    RELEASE_OBSERVATION,
    assert_lifecycle,
    contention_case,
    contention_metrics,
    lifecycle,
    prepared_writers,
)

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("writers", "control"),
    [(8, {"queue": {"max_queued": 4}}), (32, {"queue": {"max_queued": 16}})],
    indirect=["control"],
    ids=["writers-8-capacity-4", "writers-32-capacity-16"],
)
@pytest.mark.parametrize("winner", ["reorder", "consume"])
@contention_case("{writers} queue writers compete for bounded capacity; {winner} wins the reorder/consumption race")
async def test_queue_contention_preserves_capacity_permutation_and_single_consumption(control, writers, winner):
    journey, live = control, control.live
    case = await journey.case()
    model = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=1)
    source = await journey.start(case)
    await journey.reached(model)
    before = await live.thread(source["thread_id"])
    cases = [await journey.case() for _ in range(writers)]
    commands = [(*await journey.command(source, "submit", case=later), uuid4().hex) for later in cases]
    async with prepared_writers(
        journey,
        commands,
        point="queue.enqueue_prepared",
        operation="enqueue_barrier_inclusive",
        actor="Queue submission HTTP writer",
        thread_id=source["thread_id"],
    ) as (
        barrier,
        tasks,
        _,
    ):
        assert await journey.queued(source) == []
        journey.release(barrier)
        replies = await asyncio.gather(*tasks)
    capacity = writers // 2
    assert sorted(reply.status_code for reply in replies) == [202] * capacity + [409] * capacity
    by_id = {
        reply.json()["queued_submission"]["queued_submission_id"]: (case, command, reply.json())
        for case, command, reply in zip(cases, commands, replies, strict=True)
        if reply.status_code == 202
    }
    rows = await journey.queued(source)
    ids = [row["queued_submission_id"] for row in rows]
    assert len(rows) == capacity and set(ids) == set(by_id)
    assert [row["position"] for row in rows] == list(range(1, capacity + 1))
    full = await live.thread(source["thread_id"])
    assert full["version"] == before["version"] and full["queue_version"] == before["queue_version"] + capacity
    for _, command, reply in by_id.values():
        assert await journey.post(*command[:2], key=command[2], expected=202) == reply
    assert await live.thread(source["thread_id"]) == full
    path = f"/api/v1/threads/{source['thread_id']}/queued-submissions/reorder"
    body = {"expected_queue_version": full["queue_version"], "queued_submission_ids": list(reversed(ids))}
    gate = (
        journey.arm("handoff", "queue.before_commit", run_id=source["run_id"])
        if winner == "reorder"
        else journey.arm("consumed", "model.request", role="control", case_id=by_id[ids[0]][0]["case_id"], request=1)
    )
    journey.release(model)
    candidate = await journey.reached(gate)
    if winner == "reorder":
        assert await journey.queued(source) == rows
        assert await lifecycle(journey, [candidate["successor_run_id"]]) == []
    observed = await live.thread(source["thread_id"])
    reply = await contention_metrics().call(
        "reorder_during_worker_handoff",
        "Reorder HTTP writer racing the worker queue consumer",
        lambda: live.http.post(path, json=body, headers={"Idempotency-Key": uuid4().hex}),
        semantics="HTTP request to response; worker held separately at a test gate, so this is controlled-race wall time.",
        expected_statuses=(409,) if winner == "consume" else (),
    )
    assert reply.status_code == (200 if winner == "reorder" else 409), reply.text
    expected_ids = list(reversed(ids)) if winner == "reorder" else ids
    if winner == "reorder":
        reordered = await journey.queued(source)
        assert [row["queued_submission_id"] for row in reordered] == expected_ids
        assert [row["position"] for row in reordered] == list(range(1, capacity + 1))
    else:
        assert await live.thread(source["thread_id"]) == observed
    with contention_metrics().measure(
        "release_to_source_terminal_observation",
        "Source terminal observation after reorder/consume ordering is decided",
        semantics=RELEASE_OBSERVATION + " In consume-first order the source is already sealed before release.",
    ):
        journey.release(gate)
        parent = await live.finish(source["run_id"])
    finished = [parent]
    consumed_ids = []
    for queued_id in expected_ids:
        row = next(row for row in rows if row["queued_submission_id"] == queued_id)
        result = await journey.finish_queue(row)
        assert result["output_text"] == by_id[queued_id][0]["token"]
        assert result["parent_run_id"] == parent["id"]
        assert len(journey.observations(by_id[queued_id][0])) == 1
        consumed = await journey.queue_row(row)
        assert consumed["position"] is None and consumed["state"] == "consumed"
        consumed_ids.append(consumed["consumed_run_id"])
        finished.append(result)
        parent = result
    assert len(set(consumed_ids)) == capacity
    assert len(await journey.thread_runs(source)) == capacity + 1
    assert await journey.queued(source) == []
    after = await live.thread(source["thread_id"])
    assert after["queue_version"] == full["queue_version"] + capacity + int(winner == "reorder")
    for later, reply in zip(cases, replies, strict=True):
        if reply.status_code == 409:
            assert journey.observations(later) == []
    events = await lifecycle(journey, [run["id"] for run in finished])
    for run in finished:
        attempts = await journey.lab.attempts(run["id"])
        assert len(attempts) == 1
        assert_lifecycle(events, run["id"], "completed", attempts)
    if winner == "reorder":
        assert (await live.http.get(f"/api/v1/runs/{candidate['successor_run_id']}")).status_code == 404
        assert await lifecycle(journey, [candidate["successor_run_id"]]) == []


@pytest.mark.parametrize("winner", ["interrupt", "handoff"])
@contention_case("Interrupt and worker handoff race over eight queued intents; {winner} wins")
async def test_two_terminal_racers_preserve_eight_queued_intents_and_atomic_lifecycle(control, winner):
    journey, live = control, control.live
    case = await journey.case(effect=True)
    model = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=2)
    source = await journey.start(case)
    await journey.reached(model)
    cases = [await journey.case(effect=True) for _ in range(8)]
    commands = [(*await journey.command(source, "submit", case=later), uuid4().hex) for later in cases]
    async with prepared_writers(
        journey,
        commands,
        point="queue.enqueue_prepared",
        operation="enqueue_barrier_inclusive",
        actor="Queue submission HTTP writer",
        expected_statuses=(),
        thread_id=source["thread_id"],
    ) as (
        barrier,
        tasks,
        _,
    ):
        journey.release(barrier)
        replies = await asyncio.gather(*tasks)
    assert all(reply.status_code == 202 for reply in replies)
    by_id = {
        reply.json()["queued_submission"]["queued_submission_id"]: case
        for reply, case in zip(replies, cases, strict=True)
    }
    rows = await journey.queued(source)
    assert len(rows) == 8
    recovery = journey.arm(
        "recovery", "control.queue_recovery", role="control", times=100, thread_id=source["thread_id"]
    )
    handoff = journey.arm("handoff", "queue.before_commit", run_id=source["run_id"])
    interrupt = journey.arm("interrupt", "control.interrupt_prepared", role="control", run_id=source["run_id"])
    first_case = by_id[rows[0]["queued_submission_id"]]
    next_model = journey.arm("next-model", "model.request", role="control", case_id=first_case["case_id"], request=1)
    journey.release(model)
    candidate = await journey.reached(handoff)
    before = await live.thread(source["thread_id"])
    source_events = await lifecycle(journey, [source["run_id"]])
    assert await lifecycle(journey, [candidate["successor_run_id"]]) == []
    async with journey.post_in_flight(
        *await journey.command(source, "interrupt"),
        metric_operation="interrupt_barrier_inclusive",
        expected_statuses=(409,) if winner == "handoff" else (),
    ) as cancelling:
        await journey.reached(interrupt)
        assert not cancelling.done() and await journey.queued(source) == rows
        if winner == "interrupt":
            journey.release(interrupt)
            reply = await cancelling
            assert reply.status_code == 202, reply.text
            sealed = await live.finish(source["run_id"], "cancelled")
            assert await journey.queued(source) == rows
            assert await lifecycle(journey, [candidate["successor_run_id"]]) == []
            with contention_metrics().measure(
                "cancelled_handoff_release_to_successor_model",
                "Worker/control recovery admits next queued intent after cancellation",
                semantics=RELEASE_OBSERVATION,
            ):
                journey.release(handoff)
                journey.release(recovery)
                await journey.reached(next_model)
        else:
            with contention_metrics().measure(
                "handoff_release_to_source_sealed",
                "Worker wins terminal race and atomically consumes the queue head",
                semantics=RELEASE_OBSERVATION,
            ):
                journey.release(handoff)
                sealed = await live.finish(source["run_id"])
            await journey.reached(next_model)
        consumed = await journey.consumed_queue(rows[0])
        if winner == "handoff":
            assert consumed["consumed_run_id"] == candidate["successor_run_id"]
            journey.release(interrupt)
            reply = await cancelling
            assert reply.status_code == 409, reply.text
        else:
            assert consumed["consumed_run_id"] != candidate["successor_run_id"]
            assert await lifecycle(journey, [candidate["successor_run_id"]]) == []
        selected = await live.thread(source["thread_id"])
        # Sealing and successor admission each advance the public version,
        # including when both transitions share one relational commit.
        assert selected["version"] == before["version"] + 2
        assert selected["queue_version"] == before["queue_version"] + 1
        remaining = await journey.queued(source)
        assert [row["queued_submission_id"] for row in remaining] == [row["queued_submission_id"] for row in rows[1:]]
        assert [row["position"] for row in remaining] == list(range(1, 8))
        events = await lifecycle(journey, [source["run_id"], consumed["consumed_run_id"]])
        terminal = next(event for event in events if event["event_type"] == "run." + sealed["status"])
        admitted = next(
            event
            for event in events
            if event["run_id"] == consumed["consumed_run_id"] and event["event_type"] == "run.accepted"
        )
        assert (terminal["mutation_id"] == admitted["mutation_id"]) == (winner == "handoff")
        assert [event for event in events if event["id"] in {event["id"] for event in source_events}] == source_events
    journey.release(next_model)
    parent_id = source["run_id"] if winner == "handoff" else None
    finished = [sealed]
    for row in rows:
        result = await journey.finish_queue(row)
        assert result["parent_run_id"] == parent_id
        expected_case = by_id[row["queued_submission_id"]]
        assert journey.effects(expected_case) == [expected_case["token"]]
        parent_id = result["id"]
        finished.append(result)
    assert len(await journey.thread_runs(source)) == 9
    assert journey.effects(case) == [case["token"]]
    assert await live.run(source["run_id"]) == sealed
    events = await lifecycle(journey, [run["id"] for run in finished])
    for run in finished:
        attempts = await journey.lab.attempts(run["id"])
        assert len(attempts) == 1
        assert_lifecycle(events, run["id"], run["status"], attempts)
