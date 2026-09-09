"""Additional integration journeys; deterministic fault cases keep their fixtures."""

import logging
from uuid import uuid4

import anyio
import pytest
from a13n_service.connectivity.toolsets import portable_tool_name

from .client import agent_input
from .management_support import has_tool, last_tool_result
from .round_two_lab import private_json

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


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
    enabled = "enabled_services" if provider["type"] == "openconnector" else "enabled_toolkits"
    assert {item["key"] for item in discovered["items"]} == set(provider["configuration"][enabled])
    if provider["type"] == "openconnector":
        assert tested["verified_access"] == ["catalog_read"]


@pytest.mark.parametrize("configured_provider", ["slack"], indirect=True)
async def test_openconnector_slack_oauth_and_read_only_execution(configured_provider):
    journey, provider = configured_provider
    connection = await journey.post(
        journey.base + "/connector-connections",
        {
            "name": "Live Slack OAuth",
            "connector_provider_id": provider["id"],
            "connector_key": "slack",
        },
    )
    path = f"/api/v1/connector-connections/{connection['id']}"
    launch = await journey.post(
        path + "/setup",
        {"expected_version": connection["version"], "setup": {}, "return_path": "/"},
        expected=200,
    )
    assert launch["status"] == "pending" and launch["redirect_url"]
    assert launch["requires_browser_callback"] is False
    authorization = journey.lab.root / "slack-authorization.json"
    private_json(authorization, {"redirect_url": launch["redirect_url"], "connection_id": connection["id"]})
    logger.info("Open the redirect_url in %s and authorize Slack within 10 minutes", authorization)
    try:
        with anyio.fail_after(600):
            while True:
                current = await journey.live.request("GET", path)
                assert current["status"] in {"pending", "ready"}, (
                    f"Slack setup failed: {current['status']} ({current['status_reason']})"
                )
                if current["status"] == "ready":
                    break
                await anyio.sleep(2)
    finally:
        authorization.unlink(missing_ok=True)
        logger.info("OOMOL retains the remote test account; manage it in the project's Connected accounts page")
    logger.info("Slack account verified: connection=%s", connection["id"])

    agent = await journey.agent(
        connector_tools=[{"connector_connection_id": connection["id"], "tools": ["slack.list_channels"]}]
    )
    # Select the real namespaced tool offered to the model, including its portable digest suffix.
    tool_name = portable_tool_name("slack.list_channels")
    case = await journey.case(steps=[{"tool": tool_name, "arguments": {"limit": 1}}])
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    await journey.live.finish(receipt["run_id"])
    observations = journey.observations(case)
    assert has_tool(observations[0], tool_name)
    assert not has_tool(observations[0], portable_tool_name("slack.post_message"))
    outcome = last_tool_result(observations[-1])
    assert outcome["kind"] == "succeeded", "Slack tool did not report a successful provider outcome"
    result = outcome["result"]
    assert isinstance(result["channels"], list)
    for channel in result["channels"]:
        assert channel["channelId"] and isinstance(channel["name"], str)
    logger.info("Slack read-only tool succeeded: run=%s channels=%d", receipt["run_id"], len(result["channels"]))


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
