"""Case 27: managed MCP/Connector selection, real dispatch and connection revocation."""

import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from ..infrastructure.management_support import has_tool
from .fixture_connectivity import TOOLKIT_VERSION

pytestmark = pytest.mark.anyio


def requests(journey, kind):
    path = journey.lab.root / "workspace" / (kind + "-requests.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def connection(journey, kind):
    if kind == "mcp":
        resource = await journey.post(
            journey.base + "/connections",
            {
                "name": "Live MCP",
                "source": {
                    "kind": "mcp",
                    "endpoint_url": journey.live.config["control_url"] + "/__live__/mcp",
                    "auth_mode": "bearer",
                },
            },
        )
        auth = await journey.post(
            f"/api/v1/connections/{resource['id']}/authorizations",
            {
                "expected_version": resource["version"],
                "method": "credentials",
                "credentials": {"bearer": journey.live.config["token"]},
            },
        )
        assert auth["status"] == "completed"
        current = await journey.live.request("GET", f"/api/v1/connections/{resource['id']}")
        return await journey.post(
            f"/api/v1/connections/{resource['id']}/check", {"expected_version": current["version"]}, expected=200
        )
    provider = await journey.post(
        journey.base + "/connector-providers",
        {
            "name": "Live Composio adapter",
            "type": "composio",
            "configuration": {},
            "credentials": {"api_key": journey.live.config["token"]},
        },
    )
    resource = await journey.post(
        journey.base + "/connections",
        {
            "name": "Live account",
            "source": {"kind": "connector", "provider_id": provider["id"], "connector_key": "live"},
        },
    )
    nonce, verifier, state = secrets.token_hex(32), secrets.token_hex(32), secrets.token_hex(32)
    auth = await journey.post(
        f"/api/v1/connections/{resource['id']}/authorizations",
        {
            "expected_version": resource["version"],
            "method": "browser",
            "options": {"auth_config_id": "live-auth", "toolkit_version": TOOLKIT_VERSION},
            "return_url": "https://live.example/complete",
            "state": state,
            "completion_challenge": hashlib.sha256(verifier.encode()).hexdigest(),
        },
    )
    assert auth["status"] == "awaiting_user"
    path = f"/api/v1/connection-authorizations/{auth['id']}"
    token = parse_qs(urlsplit(auth["next_action"]["url"]).fragment)["token"][0]
    await journey.post(path + "/launch", {"token": token, "browser_nonce": nonce}, expected=200)
    received = await journey.post(
        path + "/receive", {"browser_nonce": nonce, "session_uri": "live-session"}, expected=200
    )
    query = parse_qs(urlsplit(received["url"]).query)
    assert query["state"] == [state] and query["authorization_id"] == [auth["id"]]
    completed = await journey.post(
        path + "/complete", {"receipt": query["receipt"][0], "completion_verifier": verifier}, expected=200
    )
    assert completed["status"] == "completed"
    return await journey.live.request("GET", f"/api/v1/connections/{resource['id']}")


@pytest.mark.parametrize("kind", ["mcp", "connector"])
async def test_managed_connection_calls_selected_tool_and_revocation_blocks_dispatch(management, kind):
    journey, live = management, management.live
    resource = await connection(journey, kind)
    assert resource["status"] == "ready"
    config = {"connection_tools": [{"connection_id": resource["id"], "tools": ["live_echo"], "permission": "allow"}]}
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

    path = f"/api/v1/connections/{resource['id']}"
    current = await live.request("GET", path)
    cleanup = await journey.post(
        path + ("/disable" if kind == "mcp" else "/connector/revoke"),
        {"expected_version": current["version"]},
        expected=200,
    )
    if kind == "connector":
        assert cleanup["local_status"] == "disabled" and cleanup["remote_status"] == "succeeded"
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
