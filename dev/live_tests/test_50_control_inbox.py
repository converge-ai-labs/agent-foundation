"""Durable inbox identity, capacity, input invalidation and active-command replay."""

import asyncio
from uuid import uuid4

import pytest
import rfc8785

from .client import agent_input
from .control_support import user_texts
from .management_packages import upload

pytestmark = pytest.mark.anyio


async def test_distinct_steer_ids_preserve_identical_content_in_fifo(control):
    journey, live = control, control.live
    case = await journey.case()
    gate = journey.arm("first-model", "model.request", role="control", case_id=case["case_id"], request=1)
    receipt = await journey.start(case)
    await journey.reached(gate)
    token = uuid4().hex
    first, _ = await journey.steer(receipt["run_id"], token=token)
    second, _ = await journey.steer(receipt["run_id"], token=token)
    assert first["steer_id"] != second["steer_id"]
    assert [row["delivery_sequence"] for row in await journey.inbox(receipt["thread_id"])] == [1, 2]
    journey.release(gate)
    result = await live.finish(receipt["run_id"])
    assert result["output_text"] == f"STEERS:{token},{token}"
    rows = await journey.inbox(receipt["thread_id"])
    assert all(row["status"] == "consumed" and row["consumed_by_run_id"] == result["id"] for row in rows)
    assert "\n".join(user_texts(journey.observations(case)[-1])).count(token) == 2


@pytest.mark.parametrize("control", [{"inbox": {"max_pending_count": 2}}], indirect=True)
@pytest.mark.parametrize("release_capacity", ["consume", "cancel"])
async def test_concurrent_steer_overflow_is_atomic_and_capacity_is_reusable(control, release_capacity):
    journey, live = control, control.live
    case = await journey.case()
    gate = journey.arm("source", "model.request", role="control", case_id=case["case_id"], request=1)
    source = await journey.start(case)
    await journey.reached(gate)
    tokens = [uuid4().hex for _ in range(3)]
    replies = await asyncio.gather(
        *(
            live.http.post(
                f"/api/v1/runs/{source['run_id']}/steer",
                json=agent_input("LIVE_STEER " + token),
                headers={"Idempotency-Key": uuid4().hex},
            )
            for token in tokens
        )
    )
    assert sorted(reply.status_code for reply in replies) == [202, 202, 409], [reply.text for reply in replies]
    rows = await journey.inbox(source["thread_id"])
    assert len(rows) == 2 and [row["delivery_sequence"] for row in rows] == [1, 2]
    if release_capacity == "cancel":
        await journey.post(*await journey.command(source, "interrupt"), expected=202)
    journey.release(gate)
    settled = await live.finish(source["run_id"], "cancelled" if release_capacity == "cancel" else "completed")
    old_rows = await journey.inbox(source["thread_id"])
    assert all(row["status"] == ("superseded" if release_capacity == "cancel" else "consumed") for row in old_rows)
    later = await journey.case()
    following_gate = journey.arm("following", "model.request", role="control", case_id=later["case_id"], request=1)
    successor = await journey.accept(*await journey.command(settled, "submit", case=later))
    await journey.reached(following_gate)
    new_tokens = [(await journey.steer(successor["run_id"]))[1] for _ in range(2)]
    rows = await journey.inbox(source["thread_id"])
    assert [row["delivery_sequence"] for row in rows] == [1, 2, 3, 4]
    assert rows[:2] == old_rows
    journey.release(following_gate)
    completed = await live.finish(successor["run_id"])
    assert completed["output_text"] == "STEERS:" + ",".join(new_tokens)
    assert all(row["status"] == "consumed" for row in (await journey.inbox(source["thread_id"]))[2:])


