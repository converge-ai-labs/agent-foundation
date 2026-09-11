"""Case 23: freeze Model execution intent but refresh the live Provider per request."""

import hashlib

import pytest

pytestmark = pytest.mark.anyio


async def test_model_settings_freeze_before_worker_claim(management):
    journey, live = management, management.live
    model_path = journey.base + "/models/" + live.config["model_id"]
    await journey.patch(model_path, {"settings": {"temperature": 0.2}, "upstream_model": "frozen-upstream"})
    await journey.lab.stop(journey.lab.workers[0])
    case = await journey.case()
    receipt = await journey.start(case)
    assert (await live.run(receipt["run_id"]))["status"] == "accepted"
    await journey.patch(model_path, {"settings": {"temperature": 0.8}, "upstream_model": "new-upstream"})
    await journey.lab.start_worker()
    await live.finish(receipt["run_id"])
    first = journey.observations(case)[0]["body"]
    assert first["temperature"] == 0.2 and first["model"] == "frozen-upstream"
    later = await journey.case()
    receipt = await journey.start(later)
    await live.finish(receipt["run_id"])
    body = journey.observations(later)[0]["body"]
    assert body["temperature"] == 0.8 and body["model"] == "new-upstream"


@pytest.mark.parametrize("change", ["rotate_and_move", "disable"])
async def test_provider_change_applies_to_next_request_in_same_run(management, change):
    journey, live = management, management.live
    case = await journey.case(gate_at=0)
    from ..infrastructure.round_two_lab import private_json

    private_json(
        journey.lab.root / "workspace" / case["case_id"] / "plan.json",
        {
            "gate_at": 0,
            "steps": [{"tool": "live_effect", "arguments": {"case_id": case["case_id"], "token": case["token"]}}],
        },
    )
    receipt = await journey.start(case)
    await journey.ready(case, receipt["run_id"])
    provider_path = journey.base + "/model-providers/" + live.config["model_provider_id"]
    if change == "disable":
        await journey.patch(provider_path, {"enabled": False})
    else:
        rotated = live.config["other_identity"]["token"]
        updated = await journey.patch(
            provider_path,
            {
                "credential": rotated,
                "configuration": {
                    "base_url": live.config["control_url"] + "/__live__/model-alternate/v1",
                    "auth_mode": "bearer",
                },
            },
        )
        assert rotated not in str(updated)
    await live.release(case)
    result = await live.finish(receipt["run_id"], "failed" if change == "disable" else "completed")
    observed = journey.observations(case)
    assert observed[0]["endpoint"] == "/__live__/model/v1/chat/completions"
    if change == "disable":
        assert len(observed) == 1, "Disabled Provider received another HTTP request"
        assert result["failure"]
    else:
        assert len(observed) == 2
        assert observed[1]["endpoint"] == "/__live__/model-alternate/v1/chat/completions"
        assert observed[1]["authorization_sha256"] == hashlib.sha256(("Bearer " + rotated).encode()).hexdigest()
        assert observed[0]["authorization_sha256"] != observed[1]["authorization_sha256"]
        assert case["token"] in result["output_text"]
