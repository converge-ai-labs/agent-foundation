"""Case 9: upstream authentication, read timeout and tool retry exhaustion."""

import pytest

from .stream import assert_stream

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "scenario,counter",
    [
        ("model_error", "injected_errors"),
        ("model_timeout", "model_timeouts"),
        ("tool_error", "effect_attempts"),
    ],
)
async def test_dependency_failure_is_bounded_and_releases_capacity(round_two, scenario, counter):
    live = round_two.client
    case = await live.case(scenario)
    body = live.start_body(case)
    if scenario == "model_timeout":
        body["agent_id"] = live.config["timeout_agent_id"]
    receipt = await live.request(
        "POST",
        f"/api/v1/workspaces/{live.config['workspace_id']}/runs",
        expected=202,
        headers={"Idempotency-Key": case["case_id"]},
        json=body,
    )
    live.track(receipt)
    failed = await live.finish(receipt["run_id"], "failed")
    assert failed["failure"]["code"] and failed["failure"]["message"]
    evidence = await live.evidence(case)
    assert evidence[counter] >= 1, "Failure was not injected at the intended dependency"
    assert evidence["effects"] == 0
    if scenario == "tool_error":
        assert evidence["effect_attempts"] == 2, "Agent tool retry limit was not enforced"
    attempts = await round_two.attempts(failed["id"])
    assert attempts and all(item["status"] == "failed" for item in attempts)
    assert_stream(await live.events(failed["id"]), failed["id"], outcome="failed")
    recovery = await live.start(await live.case("basic"))
    await live.finish(recovery["run_id"])
    assert await live.run(failed["id"]) == failed
    assert await round_two.attempts(failed["id"]) == attempts