@pytest.mark.parametrize("control", [{"inbox": {"max_pending_bytes": 512}}], indirect=True)
@pytest.mark.parametrize("delta", [-1, 0, 1])
async def test_steer_byte_budget_accepts_exact_limit_and_rejects_one_byte_over(control, delta):
    journey, live = control, control.live
    _, probe = await journey.waiting()
    await journey.post(f"/api/v1/runs/{probe['id']}/steer", agent_input("p"), expected=202)
    measured = len(rfc8785.dumps((await journey.inbox(probe["thread_id"]))[0]["payload"]))
    overhead = measured - 1
    case, waiting = await journey.waiting()
    text = "x" * (512 + delta - overhead)
    path = f"/api/v1/runs/{waiting['id']}/steer"
    await journey.post(path, agent_input(text), expected=202 if delta <= 0 else 409)
    rows = await journey.inbox(waiting["thread_id"])
    if delta > 0:
        assert rows == []
        accepted, _ = await journey.steer(waiting["id"])
        assert accepted["delivery_sequence"] == 1, "Rejected bytes reserved an inbox sequence"
    else:
        assert len(rows) == 1 and len(rfc8785.dumps(rows[0]["payload"])) == 512 + delta
    successor = await journey.accept(*await journey.command(waiting, "feedback"))
    await live.finish(successor["run_id"])
    assert all(row["status"] == "consumed" for row in await journey.inbox(waiting["thread_id"]))
    if delta <= 0:
        assert text in "\n".join(user_texts(journey.observations(case)[-1]))


async def test_deleted_binary_steer_source_fails_before_model_and_finalizes_pending_entry(control):
    journey, live = control, control.live
    asset = await upload(
        journey, "assets", b"binary-steer-evidence", params={"filename": "steer.txt", "media_type": "text/plain"}
    )
    environment, _ = await journey.environment()
    before = journey.arm("before-claim", "control.before_claim")
    case = await journey.case()
    receipt = await journey.start(case, environment={"environment_id": environment["id"]})
    await journey.reached(before)
    body = agent_input("Read the attached steer")
    body["content"].append(
        {"type": "binary", "source": {"type": "asset", "asset_id": asset["id"]}, "delivery": "environment_path"}
    )
    steer = await journey.post(f"/api/v1/runs/{receipt['run_id']}/steer", body, expected=202)
    assert (await live.http.delete(f"/api/v1/assets/{asset['id']}")).status_code == 204
    journey.release(before)
    failed, _ = await journey.assert_settled(receipt, outcome="failed")
    assert failed["failure"] and journey.observations(case) == []
    rows = await journey.inbox(receipt["thread_id"])
    assert [(row["id"], row["status"], row["consumed_by_run_id"]) for row in rows] == [
        (steer["steer_id"], "superseded", None)
    ]


@pytest.mark.parametrize("operation", ["steer", "interrupt"])
async def test_active_command_lost_reply_replays_committed_receipt_after_control_restart(control, operation):
    journey, live = control, control.live
    case = await journey.case(effect=True)
    tool = journey.arm("tool", "tool.after_effect", case_id=case["case_id"])
    receipt = await journey.start(case)
    await journey.reached(tool)
    path, body = await journey.command(receipt, operation)
    token = uuid4().hex
    if operation == "steer":
        body = agent_input("LIVE_STEER " + token)
    barrier = journey.arm("lost-reply", "control." + operation + "_committed", role="control", run_id=receipt["run_id"])
    key = uuid4().hex
    request = asyncio.create_task(live.http.post(path, json=body, headers={"Idempotency-Key": key}))
    try:
        await journey.reached(barrier)
        await journey.restart_control()
        journey.release(barrier)
        lost = (await asyncio.gather(request, return_exceptions=True))[0]
        assert isinstance(lost, Exception) or lost.status_code != 202
        repeated = await journey.post(path, body, expected=202, key=key)
        assert await journey.post(path, body, expected=202, key=key) == repeated
        journey.release(tool)
        result, _ = await journey.assert_settled(
            receipt, outcome="completed" if operation == "steer" else "cancelled", case=case, effects=1
        )
        if operation == "steer":
            assert result["output_text"] == "STEERS:" + token
            rows = await journey.inbox(receipt["thread_id"])
            assert len(rows) == 1 and rows[0]["id"] == repeated["steer_id"] and rows[0]["status"] == "consumed"
        assert await journey.post(path, body, expected=202, key=key) == repeated
        assert len(await journey.thread_runs(receipt)) == 1
    finally:
        journey.release(barrier)
        journey.release(tool)
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
