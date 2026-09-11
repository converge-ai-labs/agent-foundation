"""Queue admission and recovery compete with real Run outcome transactions."""

from uuid import uuid4

import pytest

from .control_support import assert_absent

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("winner", ["recovery", "explicit"])
async def test_background_and_explicit_consume_compete_after_both_publish_initial_state(control, winner):
    journey, live = control, control.live
    case = await journey.case()
    model = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=1)
    source = await journey.start(case)
    await journey.reached(model)
    later = await journey.case(effect=True)
    row = await journey.queue(source, later)
    recovery = journey.arm("recovery-start", "control.queue_recovery", role="control", thread_id=source["thread_id"])
    await journey.post(*await journey.command(source, "interrupt"), expected=202)
    journey.release(model)
    cancelled = await live.finish(source["run_id"], "cancelled")
    before = await live.thread(source["thread_id"])
    gates = {
        owner: journey.arm(owner, "queue.consume_prepared", role="control", consumer=owner, thread_id=before["id"])
        for owner in ("recovery", "explicit")
    }
    loser = "explicit" if winner == "recovery" else "recovery"
    lost = journey.arm(
        "loser-finished",
        "queue.consume_finished",
        role="control",
        action="observe",
        consumer=loser,
        thread_id=before["id"],
        succeeded=False,
    )
    next_model = journey.arm("next-model", "model.request", role="control", case_id=later["case_id"], request=1)
    async with journey.post_in_flight(*await journey.command(source, "consume")) as explicit:
        candidates = {"explicit": await journey.reached(gates["explicit"])}
        journey.release(recovery)
        candidates["recovery"] = await journey.reached(gates["recovery"])
        assert candidates["explicit"]["run_id"] != candidates["recovery"]["run_id"]
        assert not explicit.done() and await live.thread(before["id"]) == before
        assert await journey.queue_row(row) == row
        journey.release(gates[winner])
        consumed = await journey.consumed_queue(row)
        assert consumed["consumed_run_id"] == candidates[winner]["run_id"]
        await journey.reached(next_model)
        accepted_thread = await live.thread(before["id"])
        assert accepted_thread["version"] == before["version"] + 1
        assert accepted_thread["queue_version"] == before["queue_version"] + 1
        journey.release(gates[loser])
        response = await explicit
        assert response.status_code == (202 if winner == "explicit" else 409), response.text
        if winner == "explicit":
            assert response.json()["run"]["run_id"] == consumed["consumed_run_id"]
        await journey.reached(lost)
        assert (await live.http.get(f"/api/v1/runs/{candidates[loser]['run_id']}")).status_code == 404
        assert await journey.queue_row(row) == consumed
        assert await live.thread(before["id"]) == accepted_thread
    journey.release(next_model)
    result = await journey.finish_queue(row)
    assert result["parent_run_id"] is None and result["lineage_kind"] == "root"
    assert later["token"] in result["output_text"] and journey.effects(later) == [later["token"]]
    assert len(await journey.thread_runs(source)) == 2
    assert len(await journey.lab.attempts(result["id"])) == 1
    assert await live.run(cancelled["id"]) == cancelled


