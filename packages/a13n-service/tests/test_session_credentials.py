"""Service credential selections remain nonsecret and use workspace authority."""

import pytest
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.egress_credentials.service import validate_references
from a13n_service.resources.environment_instances.session_configuration import SessionConfiguration
from a13n_service.runs.environments.egress.tables import SessionCredentialRow
from a13n_service.tenancy.access import principal_for
from a13n_service.tenancy.authorize import WorkspaceScope
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from .environments_support import interrupt, start, with_fake_providers

pytestmark = pytest.mark.anyio


def selection(credential_id="ecred_" + "a" * 24, **binding_changes):
    return {
        "egress_mode": "controlled",
        "egress": {
            "destinations": {"mode": "allowlist", "hosts": ["api.github.com"]},
            "secrets": [
                {
                    "env": "GH_TOKEN",
                    "credential_id": credential_id,
                    "inject_hosts": ["api.github.com"],
                    **binding_changes,
                }
            ],
        },
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"env": "PATH"},
        {"env": "eip_TOKEN"},
        {"env": "SSL_CERT_FILE"},
        {"env": "INVALID-NAME"},
        {"value": "forbidden-token"},
        {"source": {"kind": "environment", "name": "HOST_SECRET"}},
        {"inject_hosts": ["other.example"]},
    ],
)
def test_references_refuse_host_sources_values_reserved_names_and_destination_escape(changes):
    with pytest.raises(ValidationError):
        SessionConfiguration.model_validate(selection(**changes))


def test_selection_requires_controlled_mode_and_unique_variable_names():
    value = selection()
    value["egress_mode"] = "inherit"
    with pytest.raises(ValidationError):
        SessionConfiguration.model_validate(value)
    value["egress_mode"] = "controlled"
    value["egress"]["secrets"] *= 2
    with pytest.raises(ValidationError):
        SessionConfiguration.model_validate(value)


def test_resolution_uses_explicit_values_without_changing_the_captured_selection(monkeypatch):
    config = SessionConfiguration.model_validate(selection())
    reference = config.credential_references[0]
    monkeypatch.setenv("GH_TOKEN", "must-not-read-host")
    captured = config.model_dump_json()
    first = config.resolve({reference.credential_id: SecretStr("first-token")})
    rotated = config.resolve({reference.credential_id: SecretStr("second-token")})
    assert first.egress.secrets[0].value == "first-token"
    assert rotated.egress.secrets[0].value == "second-token"
    assert captured == config.model_dump_json()
    assert "first-token" not in captured and "second-token" not in captured
    with pytest.raises(KeyError):
        config.resolve({})
    assert "default_session_configuration" not in AgentConfig(model="test").model_dump()


async def test_reference_validation_checks_resource_scope_revocation_and_host_subset(service):
    created = await service.client.post(
        f"{service.api}/egress-credentials",
        json={"name": "GitHub", "allowed_hosts": ["api.github.com"], "value": "private-token"},
    )
    assert created.status_code == 201, created.text
    config = SessionConfiguration.model_validate(selection(created.json()["id"]))
    async with short_session(service.runtime.storage) as session:
        actor = await principal_for(session, service.runtime.access, service.tenant.principal_id)
        scope = WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)
        await validate_references(session, actor, scope, config.credential_references)
        with pytest.raises(ServiceError) as missing:
            await validate_references(
                session, actor, scope, SessionConfiguration.model_validate(selection()).credential_references
            )
        assert missing.value.code == "invalid_argument"
        assert missing.value.details["kind"] == "egress_credential"
        assert missing.value.details["field"] == "egress.secrets.0"
        reference = config.credential_references[0].model_copy(update={"inject_hosts": ("other.example",)})
        with pytest.raises(ServiceError) as hosts:
            await validate_references(session, actor, scope, [reference])
        assert hosts.value.code == "invalid_argument"
    revoked = await service.client.post(
        f"{service.api}/egress-credentials/{created.json()['id']}/revoke",
        headers={"if-match": created.headers["etag"]},
    )
    assert revoked.status_code == 202
    async with short_session(service.runtime.storage) as session:
        with pytest.raises(ServiceError) as refused:
            await validate_references(session, actor, scope, config.credential_references)
        assert refused.value.details["reason"] == "credential_revoked"


async def test_agent_default_initializes_once_and_unstarted_references_are_discarded(service, runs_kit):
    await runs_kit.pause_sweeps(service)
    env = await with_fake_providers(service)
    credential = await service.client.post(
        f"{service.api}/egress-credentials",
        json={"name": "GitHub", "allowed_hosts": ["api.github.com"], "value": "private-token"},
    )
    config = selection(credential.json()["id"])
    created = await service.client.post(
        f"{service.api}/agents",
        json={
            "name": "Authenticated builder",
            "config": {
                "model": "unused",
                "default_environment_template_id": env.template["id"],
                "default_session_configuration": config,
            },
        },
    )
    assert created.status_code == 201, created.text
    env.agent = created.json()
    first = await start(env)
    mount = first["run"]["environment_mounts"][0]
    assert mount["session_configuration"]["egress"] == config["egress"]
    thread_id = first["thread"]["id"]
    path = f"{env.api}/threads/{thread_id}/session-bindings/{mount['environment_id']}/default"
    assert (await env.client.get(path)).json()["configuration"] == mount["session_configuration"]
    await interrupt(env, first["run"]["id"])
    thread = await env.client.get(f"{env.api}/threads/{thread_id}")
    changed = await env.client.put(path, json={"configuration": {}}, headers={"if-match": thread.headers["etag"]})
    assert changed.status_code == 200, changed.text
    accepted = await runs_kit.submit(env, thread_id, runs_kit.message(env.agent, "Use the Thread policy"))
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["run"]["environment_mounts"][0]["session_configuration"]["egress"] is None
    # Neither Run opened a native Session: the superseded references need no deferred retirement.
    async with short_session(service.runtime.storage) as db:
        assert not (
            await db.scalars(select(SessionCredentialRow).where(SessionCredentialRow.thread_id == thread_id))
        ).all()
