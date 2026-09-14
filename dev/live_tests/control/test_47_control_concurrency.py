"""Competing commands prepare concurrently but commit one exact Thread advancement."""

from uuid import uuid4

import pytest

from ..infrastructure.client import agent_input

pytestmark = pytest.mark.anyio


async def source_for(journey, operation):
    if operation in {"feedback", "waiting_continue"}:
        _, source = await journey.waiting()
        return source
    if operation == "retry":
        case = await journey.live.case("model_error")
        receipt = await journey.start(case)
        source = await journey.live.finish(receipt["run_id"], "failed")
        await journey.live.release(case)
        return source
    receipt = await journey.start(await journey.case())
    return await journey.live.finish(receipt["run_id"])


@pytest.mark.parametrize(
    "operations",
    [
        ("continue", "continue"),
        ("continue", "submit"),
        ("feedback", "feedback"),
        ("feedback", "waiting_continue"),
        ("retry", "retry"),
        ("retry", "submit"),
    ],
)
async def test_distinct_commands_with_same_thread_version_have_one_winner(control, operations):
    journey, live, lab = control, control.live, control.lab
    source = await source_for(journey, operations[0])
    if source["status"] == "waiting":
        steer, _ = await journey.steer(source["id"])
    await lab.stop(lab.workers[0])
    before = await live.thread(source["thread_id"])
    commands = [(*await journey.command(source, operation), uuid4().hex) for operation in operations]
    replies = await journey.race(commands)
    assert sorted(reply.status_code for reply in replies) == [202, 409], [reply.text for reply in replies]
    winner = next(index for index, reply in enumerate(replies) if reply.status_code == 202)
    body = replies[winner].json()
    receipt = body.get("run") if "outcome" in body else body
    live.track(receipt)
    thread = await live.thread(source["thread_id"])
    assert thread["version"] == before["version"] + 1
    assert thread["current_run_id"] == receipt["run_id"] and thread["head_run_id"] == before["head_run_id"]
    assert len(await journey.thread_runs(source)) == 2 and await journey.queued(source) == []
    for index, (path, request, key) in enumerate(commands):
        replay = await live.http.post(path, json=request, headers={"Idempotency-Key": key})
        assert replay.status_code == replies[index].status_code
        if index == winner:
            assert replay.json() == body
    if source["status"] == "waiting":
        rows = await journey.inbox(source["thread_id"])
        assert len(rows) == 1 and rows[0]["id"] == steer["steer_id"]
        assert rows[0]["status"] == "pending" and rows[0]["target_run_id"] == receipt["run_id"]
    await lab.start_worker()
    await live.finish(receipt["run_id"])
    assert await live.run(source["id"]) == source


@pytest.mark.parametrize("operation", ["continue", "fork", "retry", "feedback", "waiting_continue"])
async def test_concurrent_same_key_successor_commands_reconcile_one_receipt(control, operation):
    journey, live, lab = control, control.live, control.lab
    source = await source_for(journey, operation)
    await lab.stop(lab.workers[0])
    before = await live.thread(source["thread_id"])
    path, body = await journey.command(source, operation)
    key = uuid4().hex
    replies = await journey.race([(path, body, key), (path, body, key)])
    assert [reply.status_code for reply in replies] == [202, 202], [reply.text for reply in replies]
    assert replies[0].json() == replies[1].json()
    accepted = replies[0].json()
    receipt = accepted.get("run") if "outcome" in accepted else accepted
    live.track(receipt)
    assert len(await journey.runs()) == 2
    assert (await live.thread(source["thread_id"]))["version"] == before["version"] + int(operation != "fork")
    if operation in {"continue", "fork", "waiting_continue"}:
        changed = {**body, "input": agent_input("Changed semantic input")}
    elif operation == "feedback":
        changed = {**body, "resolutions": await journey.approve(source)}
    else:
        changed = {**body, "expected_thread_version": body["expected_thread_version"] + 1}
    await journey.post(path, changed, expected=409, key=key)
    await lab.start_worker()
    await live.finish(receipt["run_id"])
    assert await journey.post(path, body, expected=202, key=key) == accepted
    assert await live.run(source["id"]) == source
    assert len(await lab.attempts(receipt["run_id"])) == 1


async def test_fork_and_source_thread_continuation_commit_independently(control):
    journey, live, lab = control, control.live, control.lab
    first_environment, _ = await journey.environment()
    second_environment, _ = await journey.environment()
    case = await live.case("remember")
    root = await journey.start(case, environment={"environment_id": first_environment["id"]})
    source = await live.finish(root["run_id"])
    await lab.stop(lab.workers[0])
    before = await live.thread(source["thread_id"])
    prompt = agent_input("Recall the remembered token.")
    fork_path, fork_body = await journey.command(source, "fork", input=prompt)
    continue_path, continue_body = await journey.command(source, "continue", input=prompt)
    continue_body["environment"] = {"environment_id": second_environment["id"]}
    replies = await journey.race([(fork_path, fork_body, uuid4().hex), (continue_path, continue_body, uuid4().hex)])
    assert [reply.status_code for reply in replies] == [202, 202], [reply.text for reply in replies]
    receipts = [reply.json() for reply in replies]
    for receipt in receipts:
        live.track(receipt)
    assert receipts[0]["thread_id"] != source["thread_id"] == receipts[1]["thread_id"]
    thread = await live.thread(source["thread_id"])
    assert thread["version"] == before["version"] + 1 and thread["default_environment_id"] == second_environment["id"]
    await lab.start_worker()
    forked, continued = [await live.finish(receipt["run_id"]) for receipt in receipts]
    assert forked["output_text"] == continued["output_text"] == case["token"]
    assert forked["parent_run_id"] == continued["parent_run_id"] == source["id"]
    assert forked["environment_id"] == first_environment["id"]
    assert continued["environment_id"] == second_environment["id"]
    assert forked["session_id"] == continued["session_id"] == source["session_id"]
    assert await live.run(source["id"]) == source