@pytest.mark.parametrize("outcome", ["completed", "waiting", "failed", "cancelled"])
@pytest.mark.parametrize("winner", ["enqueue", "terminal"])
async def test_enqueue_and_run_sealing_revalidate_the_same_thread_version(control, outcome, winner):
    journey, live = control, control.live
    if outcome == "waiting":
        agent = await journey.control_agent()
        case = await journey.case(batches=[[{"tool": "live_client", "arguments": {"prompt": "Supply a value"}}]])
        terminal = journey.arm("terminal", "outcome.verified", kind="waiting")
    else:
        agent = None
        case = await journey.case(
            effect=outcome == "cancelled", **({"failure": "401", "failures": 100} if outcome == "failed" else {})
        )
        terminal = (
            journey.arm("terminal", "control.failure_prepared", retryable=False)
            if outcome == "failed"
            else journey.arm("terminal", "tool.before_effect", case_id=case["case_id"])
            if outcome == "cancelled"
            else journey.arm("terminal", "outcome.verified", kind="completed")
        )
    source = await journey.start(case, **({"agent_id": agent["agent"]["id"]} if agent else {}))
    await journey.reached(terminal)
    before = await live.thread(source["thread_id"])
    later = await journey.case()
    path, body = await journey.command(source, "submit", case=later)
    admission = journey.arm("enqueue", "queue.enqueue_prepared", role="control", thread_id=before["id"])
    recovery = journey.arm("recovery", "control.queue_recovery", role="control", times=100, thread_id=before["id"])
    key = uuid4().hex
    row = None
    async with journey.post_in_flight(path, body, key=key) as submitting:
        await journey.reached(admission)
        assert not submitting.done() and await journey.queued(source) == []
        if winner == "enqueue":
            journey.release(admission)
            reply = await submitting
            assert reply.status_code == 202 and reply.json()["outcome"] == "queued", reply.text
            row = reply.json()["queued_submission"]
            assert await journey.thread_runs(source) == [await live.run(source["run_id"])]
        if outcome == "cancelled":
            await journey.post(*await journey.command(source, "interrupt"), expected=202)
        journey.release(terminal)
        sealed = await live.finish(source["run_id"], outcome)
        selected = await live.thread(before["id"])
        assert selected["version"] == before["version"] + 1
        assert selected["queue_version"] == before["queue_version"] + int(winner == "enqueue")
        assert selected["current_run_id"] == source["run_id"]
        assert selected["head_run_id"] == (source["run_id"] if outcome in {"completed", "waiting"} else None)
        if winner == "terminal":
            journey.release(admission)
            reply = await submitting
            assert reply.status_code == 409, reply.text
            assert await journey.queued(source) == []
            assert await live.thread(before["id"]) == selected
            assert await journey.thread_runs(source) == [sealed]
        else:
            assert await journey.queue_row(row) == row
            assert await journey.post(path, body, key=key, expected=202) == reply.json()
    # A fresh command uses the new version. Waiting queues; other terminal
    # outcomes with no existing queue accept immediately, without a queued row.
    if row is None:
        response = await journey.post(*await journey.command(sealed, "submit", case=later), expected=202)
        if outcome == "waiting":
            assert response["outcome"] == "queued"
            row = response["queued_submission"]
        else:
            assert response["outcome"] == "run_accepted" and response["queued_submission"] is None
            live.track(response["run"])
            result = await live.finish(response["run"]["run_id"])
            assert result["parent_run_id"] == (sealed["id"] if outcome == "completed" else None)
            assert result["output_text"] == later["token"]
    parent = sealed["id"] if outcome == "completed" else None
    if outcome == "waiting":
        feedback = await journey.accept(*await journey.command(sealed, "feedback"))
        parent = (await live.finish(feedback["run_id"]))["id"]
    journey.release(recovery)
    if row is not None:
        result = await journey.finish_queue(row)
        assert result["parent_run_id"] == parent and result["output_text"] == later["token"]
    assert len(await journey.thread_runs(source)) == (3 if outcome == "waiting" else 2)
    assert await live.run(sealed["id"]) == sealed
    assert journey.effects(case) == []
    if outcome in {"failed", "cancelled"}:
        assert_absent(journey.observations(later), [case["token"]])


