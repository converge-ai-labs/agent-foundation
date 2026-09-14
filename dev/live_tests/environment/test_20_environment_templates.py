"""Case 20: versioned recipes, preparation timing and simultaneous first file use."""

import pytest

from .service_cases import assert_template_preparation

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("preparation", ["on_run", "on_use"])
async def test_template_version_and_preparation_reach_harness(management, preparation):
    template, recipe, root = await management.environment_template(preparation=preparation)
    second_root = management.lab.root / "second-template-target"
    second_root.mkdir()
    revised = {**recipe, "configuration": {**recipe["configuration"], "root": {"path": str(second_root)}}}
    await assert_template_preparation(
        management,
        template,
        revised,
        preparation=preparation,
        initial_access=recipe["access"],
        roots=(root, second_root),
    )


async def test_unused_lazy_environment_is_not_prepared(management):
    journey, live = management, management.live
    template, _, _ = await journey.environment_template(preparation="on_use")
    agent_id = live.config["agent_id"]
    await journey.patch(f"{journey.base}/agents/{agent_id}", {"default_environment_template_id": template["id"]})
    case = await journey.case()
    receipt = await journey.start(case)
    result = await live.finish(receipt["run_id"])
    environment = await live.request("GET", f"/api/v1/environments/{result['environment_id']}")
    assert environment["status"] == "unprepared" and environment["generation"] == 0
    assert (await live.thread(result["thread_id"]))["default_environment_id"] == environment["id"]
    explicit_none = await journey.start(await journey.case(), environment=None)
    assert (await live.finish(explicit_none["run_id"]))["environment_id"] is None
