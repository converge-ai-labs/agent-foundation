"""P0: reauthorize accepted intent and replacement Attempts against current grants."""

import secrets
import signal

import pytest

from .round_two_lab import private_json
from .test_27_connectivity_execution import connection, requests

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("window", ["before_claim", "recovery"])
@pytest.mark.parametrize("revocation", ["disable", "role"])
@pytest.mark.parametrize("run_faults", [{"identity_management": True}], indirect=True)
async def test_current_principal_authority_is_rechecked_before_execution(run_faults, window, revocation):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    account = await journey.post(journey.base + "/service-accounts", {"name": "Run fault principal", "role": "runner"})
    token = secrets.token_urlsafe(32)
    private_json(lab.root / "fault-principals.json", [{"id": account["id"], "token": token}])
    case = await journey.case(effect=True)
    if window == "before_claim":
        await lab.stop(lab.workers[0])
    else:
        barrier = journey.arm("model", "model.request", role="control", case_id=case["case_id"], request=1)
    receipt = await live.request(
        "POST",
        journey.base + "/runs",
        expected=202,
        headers={"Authorization": "Bearer " + token, "Idempotency-Key": case["case_id"]},
        json={**live.start_body(case), "agent_id": journey.agent_id},
    )
    live.track(receipt)
    if window == "recovery":
        await journey.reached(barrier)
        await lab.stop(lab.workers[0], signal.SIGKILL)
        journey.release(barrier)
    before = journey.observations(case)
    await live.request(
        "PATCH",
        f"/api/v1/service-accounts/{account['id']}",
        json={
            "expected_version": account["version"],
            "name": account["name"],
            "status": "disabled" if revocation == "disable" else "active",
            "role": "viewer" if revocation == "role" else "runner",
        },
    )
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, outcome="failed", case=case, effects=0)
    assert result["failure"] and attempts[-1]["harness_run_id"] is None
    assert len(attempts) == (2 if window == "recovery" else 1)
    assert journey.observations(case) == before, "Revoked authority reached another model request"
    # The primary administrator remains valid; revocation must not poison its Worker.
    following = await journey.start(await journey.case())
    await live.finish(following["run_id"])


@pytest.mark.parametrize("window", ["before_claim", "recovery"])
async def test_deleted_bound_secret_blocks_initial_or_replacement_preparation(run_faults, window):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    secret = await journey.post("/__live__/fault-secrets", {})
    agent = await journey.agent(
        secret_requirements=[{"key": "required-credential", "required": True}],
        plugins=[
            {
                "instance_name": "faults",
                "plugin_key": "live.run_faults",
                "config": {"root": live.config["workspace_root"]},
            }
        ],
    )
    case = await journey.case(effect=True)
    body = live.start_body(case)["input"]
    body["secret_bindings"] = [
        {"key": "required-credential", "credential": {"source": "workspace_secret", "secret_id": secret["id"]}}
    ]
    if window == "before_claim":
        await lab.stop(lab.workers[0])
    else:
        barrier = journey.arm("model", "model.request", role="control", case_id=case["case_id"], request=1)
    receipt = await journey.start(case, agent_id=agent["agent"]["id"], input=body)
    if window == "recovery":
        await journey.reached(barrier)
        await lab.stop(lab.workers[0], signal.SIGKILL)
        journey.release(barrier)
    before = journey.observations(case)
    deleted = await live.http.delete(f"/__live__/fault-secrets/{secret['id']}")
    assert deleted.status_code == 204
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, outcome="failed", case=case, effects=0)
    assert result["failure"] and attempts[-1]["harness_run_id"] is None
    assert journey.observations(case) == before
    assert "fixture-secret-value" not in str(result)


@pytest.mark.parametrize("kind", ["mcp", "connector"])
async def test_connection_revoked_during_model_request_prevents_tool_dispatch(run_faults, kind):
    journey, live = run_faults, run_faults.live
    resource = await connection(journey, kind)
    agent = await journey.agent(
        **{kind + "_tools": [{kind + "_connection_id": resource["id"], "tools": ["live_echo"]}]}
    )
    case = await journey.case(steps=[{"tool": "live_echo", "arguments": {"value": "must-not-dispatch"}}])
    barrier = journey.arm("before-dispatch", "model.request", role="control", case_id=case["case_id"], request=1)
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    await journey.reached(barrier)
    path = f"/api/v1/{kind}-connections/{resource['id']}"
    current = await live.request("GET", path)
    await journey.post(
        path + ("/disable" if kind == "mcp" else "/revoke"), {"expected_version": current["version"]}, expected=200
    )
    journey.release(barrier)
    result = await live.wait(
        lambda: live.run(receipt["run_id"]), lambda row: row["sealed_at"] is not None, "revoked dispatch result"
    )
    assert result["status"] in {"completed", "failed"}
    # Harness may handle a denied tool result or terminate. Neither path may
    # claim the remote operation succeeded or send the revoked request.
    assert "REMOTE:must-not-dispatch" not in (result["output_text"] or "")
    dispatches = [
        entry
        for entry in requests(journey, kind)
        if entry["body"].get("method") == "tools/call" or "/tools/execute/" in entry["path"]
    ]
    assert dispatches == []