@pytest.mark.parametrize("winner", ["enqueue", "recovery"])
async def test_completed_thread_submission_cannot_bypass_queue_during_recovery(control, winner):
    journey, live = control, control.live
    case = await journey.case()
    model = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=1)
    source = await journey.start(case)
    await journey.reached(model)
    cases = [await journey.case(), await journey.case()]
    first = await journey.queue(source, cases[0])
    # Force the bounded handoff fallback so ordinary completion leaves a queue.
    fallback = journey.arm("fallback", "queue.before_commit", action="unavailable", run_id=source["run_id"])
    recovery_start = journey.arm(
        "recovery-start", "control.queue_recovery", role="control", thread_id=source["thread_id"]
    )
    journey.release(model)
    await journey.reached(fallback)
    completed = await live.finish(source["run_id"])
    before = await live.thread(source["thread_id"])
    recovery = journey.arm(
        "recovery", "queue.consume_prepared", role="control", consumer="recovery", thread_id=before["id"]
    )
    admission = journey.arm("enqueue", "queue.enqueue_prepared", role="control", thread_id=before["id"])
    next_model = journey.arm("next-model", "model.request", role="control", case_id=cases[0]["case_id"], request=1)
    recovery_done = journey.arm(
        "recovery-done",
        "queue.consume_finished",
        role="control",
        action="observe",
        consumer="recovery",
        thread_id=before["id"],
        succeeded=winner == "recovery",
    )
    async with journey.post_in_flight(*await journey.command(source, "submit", case=cases[1])) as submitting:
        await journey.reached(admission)
        journey.release(recovery_start)
        candidate = await journey.reached(recovery)
        assert await journey.queue_row(first) == first and await live.thread(before["id"]) == before
        if winner == "enqueue":
            journey.release(admission)
            reply = await submitting
            assert reply.status_code == 202 and reply.json()["outcome"] == "queued", reply.text
            second = reply.json()["queued_submission"]
            after = await live.thread(before["id"])
            assert after["version"] == before["version"] and after["current_run_id"] == completed["id"]
            assert after["queue_version"] == before["queue_version"] + 1
            assert [row["position"] for row in await journey.queued(source)] == [1, 2]
        journey.release(recovery)
        await journey.reached(recovery_done)
        await journey.reached(next_model)
        consumed = await journey.consumed_queue(first)
        if winner == "recovery":
            assert consumed["consumed_run_id"] == candidate["run_id"]
            journey.release(admission)
            reply = await submitting
            assert reply.status_code == 409, reply.text
            assert await journey.queued(source) == []
            second = await journey.queue(source, cases[1])
        else:
            assert (await live.http.get(f"/api/v1/runs/{candidate['run_id']}")).status_code == 404
        assert journey.observations(cases[1]) == []
    journey.release(next_model)
    parent = await journey.finish_queue(first)
    following = await journey.finish_queue(second)
    assert parent["parent_run_id"] == completed["id"] and parent["output_text"] == cases[0]["token"]
    assert following["parent_run_id"] == parent["id"] and following["output_text"] == cases[1]["token"]
    assert len(await journey.thread_runs(source)) == 3


@pytest.mark.parametrize("winner", ["interrupt", "handoff"])
async def test_interrupt_and_prepared_queue_handoff_cannot_terminalize_each_others_run(control, winner):
    journey, live = control, control.live
    root = await journey.start(await journey.case())
    parent = await live.finish(root["run_id"])
    case = await journey.case(effect=True)
    model = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=2)
    source = await journey.accept(*await journey.command(parent, "continue", case=case))
    await journey.reached(model)
    later = await journey.case(effect=True)
    row = await journey.queue(source, later)
    recovery = journey.arm("recovery", "control.queue_recovery", role="control", thread_id=source["thread_id"])
    handoff = journey.arm("handoff", "queue.before_commit", run_id=source["run_id"])
    interrupt = journey.arm("interrupt", "control.interrupt_prepared", role="control", run_id=source["run_id"])
    next_model = journey.arm("next-model", "model.request", role="control", case_id=later["case_id"], request=1)
    journey.release(model)
    candidate = await journey.reached(handoff)
    path, body = await journey.command(source, "interrupt")
    async with journey.post_in_flight(path, body) as cancelling:
        await journey.reached(interrupt)
        assert not cancelling.done() and (await journey.queue_row(row))["state"] == "queued"
        if winner == "interrupt":
            journey.release(interrupt)
            reply = await cancelling
            assert reply.status_code == 202, reply.text
            sealed = await live.finish(source["run_id"], "cancelled")
            assert (await live.thread(source["thread_id"]))["head_run_id"] == parent["id"]
            assert await journey.queue_row(row) == row
            journey.release(handoff)
            journey.release(recovery)
        else:
            journey.release(handoff)
            await journey.consumed_queue(row)
            sealed = await live.finish(source["run_id"])
        await journey.reached(next_model)
        consumed = await journey.consumed_queue(row)
        if winner == "handoff":
            assert consumed["consumed_run_id"] == candidate["successor_run_id"]
            journey.release(interrupt)
            reply = await cancelling
            assert reply.status_code == 409, reply.text
        else:
            assert consumed["consumed_run_id"] != candidate["successor_run_id"]
            assert (await live.http.get(f"/api/v1/runs/{candidate['successor_run_id']}")).status_code == 404
        following = await live.run(consumed["consumed_run_id"])
        assert following["status"] == "running"
        assert following["parent_run_id"] == (parent["id"] if winner == "interrupt" else sealed["id"])
    journey.release(next_model)
    result = await journey.finish_queue(row)
    assert later["token"] in result["output_text"] and journey.effects(later) == [later["token"]]
    assert journey.effects(case) == [case["token"]]
    assert await live.run(source["run_id"]) == sealed and await live.run(parent["id"]) == parent
    assert len(await journey.thread_runs(source)) == 3


