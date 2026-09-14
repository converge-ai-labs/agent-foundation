"""8/32 prepared writers: root idempotency and optimistic Continue/Retry admission."""

import json
from uuid import uuid4

import pytest

from .contention_support import assert_lifecycle, contention_case, lifecycle

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("writers", [8, 32], ids=lambda n: f"writers-{n}")
@pytest.mark.parametrize("operation", ["accept", "continue", "retry"])
@contention_case(
    "{writers} HTTP writers compete for {operation} admission: one durable Run, idempotency and optimistic version checks"
)
async def test_admission_contention_has_one_durable_run_and_no_losing_facts(control, writers, operation):
    journey, live, lab = control, control.live, control.lab
    source = None
    if operation != "accept":
        case = await journey.case(**({"failure": "401", "failures": 100} if operation == "retry" else {}))
        initial = await journey.start(case)
        source = await live.finish(initial["run_id"], "failed" if operation == "retry" else "completed")
    await lab.stop(lab.workers[0])
    if source is None:
        case = await journey.case()
        path = journey.base + "/runs"
        body = {**live.start_body(case), "agent_id": journey.agent_id}
        key = uuid4().hex
        commands = [(path, body, key) for _ in range(writers)]
    else:
        before = await live.thread(source["thread_id"])
        path, body = await journey.command(source, operation)
        commands = [(path, body, uuid4().hex) for _ in range(writers)]
    replies = await journey.race(
        commands, metric_operation=operation + "_barrier_inclusive", expected_statuses=() if source is None else (409,)
    )
    expected = [202] * writers if source is None else [202] + [409] * (writers - 1)
    assert sorted(reply.status_code for reply in replies) == expected, [reply.text for reply in replies]
    accepted = [reply.json() for reply in replies if reply.status_code == 202]
    assert all(receipt == accepted[0] for receipt in accepted)
    receipt = accepted[0]
    live.track(receipt)
    run_id = receipt["run_id"]
    assert len(await journey.runs()) == (1 if source is None else 2)
    assert await lab.attempts(run_id) == []
    events = await lifecycle(journey, [run_id])
    assert [event["event_type"] for event in events] == ["run.accepted"]
    # Every losing candidate published an object, but no relational Run/event.
    candidates = [json.loads(path.read_text())["run_id"] for path in (lab.root / "faults").glob("race-*/hit-*.json")]
    assert len(candidates) == len(set(candidates)) == writers
    assert await lifecycle(journey, set(candidates) - {run_id}) == []
    for candidate in set(candidates) - {run_id}:
        assert (await live.http.get(f"/api/v1/runs/{candidate}")).status_code == 404
    thread = await live.thread(receipt["thread_id"])
    assert thread["current_run_id"] == run_id
    if source is not None:
        assert thread["version"] == before["version"] + 1
        assert thread["head_run_id"] == before["head_run_id"]
    for (path, body, key), reply in zip(commands, replies, strict=True):
        replay = await live.http.post(path, json=body, headers={"Idempotency-Key": key})
        assert replay.status_code == reply.status_code
        if reply.status_code == 202:
            assert replay.json() == reply.json()
    assert await lifecycle(journey, candidates) == events
    await lab.start_worker()
    outcome = "failed" if operation == "retry" else "completed"
    await live.finish(run_id, outcome)
    attempts = await lab.attempts(run_id)
    assert len(attempts) == 1
    assert_lifecycle(await lifecycle(journey, [run_id]), run_id, outcome, attempts)
    if source is not None:
        assert await live.run(source["id"]) == source
