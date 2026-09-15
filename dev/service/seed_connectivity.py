"""Connected, pending, and disabled integration resources using public local fixtures."""

from .seed_client import Client
from .seed_journeys import run
from .seed_resources import agent_config


async def connectivity(client: Client, base: str, catalog: dict, identity: dict, model_url: str) -> dict:
    endpoint = model_url.removesuffix("/v1")
    scenarios = {}
    for state, auth in (("pending", "bearer"), ("disabled", "none"), ("ready", "none")):
        connection = await client.request(
            "POST",
            base + "/connections",
            expected=201,
            json={
                "name": f"Local review MCP · {state}",
                "source": {"kind": "mcp", "endpoint_url": endpoint + "/mcp", "auth_mode": auth},
            },
        )
        path = f"/api/v1/connections/{connection['id']}"
        if state == "disabled":
            connection = await client.request(
                "POST", path + "/disable", json={"expected_version": connection["version"]}
            )
        elif state == "ready":
            tools = await client.request(
                "POST", path + "/mcp/discover", json={"expected_version": connection["version"]}
            )
            if len(tools["items"]) != 2:
                raise RuntimeError("Local MCP discovery did not return the two fixture tools")
            connection = await client.request("GET", path)
        if connection["status"] != state:
            raise RuntimeError("MCP connection did not reach the expected state")
        scenarios[f"mcp_{state}"] = connection["id"]
    config = {
        **agent_config("Local MCP review"),
        "instructions": "Use the local review fixture for fictional lookups.",
        "connection_tools": [
            {
                "connection_id": scenarios["mcp_ready"],
                "tools": ["lookup_local_review", "fail_local_review"],
                "permission": "allow",
            }
        ],
    }
    created = await client.request(
        "POST", base + "/agents", expected=201, json={"name": "MCP lookup and failure", "config": config}
    )
    scenarios["agent_mcp"] = created["agent"]["id"]
    for flag, name in (("[mcp]", "tool_success"), ("[mcp-fail]", "tool_failure")):
        result = await run(
            client,
            base,
            created["agent"]["id"],
            flag + " Look up the fictional release review.",
            expected="completed" if name == "tool_success" else "failed",
        )
        if name == "tool_success" and "LOCAL-REVIEW-42" not in result["output_text"]:
            raise RuntimeError("MCP tool result did not appear in the retained model output")
        scenarios[name] = result["id"]

    provider = await client.request(
        "POST",
        base + "/connector-providers",
        expected=201,
        json={
            "name": "Fictional Composio configuration · disabled",
            "type": "composio",
            "configuration": {},
            "credentials": {"api_key": "public-local-connector-token"},
        },
    )
    connection = await client.request(
        "POST",
        base + "/connections",
        expected=201,
        json={
            "source": {
                "kind": "connector",
                "provider_id": provider["id"],
                "connector_key": "github",
            },
            "name": "Fictional repository · disabled",
        },
    )
    connection = await client.request(
        "POST",
        f"/api/v1/connections/{connection['id']}/disable",
        json={"expected_version": connection["version"]},
    )
    provider = await client.request(
        "POST",
        f"/api/v1/connector-providers/{provider['id']}/disable",
        json={"expected_version": provider["version"]},
    )
    scenarios["connector_provider_disabled"] = provider["id"]
    scenarios[f"connector_connection_{connection['status']}"] = connection["id"]

    for index, state in enumerate(("active", "disabled")):
        account = await client.request(
            "POST",
            base + "/application-accounts",
            expected=201,
            json={
                "name": f"Fictional Slack account · {state}",
                "provider_key": "slack",
                "provider_config_version": "slack_http_v1",
                "provider_config": {
                    "api_app_id": f"LOCAL_APP_{index}",
                    "team_id": f"LOCAL_TEAM_{index}",
                    "bot_user_id": f"LOCAL_BOT_{index}",
                },
                "credentials": {
                    "signing_secret": "public-local-slack-signing-secret",
                    "bot_token": "public-local-slack-token",
                },
                "receive_enabled": False,
                "default_agent_id": catalog["agents"][3],
                "execution_service_account_id": identity["service_account_runner"],
            },
        )
        target = await client.request(
            "POST",
            f"/api/v1/application-accounts/{account['id']}/targets",
            expected=201,
            json={
                "target_kind": "conversation",
                "external_target_id": "LOCAL_CHANNEL_DEMO",
                "agent_id": catalog["agents"][3],
                "receive_enabled": False,
            },
        )
        if state == "disabled":
            account = await client.request(
                "POST",
                f"/api/v1/application-accounts/{account['id']}/disable",
                json={"expected_version": account["version"]},
            )
        scenarios[f"application_account_{state}"] = account["id"]
        scenarios[f"account_target_{state}"] = target["id"]
    return scenarios
