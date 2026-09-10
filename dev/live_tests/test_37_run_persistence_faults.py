"""P0/P1: uncheckpointed effects, uncertain publication and damaged recovery state."""

import signal
from uuid import uuid4

import pytest

from .stream import assert_stream

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("point", "idempotent", "effect_count"),
    [
        ("tool.before_effect", False, 1),
        ("tool.after_effect", False, 2),
        ("tool.after_effect", True, 1),
    ],
)
async def test_worker_crash_before_checkpoint_obeys_tool_owned_idempotency(run_faults, point, idempotent, effect_count):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    case = await journey.case(effect=True, idempotent=idempotent)
    barrier = journey.arm("tool-crash", point, case_id=case["case_id"])
    receipt = await journey.start(case)
    await journey.reached(barrier)
    original = await live.run(receipt["run_id"])
    assert journey.effects(case) == ([] if point == "tool.before_effect" else [case["token"]])
    await lab.stop(lab.workers[0], signal.SIGKILL)
    journey.release(barrier)
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, case=case, effects=effect_count)
    assert len(attempts) == 2 and attempts[1]["start_reason"] == "lease_expired"
    assert attempts[0]["status"] == "failed" and attempts[1]["status"] == "succeeded"
    assert attempts[1]["replaces_run_attempt_id"] == attempts[0]["id"]
    for field in ("id", "thread_id", "session_id", "input", "agent_revision_id"):
        assert result[field] == original[field]
    assert case["token"] in result["output_text"]
    assert (await live.evidence(case))["effect_attempts"] == 2
    assert_stream(await live.events(result["id"]), result["id"])


@pytest.mark.parametrize("kind", ["progress", "completed"])
async def test_lost_object_write_response_reconciles_without_repeating_execution(run_faults, kind):
    journey, live = run_faults, run_faults.live
    fault = journey.arm(
        "lost-write",
        "state.put_after",
        kind=kind,
        action="unavailable",
    )
    case = await journey.case(effect=True)
    receipt = await journey.start(case)
    reached = await journey.reached(fault)
    assert reached["run_id"] == receipt["run_id"]
    result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
    assert len(attempts) == 1, "A reconciled write must not create another Attempt"
    assert len(journey.observations(case)) == 2
    assert case["token"] in result["output_text"]
    assert (await journey.state(result["id"]))["digest"] == result["sealed_state_digest_sha256"]
    assert_stream(await live.events(result["id"]), result["id"])


async def test_initial_write_response_loss_retries_acceptance_with_same_idempotency_key(run_faults):
    journey, live = run_faults, run_faults.live
    fault = journey.arm("initial-response", "state.put_after", role="control", kind="initial", action="unavailable")
    case = await journey.case(effect=True)
    key = uuid4().hex
    body = {**live.start_body(case), "agent_id": journey.agent_id}
    response = await live.http.post(journey.base + "/runs", headers={"Idempotency-Key": key}, json=body)
    assert response.status_code == 503
    evidence = await journey.reached(fault)
    assert await journey.runs() == [], "Unfinished publication must not accept a Run"
    # No acceptance committed, so its object is an orphan, not a stable Run ID.
    # Retrying the same command may allocate a fresh Run-owned initial key.
    receipt = await journey.post(journey.base + "/runs", body, key=key, expected=202)
    live.track(receipt)
    assert (await live.http.get(f"/api/v1/runs/{evidence['run_id']}")).status_code == 404
    assert await journey.post(journey.base + "/runs", body, key=key, expected=202) == receipt
    result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
    assert len(attempts) == 1 and len(await journey.runs()) == 1
    assert case["token"] in result["output_text"]


