"""Case 18: uploaded wheel configuration becomes an executable Harness tool."""

import pytest

from .management_support import has_tool

pytestmark = pytest.mark.anyio


async def test_managed_plugin_configuration_controls_real_effect(management):
    journey, live = management, management.live
    case = await journey.case()
    plugins = live.config["resilience_plugins"]
    # Upload and exact version binding happened through HTTP in this test's lab.
    revision = await journey.agent(plugins=plugins)
    lock = revision["revision"]["resolved_plugin_versions"]
    assert len(lock) == 1 and lock[0]["plugin_version_id"] == plugins[0]["plugin_version_id"]
    assert lock[0]["config"] == plugins[0]["config"]
    from .round_two_lab import private_json

    private_json(
        journey.lab.root / "workspace" / case["case_id"] / "plan.json",
        {"steps": [{"tool": "live_effect", "arguments": {"case_id": case["case_id"], "token": case["token"]}}]},
    )
    receipt = await journey.start(case, agent_id=revision["agent"]["id"])
    result = await live.finish(receipt["run_id"])
    assert case["token"] in result["output_text"]
    assert (journey.lab.root / "workspace" / case["case_id"] / "effects").read_text().splitlines() == [case["token"]]
    assert has_tool(journey.observations(case)[0], "live_effect")
    assert (await live.evidence(case))["effect_attempts"] == 1

    plain = await journey.agent()
    unbound = await journey.case()
    receipt = await journey.start(unbound, agent_id=plain["agent"]["id"])
    await live.finish(receipt["run_id"])
    assert not has_tool(journey.observations(unbound)[0], "live_effect")
    assert (await live.evidence(unbound))["effects"] == 0
    # Invalid factory configuration is rejected before it can create an Agent.
    await journey.post(
        journey.base + "/agents",
        {
            "name": "Invalid plugin configuration",
            "config": {**plain["revision"]["config"], "plugins": [{**plugins[0], "config": {"unknown": "rejected"}}]},
        },
        expected=400,
    )
