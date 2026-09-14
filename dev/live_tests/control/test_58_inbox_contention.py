"""8/32 steer writers contend for four slots, with real receipt publication/recovery."""

import asyncio
import signal
from uuid import uuid4

import pytest
import rfc8785

from ..infrastructure.client import agent_input
from .contention_support import (
    RELEASE_OBSERVATION,
    assert_lifecycle,
    contention_case,
    contention_metrics,
    inbox_budget,
    lifecycle,
    prepared_writers,
)
from .control_support import assert_tokens_once

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("control", [{"inbox": {"max_pending_count": 4}}], indirect=True)
@pytest.mark.parametrize("writers", [8, 32], ids=lambda n: f"writers-{n}")
@pytest.mark.parametrize("receipt_boundary", ["checkpoint.before", "checkpoint.after"])
@contention_case("{writers} steer writers compete for four inbox slots; owner fails at {receipt_boundary}")
async def test_inbox_contention_fifo_capacity_and_receipts_survive_owner_loss(control, writers, receipt_boundary):
    journey, live, lab = control, control.live, control.lab
    case = await journey.case()
    model = journey.arm("first-model", "model.request", role="control", case_id=case["case_id"], request=1)
    source = await journey.start(case)
    await journey.reached(model)
    before = await live.thread(source["thread_id"])
    tokens = [uuid4().hex for _ in range(writers)]
    commands = [
        (f"/api/v1/runs/{source['run_id']}/steer", agent_input("LIVE_STEER " + token), uuid4().hex) for token in tokens
    ]
    async with prepared_writers(
        journey,
        commands,
        point="control.steer_prepared",
        operation="steer_barrier_inclusive",
        actor="Steer HTTP writer",
        run_id=source["run_id"],
    ) as (
        barrier,
        tasks,
        _,
    ):
        assert await journey.inbox(source["thread_id"]) == []
        journey.release(barrier)
        replies = await asyncio.gather(*tasks)
    assert sorted(reply.status_code for reply in replies) == [202] * 4 + [409] * (writers - 4)
    accepted = {
        reply.json()["steer_id"]: (index, reply.json())
        for index, reply in enumerate(replies)
        if reply.status_code == 202
    }
    rows = await journey.inbox(source["thread_id"])
    assert len(rows) == 4 and {row["id"] for row in rows} == set(accepted)
    assert [row["delivery_sequence"] for row in rows] == [1, 2, 3, 4]
    expected_tokens = [tokens[accepted[row["id"]][0]] for row in rows]
    full = await inbox_budget(journey, source["thread_id"])
    assert full == {
        "pending_count": 4,
        "pending_bytes": sum(len(rfc8785.dumps(row["payload"])) for row in rows),
        "next_delivery_sequence": 5,
    }
    assert await live.thread(source["thread_id"]) == before
    for index, receipt in accepted.values():
        assert await journey.post(*commands[index][:2], key=commands[index][2], expected=202) == receipt
    assert await inbox_budget(journey, source["thread_id"]) == full
    publication = journey.arm("receipts", receipt_boundary, run_id=source["run_id"], receipts=4)
    journey.release(model)
    await journey.reached(publication)
    assert all(row["status"] == "pending" for row in await journey.inbox(source["thread_id"]))
    state = await journey.state(source["run_id"])
    assert len(state["receipts"]) == (4 if receipt_boundary == "checkpoint.after" else 0)
    await lab.stop(lab.workers[0], signal.SIGKILL)
    with contention_metrics().measure(
        "receipt_recovery_to_settled",
        "Replacement worker recovers four pending steer receipts",
        semantics=RELEASE_OBSERVATION
        + " Includes replacement process startup, residual lease expiry, and run completion.",
    ):
        journey.release(publication)
        await lab.start_worker()
        result, attempts = await journey.assert_settled(source)
    assert len(attempts) == 2 and attempts[1]["start_reason"] == "lease_expired"
    assert result["output_text"] == "STEERS:" + ",".join(expected_tokens)
    consumed = await journey.inbox(source["thread_id"])
    assert [row["id"] for row in consumed] == [row["id"] for row in rows]
    assert all(row["status"] == "consumed" and row["consumed_by_run_id"] == result["id"] for row in consumed)
    assert all(row["consumed_checkpoint_seq"] > 0 and row["consumed_state_digest_sha256"] for row in consumed)
    state = await journey.state(result["id"])
    assert [receipt["inbox_entry_id"] for receipt in state["receipts"]] == [row["id"] for row in rows]
    assert await inbox_budget(journey, source["thread_id"]) == {
        "pending_count": 0,
        "pending_bytes": 0,
        "next_delivery_sequence": 5,
    }
    assert_tokens_once(journey.observations(case)[-1], expected_tokens)
    assert_lifecycle(await lifecycle(journey, [result["id"]]), result["id"], "completed", attempts)
    # A rejected intent can use newly available capacity with its original key.
    later = await journey.case()
    gate = journey.arm("later-model", "model.request", role="control", case_id=later["case_id"], request=1)
    successor = await journey.accept(*await journey.command(result, "continue", case=later))
    await journey.reached(gate)
    loser = next(index for index, reply in enumerate(replies) if reply.status_code == 409)
    admitted = await journey.post(
        f"/api/v1/runs/{successor['run_id']}/steer", commands[loser][1], key=commands[loser][2], expected=202
    )
    assert admitted["delivery_sequence"] == 5
    journey.release(gate)
    following = await live.finish(successor["run_id"])
    assert following["output_text"] == "STEERS:" + tokens[loser]
    assert (await journey.inbox(source["thread_id"]))[:4] == consumed
    assert (await inbox_budget(journey, source["thread_id"]))["pending_count"] == 0
