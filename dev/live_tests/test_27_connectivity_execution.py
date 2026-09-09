"""Case 27: managed MCP/Connector selection, real dispatch and connection revocation."""

import hashlib
import json
from uuid import uuid4

import pytest

from .fixture_connectivity import TOOLKIT_VERSION
from .management_support import has_tool

pytestmark = pytest.mark.anyio


def requests(journey, kind):
    path = journey.lab.root / "workspace" / (kind + "-requests.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def connection(journey, kind):
    if kind == "mcp":
        resource = await journey.post(
            journey.base + "/mcp-connections",
            {
                "name": "Live MCP",
                "endpoint_url": journey.live.config["control_url"] + "/__live__/mcp",
                "auth_mode": "bearer",
            },
        )
        resource = await journey.post(
            f"/api/v1/mcp-connections/{resource['id']}/credentials",
            {
                "expected_version": resource["version"],
                "bearer": journey.live.config["token"],
            },
            expected=200,
        )
        return await journey.post(
            f"/api/v1/mcp-connections/{resource['id']}/reconnect",
            {"expected_version": resource["version"]},
            expected=200,
        )
    provider = await journey.post(
        journey.base + "/connector-providers",
        {
            "name": "Live Composio adapter",
            "type": "composio",
            "configuration": {
                "endpoint": journey.live.config["peer_url"],
                "enabled_toolkits": ["live"],
            },
            "credentials": {"api_key": journey.live.config["token"]},
        },
    )
    resource = await journey.post(
        journey.base + "/connector-connections",
        {
            "name": "Live account",
            "connector_provider_id": provider["id"],
            "connector_key": "live",
        },
    )
    await journey.post(
        f"/api/v1/connector-connections/{resource['id']}/setup",
        {
            "expected_version": resource["version"],
            "setup": {"auth_config_id": "live-auth", "toolkit_version": TOOLKIT_VERSION},
            "return_path": "/",
        },
        expected=200,
    )
    return await journey.live.wait(
        lambda: journey.live.request("GET", f"/api/v1/connector-connections/{resource['id']}"),
        lambda value: value["status"] == "ready",
        "Connector setup reconciliation",
    )


@pytest.mark.parametrize("kind", ["mcp", "connector"])
async def test_managed_connection_calls_selected_tool_and_revocation_blocks_dispatch(management, kind):
    journey, live = management, management.live
    resource = await connection(journey, kind)
    assert resource["status"] == "ready"
    config = {kind + "_tools": [{kind + "_connection_id": resource["id"], "tools": ["live_echo"]}]}
    agent = await journey.agent(**config)
    value = uuid4().hex
    case = await journey.case(steps=[{"tool": "live_echo", "arguments": {"value": value}}])
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    result = await live.finish(receipt["run_id"])
    assert "REMOTE:" + value in result["output_text"]
    observation = journey.observations(case)[0]
    assert has_tool(observation, "live_echo") and not has_tool(observation, "live_forbidden")
    dispatches = [
        item
        for item in requests(journey, kind)
        if item["body"].get("method") == "tools/call" or "/tools/execute/" in item["path"]
    ]
    assert len(dispatches) == 1
    dispatched = dispatches[0]
    args = dispatched["body"]["params"]["arguments"] if kind == "mcp" else dispatched["body"]["arguments"]
    assert args == {"value": value}
    expected_credential = ("Bearer " if kind == "mcp" else "") + live.config["token"]
    assert dispatched["credential_sha256"] == hashlib.sha256(expected_credential.encode()).hexdigest()
    assert live.config["token"] not in json.dumps(journey.observations(case))

    path = f"/api/v1/{kind}-connections/{resource['id']}"
    current = await live.request("GET", path)
    await journey.post(
        path + ("/disable" if kind == "mcp" else "/revoke"), {"expected_version": current["version"]}, expected=200
    )
    # New invocations must be denied before any external dispatch. This does not test IAM grant revocation (case 29).
    rejected = await live.http.post(
        journey.base + "/runs",
        headers={"Idempotency-Key": uuid4().hex},
        json={**live.start_body(await journey.case()), "agent_id": agent["agent"]["id"]},
    )
    assert rejected.status_code in {400, 403, 409}
    later = [
        item
        for item in requests(journey, kind)
        if item["body"].get("method") == "tools/call" or "/tools/execute/" in item["path"]
    ]
    assert later == dispatches
    async with journey.outsider() as outsider:
        response = await outsider.get(path)
        assert response.status_code in {403, 404}
