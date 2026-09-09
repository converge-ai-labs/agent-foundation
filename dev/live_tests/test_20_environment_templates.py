"""Case 20: versioned recipes, preparation timing and simultaneous first file use."""

import json

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("preparation", ["on_run", "on_use"])
async def test_template_version_and_preparation_reach_harness(management, preparation):
    journey, live = management, management.live
    template, recipe, root = await journey.environment_template(preparation=preparation)
    (root / "proof.txt").write_text("TEMPLATE_ONE")
    second_root = journey.lab.root / "second-template-target"
    second_root.mkdir()
    (second_root / "proof.txt").write_text("TEMPLATE_TWO")
    second_recipe = {**recipe, "configuration": {**recipe["configuration"], "root": {"path": str(second_root)}}}
    revision = await journey.post(
        f"/api/v1/environment-templates/{template['id']}/revisions",
        {
            **second_recipe,
            "expected_version": template["version"],
        },
    )
    for selector, expected_revision, proof in (
        ({"template_id": template["id"], "version": 1}, template["current_revision_id"], "TEMPLATE_ONE"),
        ({"template_id": template["id"]}, revision["id"], "TEMPLATE_TWO"),
    ):
        # Both first accesses are sent in the same model turn and execute concurrently.
        case = await journey.case(
            gate_at=0,
            parallel_steps=[
                {"tool": "view", "arguments": {"file_path": "/workspace/proof.txt"}},
                {"tool": "view", "arguments": {"file_path": "/workspace/proof.txt"}},
            ],
        )
        receipt = await journey.start(case, environment=selector)
        await journey.ready(case, receipt["run_id"])
        run = await live.run(receipt["run_id"])
        path = f"/api/v1/environments/{run['environment_id']}"
        before = await live.request("GET", path)
        assert before["template_revision_id"] == expected_revision
        assert before["status"] == ("unprepared" if preparation == "on_use" else "running")
        assert before["generation"] == (0 if preparation == "on_use" else 1)
        await live.release(case)
        result = await live.finish(receipt["run_id"])
        observations = journey.observations(case)
        messages = [message for message in observations[-1]["body"]["messages"] if message.get("role") == "tool"]
        assert len(messages) == 2 and all(proof in json.dumps(message["content"]) for message in messages)
        assert proof in result["output_text"]
        assert (await live.request("GET", path))["generation"] == 1
        assert result["environment_id"] == before["id"]
    environments = await live.collection(journey.base + "/environments")
    assert len(environments) == 2 and len({value["id"] for value in environments}) == 2


async def test_unused_lazy_environment_is_not_prepared(management):
    journey, live = management, management.live
    template, _, _ = await journey.environment_template(preparation="on_use")
    agent_id = live.config["agent_id"]
    await journey.patch(f"/api/v1/agents/{agent_id}", {"default_environment_template_id": template["id"]})
    case = await journey.case()
    receipt = await journey.start(case)
    result = await live.finish(receipt["run_id"])
    environment = await live.request("GET", f"/api/v1/environments/{result['environment_id']}")
    assert environment["status"] == "unprepared" and environment["generation"] == 0
    assert (await live.thread(result["thread_id"]))["default_environment_id"] == environment["id"]
    explicit_none = await journey.start(await journey.case(), environment=None)
    assert (await live.finish(explicit_none["run_id"]))["environment_id"] is None
