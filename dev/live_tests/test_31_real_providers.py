"""Additional integration journeys; deterministic fault cases keep their fixtures."""

from uuid import uuid4

import pytest

from .client import agent_input

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("configured_provider", ["environment"], indirect=True)
async def test_configured_environment_executes_shell_and_reads_file(configured_provider):
    journey, environment = configured_provider
    proof = "REMOTE_" + uuid4().hex
    case = await journey.case(
        steps=[
            {
                "tool": "shell_exec",
                "arguments": {"command": f"printf '%s' '{proof}' > proof.txt", "yield_time_seconds": 5},
            },
            {"tool": "view", "arguments": {"file_path": "/workspace/proof.txt"}},
        ]
    )
    receipt = await journey.start(case, environment={"environment_id": environment["id"]})
    result = await journey.live.finish(receipt["run_id"])
    assert proof in result["output_text"]
    persisted = await journey.live.request("GET", f"/api/v1/environments/{environment['id']}")
    assert persisted["status"] == "running"


@pytest.mark.parametrize("configured_provider", ["connector"], indirect=True)
async def test_configured_connector_authentication_and_discovery(configured_provider):
    journey, provider = configured_provider
    path = f"/api/v1/connector-providers/{provider['id']}"
    tested = await journey.post(path + "/test", {"expected_version": provider["version"]}, expected=200)
    assert tested["status"] == "succeeded" and tested["verified_access"]
    discovered = await journey.post(path + "/discover-connectors", {}, expected=200)
    assert {item["key"] for item in discovered["items"]} == set(provider["configuration"]["enabled_toolkits"])


@pytest.mark.parametrize("configured_provider", ["model"], indirect=True)
async def test_configured_model_completes_real_run(configured_provider):
    journey, model = configured_provider
    agent = await journey.agent(model_key=model["key"], instructions="Answer briefly in plain text.")
    receipt = await journey.post(
        journey.base + "/runs",
        {"agent_id": agent["agent"]["id"], "input": agent_input("What is 2 plus 2? Answer with one digit.")},
        expected=202,
    )
    journey.live.track(receipt)
    result = await journey.live.finish(receipt["run_id"])
    assert "4" in result["output_text"].strip()
    assert len(await journey.lab.attempts(receipt["run_id"])) == 1
