"""Child results compete with steer, queue precedence, cancellation and branch selection."""

import json
import signal
from uuid import uuid4

import pytest

from .control_children import parent_observations, start_children
from .control_support import assert_absent, user_texts

pytestmark = pytest.mark.anyio


async def test_mixed_steer_and_child_results_reach_model_in_durable_fifo_order(control):
    journey, live = control, control.live
    case, root, parent, children = await start_children(journey)
    first, first_token = await journey.steer(parent["run_id"])
    (root / "child_0_release").touch()
    await live.finish(children[0]["id"])
    await live.wait(lambda: journey.inbox(parent["thread_id"]), lambda rows: len(rows) == 2, "first child publication")
    second, second_token = await journey.steer(parent["run_id"])
    (root / "child_1_release").touch()
    await live.finish(children[1]["id"])
    rows = await live.wait(
        lambda: journey.inbox(parent["thread_id"]), lambda rows: len(rows) == 4, "mixed inbox publication"
    )
    assert [row["delivery_sequence"] for row in rows] == [1, 2, 3, 4]
    assert [row["kind"] for row in rows] == ["steer", "async_subagent_result", "steer", "async_subagent_result"]
    assert rows[0]["id"] == first["steer_id"] and rows[2]["id"] == second["steer_id"]
    assert all(row["status"] == "pending" and row["target_run_id"] == parent["run_id"] for row in rows)
    assert_absent(parent_observations(journey, case), [first_token, second_token])
    (root / "parent_release").touch()
    result = await live.finish(parent["run_id"])
    assert result["output_text"] == "children-seen:0,1"
    text = "\n".join(user_texts(parent_observations(journey, case)[-1]))
    expected = [first_token, f"CHILD_RESULT_0_{case['token']}", second_token, f"CHILD_RESULT_1_{case['token']}"]
    assert all(text.count(value) == 1 for value in expected)
    assert [text.index(value) for value in expected] == sorted(text.index(value) for value in expected)
    rows = await journey.inbox(parent["thread_id"])
    assert all(row["status"] == "consumed" and row["consumed_by_run_id"] == result["id"] for row in rows)
    assert len(await journey.thread_runs(parent)) == 1


@pytest.mark.parametrize("arrival", ["before_cancel", "after_retry_accepted"])
async def test_cancelled_origin_results_never_revive_in_retry_but_new_children_can_deliver(control, arrival):
    journey, live, lab = control, control.live, control.lab
    case, root, parent, children = await start_children(journey)
    _, steer_token = await journey.steer(parent["run_id"])
    if arrival == "before_cancel":
        (root / "child_0_release").touch()
        await live.finish(children[0]["id"])
        await live.wait(
            lambda: journey.inbox(parent["thread_id"]), lambda rows: len(rows) == 2, "pending child before cancel"
        )
    await journey.post(*await journey.command(parent, "interrupt"), expected=202)
    cancelled = await live.finish(parent["run_id"], "cancelled")
    await lab.stop(lab.workers[0])
    for worker in lab.workers[1:]:
        lab.send(worker, signal.SIGTERM)
        await lab.wait_unready(worker)
    retry = await journey.accept(*await journey.command(cancelled, "retry"))
    assert (await live.run(retry["run_id"]))["status"] == "accepted"
    (root / "children_release").touch()
    for child in children.values():
        await live.finish(child["id"])
    rows = await live.wait(
        lambda: journey.inbox(parent["thread_id"]), lambda rows: len(rows) == 3, "old origin results"
    )
    old_children = {child["id"] for child in children.values()}
    assert rows[0]["status"] == "superseded"
    assert all(row["status"] == "suppressed" and row["consumed_by_run_id"] is None for row in rows[1:])
    assert {row["payload"]["child_run_id"] for row in rows[1:]} == old_children
    assert (await journey.state(retry["run_id"]))["receipts"] == []
    for worker in lab.workers[1:]:
        await lab.stop(worker)
    for name in ("child_0_release", "child_1_release", "children_release", "parent_release"):
        (root / name).unlink(missing_ok=True)
    await lab.start_worker()
    await lab.start_worker()
    await lab.start_worker()

    async def retry_children():
        threads = await live.collection(f"/api/v1/sessions/{parent['session_id']}/threads")
        return [thread for thread in threads if thread["origin_run_id"] == retry["run_id"]]

    spawned = await live.wait(retry_children, lambda threads: len(threads) == 2, "fresh retry children")
    new_ids = {thread["current_run_id"] for thread in spawned}
    assert len(new_ids) == 2 and not new_ids & old_children
    live.runs.extend(new_ids)
    (root / "children_release").touch()
    for child_id in new_ids:
        await live.finish(child_id)
    await live.wait(lambda: journey.inbox(parent["thread_id"]), lambda rows: len(rows) == 5, "new origin results")
    (root / "parent_release").touch()
    result = await live.finish(retry["run_id"])
    assert result["output_text"] == "children-seen:0,1"
    final_rows = await journey.inbox(parent["thread_id"])
    assert final_rows[:3] == rows
    assert all(row["status"] == "consumed" and row["consumed_by_run_id"] == retry["run_id"] for row in final_rows[3:])
    assert_absent(parent_observations(journey, case), [steer_token, *old_children])
    assert await live.run(cancelled["id"]) == cancelled


