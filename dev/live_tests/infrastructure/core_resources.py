"""Provision the core HTTP journeys through the running Control API."""

from __future__ import annotations

import sys
from collections.abc import Callable

from .client import LiveClient


async def provision(client: LiveClient, *, on_created: Callable[[dict], None] | None = None) -> None:
    config = client.config
    base = f"/api/v1/workspaces/{config['workspace_id']}"

    async def create_once(key: str, path: str, body: dict, *, response_key: str | None = None) -> str:
        if key not in config:
            result = await client.request(
                "POST",
                path,
                expected=201,
                timeout=120,
                json=body,
                headers={"Idempotency-Key": "live-setup-" + config["workspace_id"] + "-" + key},
            )
            config[key] = (result[response_key] if response_key else result)["id"]
            if on_created is not None:
                on_created(config)
        return config[key]

    provider_id = await create_once(
        "model_provider_id",
        base + "/model-providers",
        {
            "type": "openai",
            "name": "Local live-test model",
            "credential": config["token"],
            "configuration": {"base_url": config["control_url"] + "/__live__/model/v1", "auth_mode": "bearer"},
        },
    )
    await create_once(
        "model_id",
        base + "/models",
        {
            "key": "live-fixture",
            "name": "Deterministic live model",
            "provider_id": provider_id,
            "upstream_model": "live-fixture",
            "model_api": "openai.chat_completions",
            "settings": {},
        },
    )
    environment_provider_id = await create_once(
        "environment_provider_id",
        base + "/environment-providers",
        {
            "type": "a13n.direct-local",
            "name": "Live-test local files",
            "configuration": {},
        },
    )
    await create_once(
        "environment_id",
        base + "/environments",
        {
            "provider_id": environment_provider_id,
            "configuration_schema_version": "1",
            "access": "full",
            "configuration": {
                "root": {"path": config["workspace_root"]},
                "shell_profiles": [{"profile_id": "default", "executable": "/bin/sh"}],
                "allowed_executables": [sys.executable],
                "max_wall_time_seconds": 180,
            },
        },
    )
    agent_config = {
        "model": {"model_key": "live-fixture", "characteristics": {"context_window_tokens": 32768}},
        "instructions": "Execute the local live-test scenario. Preserve the full conversation history.",
        "input_adapter": {"adapter_key": "native", "config": {}},
        "protocol": {"schema_version": "1", "public_name": "Live test", "output_modes": ["text"], "limits": {}},
    }
    await create_once(
        "agent_id", base + "/agents", {"name": "Live test agent", "config": agent_config}, response_key="agent"
    )
    await create_once(
        "protocol_agent_id",
        base + "/agents",
        {
            "name": "Live protocol agent",
            "config": {
                **agent_config,
                "plugins": [
                    {
                        "instance_name": "effects",
                        "plugin_key": "live.resilience",
                        "config": {"root": config["workspace_root"]},
                    }
                ],
            },
        },
        response_key="agent",
    )
    await create_once(
        "approval_agent_id",
        base + "/agents",
        {
            "name": "Live approval agent",
            "config": {
                **agent_config,
                "plugins": [
                    {
                        "instance_name": "approval",
                        "plugin_key": "live.approval",
                        "config": {"root": config["workspace_root"]},
                    }
                ],
            },
        },
        response_key="agent",
    )
