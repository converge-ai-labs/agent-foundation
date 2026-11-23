"""Composer operations use existing owners, frozen authority and nonsecret contracts."""

import json

import pytest
from a13n_service.infra.db import transaction
from a13n_service.platform_catalog import catalog
from a13n_service.resources.agents.configuration_tools import EgressConfiguration, WebhookConfiguration
from a13n_service.runs.claim import claim
from a13n_service.runs.platform_tokens import issue
from pydantic import ValidationError

from .platform_support import call, client, rpc
from .test_platform_tools import lease_for, set_role

pytestmark = pytest.mark.anyio


def test_private_proposals_refuse_values_and_require_original_versions():
    webhook = {"action": "create", "name": "Runs", "url": "https://example.com", "kinds": ["run.completed"]}
    credential = {"action": "create", "name": "GitHub", "allowed_hosts": ["api.github.com"]}
    for schema, request, secret in (
        (WebhookConfiguration, webhook, "signing_secret"),
        (EgressConfiguration, credential, "value"),
    ):
        assert schema.model_validate({"request": request})
        with pytest.raises(ValidationError):
            schema.model_validate({"request": {**request, secret: "private"}})
    with pytest.raises(ValidationError):
        EgressConfiguration.model_validate({"request": {"action": "rotate", "credential_id": "cred_example"}})
    tools = {tool.name: tool for tool in catalog()}
    assert len([tool for tool in tools.values() if tool.group == "usage"]) == 9
    assert len([tool for tool in tools.values() if tool.group == "subscriptions"]) == 6
    assert all(tool.effect == "read" for tool in tools.values() if tool.group == "egress_credentials")


async def test_composer_operations_discovery_reads_and_nonsecret_mutations(service, scripted_model, runs_kit):
    subscription = (
        await service.client.post(
            "/api/v1/subscriptions",
            json={
                "name": "Runs",
                "url": "http://127.0.0.1:9/hook",
                "kinds": ["run.completed"],
            },
        )
    ).json()
    credential = (
        await service.client.post(
            "/api/v1/egress-credentials",
            json={
                "name": "GitHub",
                "allowed_hosts": ["api.github.com"],
                "value": "fixture-private-token",
            },
        )
    ).json()
    lease, agent, _ = await lease_for(service, scripted_model, runs_kit)
    token = await issue(service.runtime, lease, frozenset({"usage", "subscriptions", "egress_credentials"}))
    async with client(service, {"secret": token}) as http:
        tools = {tool["name"]: tool for tool in (await rpc(http, "tools/list")).json()["result"]["tools"]}
        assert len(tools) == 19
        assert "signing_secret" not in json.dumps(tools["update_subscription"]["inputSchema"])
        assert tools["update_subscription"]["annotations"]["readOnlyHint"] is False
        assert (await call(http, "list_subscriptions"))["items"][0]["id"] == subscription["id"]
        read = await call(http, "get_subscription", subscription_id=subscription["id"])
        assert "signing_secret" not in read
        assert "items" in await call(http, "list_deliveries", subscription_id=subscription["id"])
        changed = await call(
            http,
            "update_subscription",
            subscription_id=read["id"],
            expected_version=f"{read['id']}:{read['version']}",
            enabled=False,
        )
        assert changed["enabled"] is False
        stale = await call(
            http, "delete_subscription", subscription_id=read["id"], expected_version=f"{read['id']}:{read['version']}"
        )
        assert stale["error"]["code"] == "precondition_failed"
        listed = await call(http, "list_egress_credentials")
        assert listed["items"][0]["id"] == credential["id"] and "fixture-private-token" not in json.dumps(listed)
        assert (await call(http, "get_egress_credential", credential_id=credential["id"]))["credential_version"] == 1
        assert (await call(http, "list_credential_sessions", credential_id=credential["id"]))["items"] == []
        summary = await call(http, "summarize_usage", run_id=lease.run_id)
        assert "error" not in summary, summary
        assert "error" not in await call(http, "list_run_usage", run_id=lease.run_id)
        window = {"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"}
        for name in ("usage_overview", "usage_agents", "usage_models", "list_usage_members"):
            result = await call(http, name, **window)
            assert "error" not in result, (name, result)
        assert "error" not in await call(http, "list_session_usage", agent_id=agent["id"], **window)
        # Internal tool admission never grants HTTP write access to private credential endpoints.
        assert (await http.post("/api/v1/egress-credentials", json={})).status_code == 403


async def test_subscription_admin_refresh_cannot_widen_an_accepted_run(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(service, scripted_model)
    async with transaction(service.runtime.storage) as session:
        await set_role(session, service, "runner")
    await runs_kit.start_thread(service, agent, "limited scope")
    (lease,) = await claim(service.runtime, worker_id="composer-test", worker_build="test", limit=1)
    token = await issue(service.runtime, lease, frozenset({"usage", "subscriptions"}))
    async with transaction(service.runtime.storage) as session:
        await set_role(session, service, "admin")
    async with client(service, {"secret": token}) as http:
        result = await call(http, "list_subscriptions")
        assert result["error"]["code"] == "forbidden"
        result = await call(http, "list_usage_members", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z")
        assert result["error"]["code"] == "forbidden"


async def test_existing_composer_offers_new_operations_without_changing_its_default(
    service, scripted_model, runs_kit, monkeypatch
):
    from a13n_service.resources.agents import composer
    from a13n_service.resources.agents.toolsets import default_toolsets

    await runs_kit.create_model(service, scripted_model)
    groups = {"usage", "subscriptions", "egress_credentials"}
    assert all(not default_toolsets()[group].enabled for group in groups)
    current = composer.configuration

    def previous(model):
        config = current(model)
        return type(config).model_validate(
            {
                **config.model_dump(mode="json"),
                "toolsets": {key: value for key, value in config.toolsets.items() if key not in groups},
                "client_tools": config.client_tools[:1],
            }
        )

    from a13n_service.resources import revisions
    from a13n_service.resources.agents.tables import AgentRevisionRow
    from sqlalchemy import event

    def historical_insert(_mapper, _connection, revision):
        # Model the rows written by the old deployment; immutable rows are never updated.
        revision.config = {
            **revision.config,
            "toolsets": {key: value for key, value in revision.config["toolsets"].items() if key not in groups},
        }
        revision.digest = revisions.config_digest(revision.config)

    monkeypatch.setattr(composer, "configuration", previous)
    event.listen(AgentRevisionRow, "before_insert", historical_insert)
    try:
        before = (await service.client.post("/api/v1/agent-composer")).json()
    finally:
        event.remove(AgentRevisionRow, "before_insert", historical_insert)
    monkeypatch.setattr(composer, "configuration", current)
    after = (await service.client.post("/api/v1/agent-composer")).json()
    assert after["default_revision_id"] == before["default_revision_id"]
    versions = (await service.client.get(f"/api/v1/agents/{after['id']}/revisions")).json()["items"]
    assert len(versions) == 2
    candidate = versions[0]
    assert candidate["id"] != after["default_revision_id"]
    assert all(candidate["config"]["toolsets"][group]["enabled"] for group in groups)
    assert len(candidate["config"]["client_tools"]) == 3
    await service.client.post("/api/v1/agent-composer")
    assert len((await service.client.get(f"/api/v1/agents/{after['id']}/revisions")).json()["items"]) == 2
