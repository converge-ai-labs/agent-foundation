"""Case 19: build/install a custom wheel, then bind and execute it in the production Worker."""

import hashlib
import importlib.util
import logging

import pytest

from ..infrastructure.management_support import ManagementJourney, has_tool, last_tool_result
from ..infrastructure.round_two_lab import open_lab
from .plugin_image import container_url, events, installed_worker, plugin_image

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


@pytest.fixture
async def packaged_plugin(request):
    if not request.config.getoption("--live-plugin-image"):
        pytest.skip("Opt in with make live-test-plugin-image; images are not built by default")
    assert importlib.util.find_spec("a13n_live_plugin") is None, "Fixture package must not be installed in the host"
    async with plugin_image() as image:
        async with open_lab(suite="management") as lab:
            await lab.stop(lab.workers[0])
            journey = ManagementJourney(lab)
            if container_url(lab.config["control_url"]) != lab.config["control_url"]:
                # Docker Desktop resolves this name only inside containers. The Worker
                # still performs the normal DNS/allowlist validation at request time.
                await lab.stop(lab.control)
                lab.environment["A13N_SERVICE_MODEL_RESOLVE_DNS_ON_SAVE"] = "false"
                lab.control = await lab.spawn("dev.live_tests.manage", "control")
                await lab.ready(lab.control, lab.config["control_url"])
            await journey.patch(
                journey.base + "/model-providers/" + lab.config["model_provider_id"],
                {
                    "configuration": {
                        "base_url": container_url(lab.config["control_url"]) + "/__live__/model/v1",
                        "auth_mode": "bearer",
                    }
                },
            )
            yield journey, image


async def test_packaged_plugin_image_binding_and_harness_execution(packaged_plugin):
    journey, image = packaged_plugin
    live = journey.live
    selection = {
        "instance_name": "audit",
        "plugin_key": "live.packaged",
        "config": {"root": "/plugin-effects", "label": "first"},
    }
    # Control creates and reads the authored selection without the package on its import path.
    revision = await journey.agent(plugins=[selection])
    assert revision["revision"]["config"]["plugins"] == [selection]
    async with installed_worker(journey, image) as root:
        for label in ("first", "updated"):
            if label == "updated":
                selection = {**selection, "config": {**selection["config"], "label": label}}
                revision = await journey.revision(revision["agent"], plugins=[selection])
            case = await journey.case()
            journey.plan(case, steps=[{"tool": "packaged_effect", "arguments": {"value": case["token"]}}])
            receipt = await journey.start(case, agent_id=revision["agent"]["id"])
            result = await live.finish(receipt["run_id"])
            observed = journey.observations(case)
            assert len(observed) == 2 and has_tool(observed[0], "packaged_effect")
            tool = last_tool_result(observed[-1])
            expected = hashlib.sha256(f"{label}:{case['token']}".encode()).hexdigest()
            assert tool["digest"] == expected and expected in result["output_text"]
            assert tool["label"] == label and tool["distribution_version"] == "0.0.0"
            assert tool["plugin_id"] == selection["instance_name"]
            assert "/site-packages/a13n_live_plugin/" in tool["module_file"] and tool["uid"] != 0
            assert result["agent_revision_id"] == revision["revision"]["id"]
            recorded = events(root)
            assert recorded[-2] == {"event": "tool", **tool}
            attempts = await journey.lab.attempts(receipt["run_id"])
            assert len(attempts) == 1 and attempts[0]["worker_build_id"] == "live-plugin-image"
            assert recorded[-1] == {
                "event": "result",
                "run_id": attempts[0]["harness_run_id"],
                "plugin_id": tool["plugin_id"],
                "status": "completed",
            }
            logger.info("Installed plugin executed run=%s label=%s digest=%s", receipt["run_id"], label, expected)
        assert len(events(root)) == 4, "Exactly one tool effect and one middleware result per Run"

        # Removing the binding through a Revision removes both the tool and the middleware.
        revision = await journey.revision(revision["agent"], plugins=[])
        plain = await journey.case()
        receipt = await journey.start(plain, agent_id=revision["agent"]["id"])
        await live.finish(receipt["run_id"])
        assert not has_tool(journey.observations(plain)[0], "packaged_effect")
        before = events(root)
        assert len(before) == 4

        invalid = await journey.agent(plugins=[{**selection, "config": {"root": "/plugin-effects"}}])
        case = await journey.case()
        receipt = await journey.start(case, agent_id=invalid["agent"]["id"])
        result = await live.finish(receipt["run_id"], "failed")
        assert result["failure"]["code"] == "plugin_factory_configuration_invalid"
        assert journey.observations(case) == [] and events(root) == before

    # Installed bytes alone grant no execution: a fresh process with no selected key fails preparation.
    async with installed_worker(journey, image, plugin_keys=()) as root:
        bound = await journey.agent(plugins=[selection])
        case = await journey.case()
        receipt = await journey.start(case, agent_id=bound["agent"]["id"])
        result = await live.finish(receipt["run_id"], "failed")
        assert result["failure"]["code"] == "plugin_factory_missing"
        assert journey.observations(case) == [] and events(root) == before
