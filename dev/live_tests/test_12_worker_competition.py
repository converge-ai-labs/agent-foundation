"""Case 12: two independently running, one-slot Workers compete for slow Runs."""

import pytest

pytestmark = pytest.mark.anyio


async def test_two_workers_respect_capacity_and_each_run_has_one_owner(round_two):
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
