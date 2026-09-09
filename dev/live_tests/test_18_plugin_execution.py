"""Case 18: installed plugin selections reach real Worker preparation and execution."""

import pytest

from .management_support import has_tool

pytestmark = pytest.mark.anyio


async def test_installed_plugin_configuration_controls_real_effect(management):
    journey, live = management, management.live
    case = await journey.case()
    plugins = live.config["resilience_plugins"]
    # Control preserves authored selections; the Worker owns the installed catalog.
    revision = await journey.agent(plugins=plugins)
    assert revision["revision"]["config"]["plugins"] == plugins
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


@pytest.mark.parametrize(
    ("override", "failure_code"),
    [
        pytest.param({"config": {}}, "plugin_factory_configuration_invalid", id="missing-root"),
        pytest.param(
            {"config": {"root": ".", "unknown": "rejected"}},
            "plugin_factory_configuration_invalid",
            id="unknown-field",
        ),
        pytest.param({"plugin_key": "live.missing"}, "plugin_factory_missing", id="missing-factory"),
    ],
)
async def test_worker_rejects_invalid_plugin_selection(management, override, failure_code):
    journey, live = management, management.live
    selection = {**live.config["resilience_plugins"][0], **override}
    # Control accepts bounded JSON without loading factories or checking their schema.
    revision = await journey.agent(plugins=[selection])
    assert revision["revision"]["config"]["plugins"] == [selection]
    case = await journey.case()
    receipt = await journey.start(case, agent_id=revision["agent"]["id"])
    result = await live.finish(receipt["run_id"], "failed")
    assert result["failure"]["code"] == failure_code
    assert journey.observations(case) == []
    evidence = await live.evidence(case)
    assert evidence["effects"] == 0 and evidence["effect_attempts"] == 0
