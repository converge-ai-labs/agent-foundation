"""Case 12: independent Workers compete for one Run and respect slot capacity."""

import logging
import signal

import pytest

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.parametrize("race_round", range(1, 101), ids=lambda value: f"round-{value:03d}"),
]
logger = logging.getLogger(__name__)


async def test_four_workers_race_for_one_run(round_two, race_round):
    lab, live = round_two, round_two.client
    for _ in range(3):
        await lab.start_worker()
    for worker in lab.workers:
        lab.send(worker, signal.SIGSTOP)
    try:
        case = await live.case("checkpoint")
        receipt = await live.start(case)
        assert await lab.attempts(receipt["run_id"]) == []
    finally:
        for worker in lab.workers:
            lab.send(worker, signal.SIGCONT)
    await lab.wait_evidence(case, "checkpoint_ready", run_id=receipt["run_id"])

    async def owners():
        return [item["id"] for item in await lab.attempts(receipt["run_id"])]

    assert len(await owners()) == 1
    await live.assert_stable(owners, await owners(), seconds=2)
    await live.release(case)
    await live.finish(receipt["run_id"])
    attempts = await lab.attempts(receipt["run_id"])
    assert len(attempts) == 1 and attempts[0]["status"] == "succeeded"
    assert attempts[0]["attempt_number"] == 1
    evidence = await live.evidence(case)
    assert evidence["effects"] == 1 and evidence["model_requests"] == 2
    logger.info(
        "Four-worker claim: round=%s/100 run=%s winner=%s attempts=1 effects=1",
        race_round,
        receipt["run_id"],
        attempts[0]["id"],
    )


async def test_two_workers_respect_capacity_and_each_run_has_one_owner(round_two, race_round):
    lab, live = round_two, round_two.client
    cases = [await live.case("gate") for _ in range(4)]
    receipts = [await live.start(case) for case in cases]
    await lab.wait_evidence(cases[0], "gate_ready", run_id=receipts[0]["run_id"])

    async def admitted():
        return [await lab.attempts(receipt["run_id"]) for receipt in receipts]

    assert sum(bool(items) for items in await admitted()) == 1
    await lab.start_worker()
    await live.wait(admitted, lambda groups: sum(bool(items) for items in groups) == 2, "both Worker slots occupied")

    # A full second scan window must still leave two Runs queued, rather than creating spare Attempts.
    async def owners():
        return [[item["id"] for item in group] for group in await admitted()]

    await live.assert_stable(owners, await owners(), seconds=1)
    for case in cases:
        await live.release(case)
    for receipt in receipts:
        await live.finish(receipt["run_id"])
    groups = await admitted()
    assert all(
        len(items) == 1 and items[0]["status"] == "succeeded" and items[0]["attempt_number"] == 1 for items in groups
    )
    assert len({items[0]["harness_run_id"] for items in groups}) == 4
    for case in cases:
        assert (await live.evidence(case))["model_requests"] == 1
    logger.info(
        "Two-worker capacity: round=%s/100 runs=%s attempts_per_run=1",
        race_round,
        [receipt["run_id"] for receipt in receipts],
    )
