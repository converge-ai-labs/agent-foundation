"""Provision an owned native Bot and immutable document through public APIs."""

import os
from uuid import uuid4

from .platform import account_config


async def setup(journey, platform, *, agent=None):
    conversation = "C_LIVE" if platform == "slack" else "oc_live"
    provider = await journey.post(
        journey.base + "/memory-providers",
        {
            "type": "mem0_oss",
            "name": "Live Bot memory",
            "configuration": {"base_url": os.environ["TEST_MEM0_OSS_URL"]},
            "credential": {"api_key": os.environ["TEST_MEM0_OSS_API_KEY"]},
        },
    )
    principal = await journey.post(journey.base + "/service-accounts", {"name": "Bot runner", "role": "runner"})
    agent = agent or await journey.agent(toolsets={})
    agent_id = agent["agent"]["id"]
    account = await journey.post(
        journey.base + "/application-accounts",
        {
            "name": "Fictional live bot",
            **account_config(platform),
            "receive_enabled": False,
            "reception_scope": "configured_targets",
        },
    )
    base = f"/api/v1/application-accounts/{account['id']}"
    await journey.live.request(
        "PUT", base + "/bot/memory-settings", json={"expected_version": 0, "memory": {"provider_id": provider["id"]}}
    )
    target = await journey.post(base + "/targets", {"target_kind": "conversation", "external_target_id": conversation})
    check = await journey.post(
        base + "/bot/checks",
        {"expected_version": account["version"], "conversation_id": conversation},
        expected=200,
    )
    assert check["error_code"] is None and check["conversation"]["audience"] == "private"
    account = await journey.post(
        base + "/bot/activate",
        {
            "expected_version": account["version"],
            "target_id": target["id"],
            "target_version": target["version"],
            "conversation_id": conversation,
            "agent_id": agent_id,
            "execution_service_account_id": principal["id"],
            "policy": {"interaction_mode": "mention", "reply_mode": "thread"},
        },
        expected=200,
    )
    scope = await journey.post(base + "/memory-scopes", {"external_conversation_id": conversation}, expected=200)
    assert scope["audience"] == "private"
    path = f"{base}/memory-scopes/{scope['id']}"
    proof = "MEMORY-BODY-ONLY-" + uuid4().hex
    document = await journey.post(
        path + "/documents",
        {
            "title": "Release checklist",
            "description": "Saved release facts",
            "text": proof,
        },
    )
    return account, target, scope, path, document, proof, agent, provider