@pytest.mark.parametrize("operation", ["retry", "continue"])
@pytest.mark.parametrize("winner", ["branch", "recovery"])
async def test_background_queue_consumption_and_explicit_branch_commit_one_advancement(control, operation, winner):
    journey, live, lab = control, control.live, control.lab
    first = await journey.start(await journey.case())
    historical = await live.finish(first["run_id"])
    second = await journey.accept(*await journey.command(historical, "continue", case=await journey.case()))
    head = await live.finish(second["run_id"])
    case = await journey.case(effect=True)
    model = journey.arm("source", "tool.before_effect", case_id=case["case_id"])
    source = await journey.accept(*await journey.command(head, "continue", case=case))
    await journey.reached(model)
    later = await journey.case()
    row = await journey.queue(source, later)
    start = journey.arm(
        "recovery-start", "control.queue_recovery", role="control", times=100, thread_id=source["thread_id"]
    )
    await journey.post(*await journey.command(source, "interrupt"), expected=202)
    cancelled = await live.finish(source["run_id"], "cancelled")
    # Interrupt commits before the Worker necessarily observes cancellation.
    # Keep the tool paused until that Worker exits so only Retry can execute it.
    await lab.stop(lab.workers[0])
    journey.release(model)
    assert journey.effects(case) == []
    before = await live.thread(source["thread_id"])
    branch_case = await journey.case()
    target = cancelled if operation == "retry" else historical
    branch = journey.arm(
        "branch",
        "control.state_published",
        role="control",
        **({"retry_of_run_id": cancelled["id"]} if operation == "retry" else {"parent_run_id": historical["id"]}),
    )
    recovery = journey.arm(
        "recovery", "queue.consume_prepared", role="control", consumer="recovery", thread_id=before["id"]
    )
    recovered = journey.arm(
        "recovery-finished",
        "queue.consume_finished",
        role="control",
        action="observe",
        consumer="recovery",
        thread_id=before["id"],
        succeeded=winner == "recovery",
    )
    async with journey.post_in_flight(*await journey.command(target, operation, case=branch_case)) as branching:
        branch_candidate = await journey.reached(branch)
        journey.release(start)
        recovery_candidate = await journey.reached(recovery)
        assert branch_candidate["run_id"] != recovery_candidate["run_id"]
        assert await live.thread(before["id"]) == before and await journey.queue_row(row) == row
        if winner == "branch":
            journey.release(branch)
            reply = await branching
            assert reply.status_code == 202, reply.text
            accepted = reply.json()
            live.track(accepted)
            assert await journey.queue_row(row) == row
        journey.release(recovery)
        await journey.reached(recovered)
        if winner == "recovery":
            consumed = await journey.consumed_queue(row)
            assert consumed["consumed_run_id"] == recovery_candidate["run_id"]
            journey.release(branch)
            reply = await branching
            assert reply.status_code == 409, reply.text
        selected = await live.thread(before["id"])
        assert selected["version"] == before["version"] + 1
        assert selected["queue_version"] == before["queue_version"] + int(winner == "recovery")
        losing_id = recovery_candidate["run_id"] if winner == "branch" else branch_candidate["run_id"]
        assert (await live.http.get(f"/api/v1/runs/{losing_id}")).status_code == 404
        assert len(await journey.thread_runs(source)) == 4
    await lab.start_worker()
    if winner == "branch":
        advanced = await live.finish(accepted["run_id"])
        assert advanced["parent_run_id"] == (head["id"] if operation == "retry" else historical["id"])
        assert advanced["retry_of_run_id"] == (cancelled["id"] if operation == "retry" else None)
        expected_parent = advanced["id"]
    else:
        expected_parent = head["id"]
    following = await journey.finish_queue(row)
    assert following["parent_run_id"] == expected_parent and following["output_text"] == later["token"]
    assert journey.effects(case) == ([case["token"]] if winner == "branch" and operation == "retry" else [])
    assert len(await journey.thread_runs(source)) == (5 if winner == "branch" else 4)
    assert await live.run(cancelled["id"]) == cancelled
    assert await live.run(historical["id"]) == historical and await live.run(head["id"]) == head
