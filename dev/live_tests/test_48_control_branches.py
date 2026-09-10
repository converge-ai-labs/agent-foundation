"""Historical selection and terminal successors cannot revive abandoned input."""

from uuid import uuid4

import pytest

from .control_support import assert_absent

pytestmark = pytest.mark.anyio


async def test_historical_continue_abandons_waiting_branch_and_preserves_queue(control):
    journey, live, lab = control, control.live, control.lab
    agent = await journey.control_agent()
    root = await journey.start(await journey.case(), agent_id=agent["agent"]["id"])
    original = await live.finish(root["run_id"])
    _, waiting = await journey.waiting(source=original)
    steer, token = await journey.steer(waiting["id"])
    later = await journey.case()
    queued = await journey.queue(waiting, later)
    queue_before = await journey.queued(waiting)
    await lab.stop(lab.workers[0])
    thread = await live.thread(waiting["thread_id"])
    branch_case = await journey.case()
    branch = await journey.accept(*await journey.command(original, "continue", case=branch_case))
    selected = await live.thread(waiting["thread_id"])
    assert selected["current_run_id"] == branch["run_id"] and selected["head_run_id"] == original["id"]
    assert selected["version"] == thread["version"] + 1
    assert await journey.queued(waiting) == queue_before
    rows = await journey.inbox(waiting["thread_id"])
    assert [(row["id"], row["status"], row["consumed_by_run_id"]) for row in rows] == [
        (steer["steer_id"], "superseded", None)
    ]
    for operation in ("feedback", "waiting_continue", "retry"):
        await journey.post(*await journey.command(waiting, operation), expected=409)
        assert await live.thread(waiting["thread_id"]) == selected
        assert await journey.inbox(waiting["thread_id"]) == rows
    await lab.start_worker()
    completed = await live.finish(branch["run_id"])
    assert completed["parent_run_id"] == original["id"] and completed["thread_id"] == original["thread_id"]
    assert completed["output_text"] == branch_case["token"]
    following = await journey.finish_queue(queued)
    assert following["parent_run_id"] == completed["id"]
    assert_absent(journey.observations(branch_case) + journey.observations(later), [token])
    assert await live.run(original["id"]) == original and await live.run(waiting["id"]) == waiting
    assert len(await journey.thread_runs(waiting)) == 4


@pytest.mark.parametrize("lineage", ["root", "continue"])
async def test_cancelled_retry_preserves_original_parent_and_drops_superseded_steer(control, lineage):
    journey, live, lab = control, control.live, control.lab
    parent = None
    if lineage == "continue":
        first = await journey.start(await journey.case())
        parent = await live.finish(first["run_id"])
    case = await journey.case(effect=True)
    gate = journey.arm("before-effect", "tool.before_effect", case_id=case["case_id"])
    source = (
        await journey.start(case)
        if parent is None
        else await journey.accept(*await journey.command(parent, "continue", case=case))
    )
    await journey.reached(gate)
    steer, token = await journey.steer(source["run_id"])
    path, body = await journey.command(source, "interrupt")
    key = uuid4().hex
    cancelled_receipt = await journey.post(path, body, expected=202, key=key)
    cancelled = await live.finish(source["run_id"], "cancelled")
    journey.release(gate)
    await lab.stop(lab.workers[0])
    assert journey.effects(case) == []
    attempts = await lab.attempts(cancelled["id"])
    rows = await journey.inbox(cancelled["thread_id"])
    assert [(row["id"], row["status"]) for row in rows] == [(steer["steer_id"], "superseded")]
    retry = await journey.accept(*await journey.command(cancelled, "retry"))
    accepted = await live.run(retry["run_id"])
    for field in ("input", "input_kind", "parent_run_id", "lineage_kind", "agent_revision_id"):
        assert accepted[field] == cancelled[field]
    assert accepted["parent_run_id"] == (parent["id"] if parent else None)
    assert accepted["retry_of_run_id"] == cancelled["id"]
    execution = await journey.execution(accepted["id"])
    assert execution["authority_principal"] == (await journey.execution(cancelled["id"]))["authority_principal"]
    assert execution["attempts_charged"] == 0 and execution["attempts"] == []
    assert execution["usage_charged"]["model_requests"] == 0
    assert (await journey.state(accepted["id"]))["receipts"] == []
    assert await journey.post(path, body, expected=202, key=key) == cancelled_receipt
    await journey.post(*await journey.command(cancelled, "retry"), expected=409)
    await lab.start_worker()
    await journey.assert_settled(retry, case=case, effects=1)
    assert_absent(journey.observations(case), [token])
    assert await journey.inbox(cancelled["thread_id"]) == rows
    assert await live.run(cancelled["id"]) == cancelled and await lab.attempts(cancelled["id"]) == attempts


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
@pytest.mark.parametrize("head", ["none", "completed", "waiting"])
@pytest.mark.parametrize("has_queue", [False, True])
async def test_terminal_submission_respects_selected_head_and_existing_queue(control, terminal, head, has_queue):
    journey, live, lab = control, control.live, control.lab
    parent = None
    if head == "waiting":
        case, parent = await journey.waiting()
        request_number = 2
    else:
        if head == "completed":
            root = await journey.start(await journey.case())
            parent = await live.finish(root["run_id"])
        case = await journey.case()
        request_number = 1
    if terminal == "failed":
        journey.plan(case, failure="401", failures=100)
    gate = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=request_number)
    if head == "none":
        receipt = await journey.start(case)
    elif head == "waiting":
        receipt = await journey.accept(
            *await journey.command(parent, "feedback", resolutions=await journey.approve(parent))
        )
    else:
        receipt = await journey.accept(*await journey.command(parent, "continue", case=case))
    await journey.reached(gate)
    recovery = journey.arm(
        "defer-recovery", "control.queue_recovery", role="control", times=100, thread_id=receipt["thread_id"]
    )
    queued = await journey.queue(receipt, await journey.case()) if has_queue else None
    queue_before = await journey.queued(receipt)
    if terminal == "cancelled":
        await journey.post(*await journey.command(receipt, "interrupt"), expected=202)
    journey.release(gate)
    source = await live.finish(receipt["run_id"], terminal)
    thread = await live.thread(receipt["thread_id"])
    assert thread["head_run_id"] == (parent["id"] if parent else None)
    path, body = await journey.command(source, "submit", case=await journey.case())
    if has_queue or head == "waiting":
        await journey.post(path, body, expected=409)
        assert await live.thread(thread["id"]) == thread
        assert await journey.queued(source) == queue_before
        assert len(await journey.thread_runs(source)) == (1 if head == "none" else 2)
    else:
        successor = await journey.accept(path, body)
        result = await live.finish(successor["run_id"])
        assert result["parent_run_id"] == (parent["id"] if parent else None)
        assert result["lineage_kind"] == ("continue" if parent else "root")
        assert result["retry_of_run_id"] is None and result["thread_id"] == source["thread_id"]
    if head == "waiting":
        # Retrying failed feedback is the allowed way to move this waiting head.
        journey.plan(case)
        await lab.stop(lab.workers[0])
        retry = await journey.accept(*await journey.command(source, "retry"))
        assert await journey.queued(source) == queue_before
        accepted = await live.run(retry["run_id"])
        assert accepted["parent_run_id"] == parent["id"] and accepted["input"] == source["input"]
        await lab.start_worker()
        await live.finish(retry["run_id"])
    journey.release(recovery)
    if queued:
        await journey.finish_queue(queued)
    assert await live.run(source["id"]) == source
