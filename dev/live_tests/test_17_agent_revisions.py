"""Case 17: revision selection, accepted configuration freeze and per-Run overrides."""

import json

import pytest

from .round_two_resources import agent_config

pytestmark = pytest.mark.anyio


async def test_revision_and_override_reach_harness_without_drifting(management):
    journey, live = management, management.live
    first = await journey.agent(instructions="REVISION_ONE")
    agent_id = first["agent"]["id"]
    await journey.lab.stop(journey.lab.workers[0])
    accepted_case = await journey.case()
    accepted = await journey.start(accepted_case, agent_id=agent_id)
    assert (await live.run(accepted["run_id"]))["status"] == "accepted"
    second = await journey.revision(first["agent"], instructions="REVISION_TWO")
    await journey.lab.start_worker()
    frozen = await live.finish(accepted["run_id"])
    assert frozen["agent_revision_id"] == first["revision"]["id"]
    assert "REVISION_ONE" in json.dumps(journey.observations(accepted_case)[0]["body"]["messages"])
    assert "REVISION_TWO" not in json.dumps(journey.observations(accepted_case))

    for values, expected_revision, marker in (
        ({}, second["revision"]["id"], "REVISION_TWO"),
        ({"agent_revision_id": first["revision"]["id"]}, first["revision"]["id"], "REVISION_ONE"),
        ({"config_override": {"instructions": "RUN_OVERRIDE"}}, second["revision"]["id"], "RUN_OVERRIDE"),
        ({}, second["revision"]["id"], "REVISION_TWO"),
    ):
        case = await journey.case()
        receipt = await journey.start(case, agent_id=agent_id, **values)
        result = await live.finish(receipt["run_id"])
        assert result["agent_revision_id"] == expected_revision
        assert marker in json.dumps(journey.observations(case)[0]["body"]["messages"])
        assert result["output_text"] == case["token"]
    stored = await live.request("GET", f"/api/v1/agent-revisions/{second['revision']['id']}")
    assert stored["config"]["instructions"] == "REVISION_TWO"
    assert await live.run(frozen["id"]) == frozen

    # Stale management writes and non-overridable fields cannot create executions.
    before = await journey.runs()
    await journey.post(
        f"{journey.base}/agents/{agent_id}/revisions",
        {"expected_version": first["agent"]["version"], "config": agent_config(instructions="STALE")},
        expected=409,
    )
    await journey.post(
        journey.base + "/runs",
        {
            **live.start_body(await journey.case()),
            "agent_id": agent_id,
            "config_override": {"secret_requirements": [{"key": "not-overridable"}]},
        },
        expected=400,
    )
    assert await journey.runs() == before
