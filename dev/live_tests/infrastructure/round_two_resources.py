"""Provision round-two model and Agent resources through the public Control API."""

from uuid import uuid4


def agent_config(model_key="live-fixture", **values):
    return {
        "model": {"model_key": model_key, "characteristics": {"context_window": 32768}},
        "instructions": "Execute the live-test scenario and preserve full conversation history.",
        "input_adapter": {"adapter_key": "native", "config": {}},
        "protocol": {"schema_version": "1", "public_name": "Live test", "output_modes": ["text"], "limits": {}},
        **values,
    }


async def provision(client):
    config = client.config
    base = f"/api/v1/workspaces/{config['workspace_id']}"
    provider = await client.request(
        "POST",
        base + "/model-providers",
        expected=201,
        json={
            "type": "openai_compatible",
            "name": "Round-two HTTP fixture",
            "credential": config["token"],
            "configuration": {"base_url": config["control_url"] + "/__live__/model/v1", "auth_mode": "bearer"},
        },
    )
    config["model_provider_id"] = provider["id"]
    for key, settings in (("live-fixture", {}), ("live-timeout", {"timeout": 0.5})):
        model = await client.request(
            "POST",
            base + "/models",
            expected=201,
            json={
                "key": key,
                "name": key,
                "provider_id": provider["id"],
                "upstream_model": "live-fixture",
                "model_api": "openai.chat_completions",
                "settings": settings,
            },
        )
        if key == "live-fixture":
            config["model_id"] = model["id"]
    plugins = [
        {
            "instance_name": "resilience",
            "plugin_key": "live.resilience",
            "config": {"root": config["workspace_root"]},
        }
    ]
    config["resilience_plugins"] = plugins

    async def create_agent(key, definition):
        result = await client.request(
            "POST",
            base + "/agents",
            expected=201,
            headers={"Idempotency-Key": uuid4().hex},
            json={"name": key, "config": definition},
        )
        config[key] = result["agent"]["id"]

    await create_agent("agent_id", agent_config(plugins=plugins, retries={"tools": 1, "output": 1}))
    await create_agent("timeout_agent_id", agent_config("live-timeout"))
    await create_agent("child_agent_id", agent_config())
    await create_agent(
        "async_agent_id",
        agent_config(subagent_mode="async", subagents={"child": {"agent_id": config["child_agent_id"]}}),
    )