@pytest.mark.parametrize("point", ["checkpoint.after", "outcome.verified"])
async def test_terminal_candidate_survives_worker_loss_before_relational_seal(run_faults, point):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    barrier = journey.arm("before-seal", point, kind="completed")
    case = await journey.case(effect=True)
    receipt = await journey.start(case)
    await journey.reached(barrier)
    candidate = await journey.state(receipt["run_id"])
    active = await live.run(receipt["run_id"])
    assert candidate["kind"] == "completed" and active["status"] == "running"
    assert active["sealed_at"] is None
    before = journey.observations(case)
    await lab.stop(lab.workers[0], signal.SIGKILL)
    journey.release(barrier)
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
    assert len(attempts) == 2 and attempts[1]["harness_run_id"] is None
    assert attempts[1]["status"] == "succeeded"
    assert journey.observations(case) == before, "Outcome adoption re-entered the model"
    assert result["sealed_state_digest_sha256"] == candidate["digest"]


@pytest.mark.parametrize("damage", ["missing", "invalid_json", "schema", "harness_schema", "digest"])
async def test_invalid_recovery_state_fails_before_more_model_or_tool_work(run_faults, damage):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    case = await journey.case(effect=True)
    barrier = journey.arm("completed-tool", "model.request", role="control", case_id=case["case_id"], request=2)
    receipt = await journey.start(case)
    await journey.reached(barrier)
    before = journey.observations(case)
    assert (await journey.state(receipt["run_id"]))["kind"] == "progress"
    if damage == "invalid_json":
        async with journey.outsider() as outsider:
            for suffix in ("state", "execution"):
                response = await outsider.get(f"/__live__/faults/runs/{receipt['run_id']}/{suffix}")
                assert response.status_code == 403
            response = await outsider.post(
                f"/__live__/faults/runs/{receipt['run_id']}/damage", json={"kind": "missing"}
            )
            assert response.status_code == 403
    await lab.stop(lab.workers[0], signal.SIGKILL)
    changed = await live.request("POST", f"/__live__/faults/runs/{receipt['run_id']}/damage", json={"kind": damage})
    assert changed["damage"] == damage
    journey.release(barrier)
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, outcome="failed", case=case, effects=1)
    assert result["failure"]["code"] and result["output_text"] is None
    assert len(attempts) == 2 and attempts[1]["harness_run_id"] is None
    assert journey.observations(case) == before
    following = await journey.start(await journey.case())
    await live.finish(following["run_id"])
    assert await live.run(result["id"]) == result


async def test_plugin_removed_between_attempts_fails_recovery_without_default_substitution(run_faults):
    journey, lab = run_faults, run_faults.lab
    case = await journey.case(effect=True)
    barrier = journey.arm("tool-checkpoint", "model.request", role="control", case_id=case["case_id"], request=2)
    receipt = await journey.start(case)
    await journey.reached(barrier)
    await lab.stop(lab.workers[0], signal.SIGKILL)
    lab.config["run_faults"]["omit_plugin"] = True
    journey.save_config()
    journey.release(barrier)
    before = journey.observations(case)
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, outcome="failed", case=case, effects=1)
    assert result["failure"]["code"] == "plugin_factory_missing"
    assert len(attempts) == 2 and attempts[1]["harness_run_id"] is None
    assert journey.observations(case) == before


@pytest.mark.parametrize("run_faults", [{"plugin_state_version": "1"}], indirect=True)
async def test_incompatible_installed_plugin_state_cannot_resume_execution(run_faults):
    journey, lab = run_faults, run_faults.lab
    case = await journey.case(effect=True)
    barrier = journey.arm("persisted-plugin", "model.request", role="control", case_id=case["case_id"], request=2)
    receipt = await journey.start(case)
    await journey.reached(barrier)
    before = journey.observations(case)
    await lab.stop(lab.workers[0], signal.SIGKILL)
    lab.config["run_faults"]["plugin_state_version"] = "2"
    journey.save_config()
    journey.release(barrier)
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, outcome="failed", case=case, effects=1)
    assert len(attempts) == 2 and result["failure"] and result["output_text"] is None
    assert journey.observations(case) == before, "Incompatible plugin state reached another model request"
