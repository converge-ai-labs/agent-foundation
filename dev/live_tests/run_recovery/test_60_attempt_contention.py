"""4/8 real claimants: competing scan windows, slot bounds, lease takeover and fencing."""

import json
import signal
from datetime import UTC, datetime

import pytest

from ..control.contention_support import (
    RELEASE_OBSERVATION,
    assert_lifecycle,
    contention_case,
    contention_metrics,
    lifecycle,
    query,
)

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("claimants", [4, 8], ids=lambda n: f"claimants-{n}")
@contention_case(
    "{claimants} worker processes scan the same candidate; distinct attempts fill exactly {claimants} slots"
)
async def test_multiworker_claim_contention_fills_slots_without_duplicate_attempts(control, claimants):
    journey, live, lab = control, control.live, control.lab
    claims = journey.arm("claimants", "control.before_claim", times=claimants)
    cases = [await journey.case() for _ in range(2 * claimants)]
    gates = [
        journey.arm(f"model-{index}", "model.request", role="control", case_id=case["case_id"], request=1)
        for index, case in enumerate(cases)
    ]
    receipts = [await journey.start(case) for case in cases]
    for _ in range(claimants - 1):
        await lab.start_worker()
    hits = [await journey.reached(claims, hit=index) for index in range(1, claimants + 1)]
    assert {hit["pid"] for hit in hits} == {worker.pid for worker in lab.workers}
    assert {hit["run_id"] for hit in hits} == {receipts[0]["run_id"]}
    assert not list(claims.glob("finished-*.json"))

    async def ownership():
        return [await lab.attempts(receipt["run_id"]) for receipt in receipts]

    assert all(not group for group in await ownership())
    with contention_metrics().measure(
        "claim_release_to_all_slots_owned",
        f"Cohort of {claimants} competing worker schedulers",
        semantics=RELEASE_OBSERVATION + " One cohort observation (n=1), not one timing per claimant.",
    ):
        journey.release(claims)
        groups = await live.wait(
            ownership, lambda groups: sum(bool(group) for group in groups) == claimants, "all slots"
        )
    assert all(len(group) <= 1 for group in groups)
    # Every occupied slot must reach the held model, then one further scan
    # window must retain the exact same Attempt identities.
    for index, group in enumerate(groups):
        if group:
            await journey.reached(gates[index])

    async def identities():
        return [[attempt["id"] for attempt in group] for group in await ownership()]

    await live.assert_stable(identities, await identities(), seconds=1)
    owners = await query(
        journey,
        "SELECT id, worker_id FROM run_attempts WHERE organization_id = %s AND id = ANY(%s)",
        (live.config["organization_id"], [group[0]["id"] for group in groups if group]),
    )
    assert len(owners) == len({owner["worker_id"] for owner in owners}) == claimants
    for gate in gates:
        journey.release(gate)
    for receipt in receipts:
        await live.finish(receipt["run_id"])
    groups = await ownership()
    events = await lifecycle(journey, [receipt["run_id"] for receipt in receipts])
    for receipt, case, attempts in zip(receipts, cases, groups, strict=True):
        assert len(attempts) == 1 and attempts[0]["attempt_number"] == 1
        assert len(journey.observations(case)) == 1
        assert_lifecycle(events, receipt["run_id"], "completed", attempts)


@pytest.mark.parametrize("claimants", [4, 8], ids=lambda n: f"replacement-claimants-{n}")
@contention_case("{claimants} replacement workers compete for one expired Run while its original owner is suspended")
async def test_expired_owner_loses_to_one_of_many_prepared_replacements(control, claimants):
    journey, lab = control, control.lab
    case = await journey.case(effect=True, idempotent=True)
    model = journey.arm("checkpointed-model", "model.request", role="control", case_id=case["case_id"], request=2)
    receipt = await journey.start(case)
    assert (await journey.reached(model))["tool_results"] == 1
    first = (await lab.attempts(receipt["run_id"]))[0]
    assert journey.effects(case) == [case["token"]]
    owner = lab.workers[0]
    lab.send(owner, signal.SIGSTOP)
    try:
        claims = journey.arm("replacement-claims", "control.before_claim", times=claimants, run_id=receipt["run_id"])
        rivals = [await lab.start_worker() for _ in range(claimants)]
        hits = [await journey.reached(claims, hit=index) for index in range(1, claimants + 1)]
        assert {hit["pid"] for hit in hits} == {worker.pid for worker in rivals}
        assert not list(claims.glob("finished-*.json"))
        execution = await journey.execution(receipt["run_id"])
        assert datetime.fromisoformat(execution["attempts"][0]["lease_expires_at"]) < datetime.now(UTC)
        assert len(await lab.attempts(receipt["run_id"])) == 1
        replacement = journey.arm(
            "replacement-model", "model.request", role="control", case_id=case["case_id"], request=3
        )
        with contention_metrics().measure(
            "replacement_claim_release_to_model",
            f"Winning replacement from {claimants} competing workers",
            semantics=RELEASE_OBSERVATION
            + " Starts after lease expiry and worker startup; one winning recovery observation (n=1).",
        ):
            journey.release(claims)
            assert (await journey.reached(replacement))["tool_results"] == 1
        attempts = await lab.attempts(receipt["run_id"])
        assert len(attempts) == 2
        failed = attempts[0]
        assert failed["status"] == "failed" and attempts[1]["attempt_number"] == 2
        assert attempts[1]["replaces_run_attempt_id"] == first["id"]
        assert attempts[1]["start_reason"] == "lease_expired"
        state = await journey.state(receipt["run_id"])
        assert state["fence"] == 2
        lab.send(owner, signal.SIGCONT)
        journey.release(model)
        # Drain the resurrected process while the current owner is held. The
        # old model reply may return, but cannot charge usage, seal, or publish.
        await lab.stop(owner)
        assert (await lab.attempts(receipt["run_id"]))[0] == failed
        assert await journey.state(receipt["run_id"]) == state
        with contention_metrics().measure(
            "replacement_model_release_to_settled",
            "Winning replacement completes after original owner is fenced and drained",
            semantics=RELEASE_OBSERVATION,
        ):
            journey.release(replacement)
            result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
        assert len(attempts) == 2 and attempts[0] == failed
        assert result["output_text"] == json.dumps(case["token"])
        assert len(journey.observations(case)) == 3
        assert_lifecycle(await lifecycle(journey, [result["id"]]), result["id"], "completed", attempts)
    finally:
        lab.send(owner, signal.SIGCONT)