async def test_unbound_child_results_wait_behind_recoverably_blocked_queue(control):
    journey, live = control, control.live
    _, root, parent, children = await start_children(journey, mode="completed")
    source = await live.run(parent["run_id"])
    agent = await live.request("GET", f"{journey.base}/agents/{source['agent_id']}")
    later = await journey.case()
    row = await journey.queue(parent, later, expected_current_revision_id=source["agent_revision_id"])
    await journey.revision(agent, instructions="Queue repair uses the current revision")
    (root / "parent_release").touch()
    completed = await live.finish(parent["run_id"])
    (root / "children_release").touch()
    for child in children.values():
        await live.finish(child["id"])
    rows = await live.wait(
        lambda: journey.inbox(parent["thread_id"]), lambda rows: len(rows) == 2, "unbound child results"
    )
    assert all(item["status"] == "pending" and item["target_run_id"] is None for item in rows)
    await live.assert_stable(lambda: journey.thread_runs(parent), [completed], seconds=2)
    assert await journey.queue_row(row) == row and journey.observations(later) == []
    repaired = {key: value for key, value in row["submission"].items() if key != "expected_current_revision_id"}
    await live.request(
        "PATCH",
        f"/api/v1/queued-submissions/{row['queued_submission_id']}",
        json={
            "expected_version": row["version"],
            "submission": repaired,
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    result = await journey.finish_queue(row)
    assert result["input_kind"] == "agent_input" and result["parent_run_id"] == completed["id"]
    assert result["output_text"] == later["token"] and len(await journey.thread_runs(parent)) == 2
    rows = await journey.inbox(parent["thread_id"])
    assert all(item["status"] == "consumed" and item["consumed_by_run_id"] == result["id"] for item in rows)
    text = json.dumps(journey.observations(later))
    assert all(child["id"] in text for child in children.values())


async def test_historical_continue_supersedes_waiting_inbox_and_preserves_queue(control):
    journey, live, lab = control, control.live, control.lab
    agent = await journey.control_agent(
        subagent_mode="async", subagents={"child": {"agent_id": live.config["child_agent_id"]}}
    )
    first = await journey.start(await journey.case(), agent_id=agent["agent"]["id"])
    original = await live.finish(first["run_id"])
    _, root, parent, children = await start_children(journey, mode="client_tool", source=original)
    (root / "parent_release").touch()
    waiting = await live.finish(parent["run_id"], "waiting")
    steer, steer_token = await journey.steer(waiting["id"])
    (root / "children_release").touch()
    for child in children.values():
        await live.finish(child["id"])
    rows = await live.wait(
        lambda: journey.inbox(parent["thread_id"]), lambda rows: len(rows) == 3, "waiting mixed inbox"
    )
    assert rows[0]["id"] == steer["steer_id"]
    assert all(row["status"] == "pending" and row["source_waiting_run_id"] == waiting["id"] for row in rows)
    later = await journey.case()
    queued = await journey.queue(waiting, later)
    queue_before = await journey.queued(waiting)
    # Keep acceptance observable before any Worker can complete the branch or consume its queue.
    for worker in lab.workers:
        await lab.stop(worker)
    thread = await live.thread(waiting["thread_id"])
    branch_case = await journey.case()
    branch = await journey.accept(*await journey.command(original, "continue", case=branch_case))
    selected = await live.thread(waiting["thread_id"])
    assert selected["current_run_id"] == branch["run_id"] and selected["head_run_id"] == original["id"]
    assert selected["version"] == thread["version"] + 1
    assert await journey.queued(waiting) == queue_before
    superseded = await journey.inbox(parent["thread_id"])
    assert [row["id"] for row in superseded] == [row["id"] for row in rows]
    assert all(row["status"] == "superseded" and row["consumed_by_run_id"] is None for row in superseded)
    for action in ("feedback", "waiting_continue", "retry"):
        await journey.post(*await journey.command(waiting, action), expected=409)
        assert await live.thread(waiting["thread_id"]) == selected
        assert await journey.inbox(waiting["thread_id"]) == superseded
    await lab.start_worker()
    result = await live.finish(branch["run_id"])
    assert result["parent_run_id"] == original["id"] and result["thread_id"] == original["thread_id"]
    assert result["output_text"] == branch_case["token"]
    following = await journey.finish_queue(queued)
    assert following["parent_run_id"] == result["id"] and following["output_text"] == later["token"]
    assert_absent(
        journey.observations(branch_case) + journey.observations(later),
        [steer_token, *(child["id"] for child in children.values())],
    )
    assert await live.run(waiting["id"]) == waiting and await live.run(original["id"]) == original
    assert len(await journey.thread_runs(waiting)) == 4
