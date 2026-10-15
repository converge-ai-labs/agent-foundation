"""Deployment refresh publishes ordinary revisions without changing accepted Runs or impersonating users."""

import asyncio
import json
from dataclasses import asdict

import pytest
from a13n_service.cli import main
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session
from a13n_service.resources.agents import composer, finding
from a13n_service.resources.agents.refresh import refresh
from a13n_service.resources.agents.tables import AgentRevisionRow
from click.testing import CliRunner
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio


async def prepare_old(service, monkeypatch, kind="composer"):
    definition = composer if kind == "composer" else finding
    original = definition.configuration
    with monkeypatch.context() as previous:
        previous.setattr(definition, "NAME", "Previous name")
        previous.setattr(
            definition,
            "configuration",
            lambda model: original(model).model_copy(
                update={"instructions": "Previous instructions", "client_tools": ()}
            ),
        )
        path = "agent-composer" if kind == "composer" else "finding-agent"
        response = await service.client.post(f"{service.api}/{path}")
    assert response.status_code == 200, response.text
    return response.json()


async def get_agent(service, agent_id):
    response = await service.client.get(f"{service.api}/agents/{agent_id}")
    assert response.status_code == 200, response.text
    agent = response.json()
    response = await service.client.get(f"{service.api}/agents/{agent_id}/revisions/{agent['default_revision_id']}")
    assert response.status_code == 200, response.text
    return agent, response.json()


async def test_refresh_updates_existing_presets_once_and_preserves_accepted_runs(
    service, scripted_model, runs_kit, monkeypatch
):
    storage = service.runtime.storage
    assert asdict(await refresh(storage, preferred=())) == {"updated": 0, "unchanged": 0, "failed": 0}
    model = await runs_kit.create_model(service, scripted_model)
    old = await prepare_old(service, monkeypatch)
    finder = await prepare_old(service, monkeypatch, "finding")
    custom = await runs_kit.add_agent(service, "custom", model, instructions="User configuration")
    accepted = await runs_kit.start_thread(service, old, "Before deployment")
    old_run_id = accepted["run"]["id"]

    result = await refresh(storage, preferred=("new-preference",))
    assert asdict(result) == {"updated": 2, "unchanged": 0, "failed": 0}
    current, revision = await get_agent(service, old["id"])
    assert current["name"] == composer.NAME and current["updated_by_id"] is None
    assert current["created_by_id"] == old["created_by_id"]
    assert revision["created_by_id"] is None and revision["number"] == 2
    assert revision["config"]["model"] == model
    assert [tool["name"] for tool in revision["config"]["client_tools"]] == ["configure_provider"]
    assert (await get_agent(service, finder["id"]))[1]["config"]["instructions"] == finding.INSTRUCTIONS
    assert (await get_agent(service, custom["id"]))[0] == custom
    assert (await runs_kit.get_run(service, old_run_id))["agent_revision_id"] == old["default_revision_id"]
    # The ordinary start path (Console's Try Agent) uses the refreshed default, without preparing it.
    started = await runs_kit.start_thread(service, current, "After deployment")
    assert started["run"]["agent_revision_id"] == revision["id"]
    pinned = await runs_kit.start_thread(
        service, current, "Pinned history", agent_revision_id=old["default_revision_id"]
    )
    assert pinned["run"]["agent_revision_id"] == old["default_revision_id"]
    assert asdict(await refresh(storage, preferred=())) == {"updated": 0, "unchanged": 2, "failed": 0}
    assert (await get_agent(service, old["id"])) == (current, revision)
    async with short_session(storage) as session:
        previous = await session.get_one(AgentRevisionRow, old["default_revision_id"])
        assert previous.config["instructions"] == "Previous instructions"
        events = (
            await session.scalars(select(AuditEventRow).where(AuditEventRow.action.like("agent.%.refresh")))
        ).all()
        assert len(events) == 2
        assert all(event.actor_id is None and event.details["authority"] == "operator" for event in events)


async def test_concurrent_refresh_and_prepare_publish_only_one_revision(service, scripted_model, runs_kit, monkeypatch):
    await runs_kit.create_model(service, scripted_model)
    old = await prepare_old(service, monkeypatch)
    results = await asyncio.gather(
        refresh(service.runtime.storage, preferred=()),
        refresh(service.runtime.storage, preferred=()),
        service.client.post(f"{service.api}/agent-composer"),
    )
    assert results[0].failed == results[1].failed == 0
    assert results[2].status_code == 200
    async with short_session(service.runtime.storage) as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(AgentRevisionRow).where(AgentRevisionRow.agent_id == old["id"])
            )
            == 2
        )


async def test_refresh_metadata_does_not_add_a_revision(service, scripted_model, runs_kit, monkeypatch):
    await runs_kit.create_model(service, scripted_model)
    with monkeypatch.context() as previous:
        previous.setattr(composer, "NAME", "Previous name")
        before = (await service.client.post(f"{service.api}/agent-composer")).json()
    assert (await refresh(service.runtime.storage, preferred=())).updated == 1
    after, _ = await get_agent(service, before["id"])
    assert after["name"] == composer.NAME
    assert after["default_revision_id"] == before["default_revision_id"]
    assert after["version"] == before["version"] + 1
    assert after["updated_by_id"] is None


async def test_refresh_keeps_a_usable_model_and_replaces_a_disabled_model(
    service, scripted_model, runs_kit, monkeypatch
):
    key = await runs_kit.create_model(service, scripted_model)
    old = await prepare_old(service, monkeypatch)
    model = await service.client.get(f"{service.api}/models/{key}")
    candidate = await service.client.post(
        f"{service.api}/models",
        json={
            "key": "preferred",
            "name": "Preferred",
            "provider_id": model.json()["provider_id"],
            "config": {"model_name": "new-preference", "model_api": "openai.chat_completions"},
        },
    )
    assert candidate.status_code == 201, candidate.text
    assert (await refresh(service.runtime.storage, preferred=("new-preference",))).updated == 1
    assert (await get_agent(service, old["id"]))[1]["config"]["model"] == key
    disabled = await service.client.patch(
        f"{service.api}/models/{key}", json={"enabled": False}, headers={"if-match": model.headers["etag"]}
    )
    assert disabled.status_code == 200, disabled.text
    assert (await refresh(service.runtime.storage, preferred=("new-preference",))).updated == 1
    assert (await get_agent(service, old["id"]))[1]["config"]["model"] == "preferred"


async def test_cli_reports_partial_failure_and_can_retry_without_duplicate_revisions(
    service, scripted_model, runs_kit, monkeypatch, settings, tmp_path, clean_environment
):
    model = await runs_kit.create_model(service, scripted_model)
    first = await prepare_old(service, monkeypatch)
    created = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other workspace"})
    assert created.status_code == 201, created.text
    other_workspace = created.json()["id"]
    service.client.headers["x-workspace-id"] = other_workspace
    await runs_kit.create_model(service, scripted_model)
    second = await prepare_old(service, monkeypatch)
    response = await service.client.get(f"{service.api}/models/{model}")
    provider_id = response.json()["provider_id"]
    provider = await service.client.get(f"{service.api}/model-providers/{provider_id}")
    disabled = await service.client.patch(
        f"{service.api}/model-providers/{provider_id}",
        json={"enabled": False},
        headers={"if-match": provider.headers["etag"]},
    )
    assert disabled.status_code == 200, disabled.text
    config = tmp_path / "service.toml"
    config.write_text(f'[database]\nurl = "{settings.database.url.get_secret_value()}"\nauto_migrate = false\n')

    def invoke():
        return CliRunner().invoke(main, ["--config", str(config), "refresh-builtin-agents"])

    result = await asyncio.to_thread(invoke)
    assert result.exit_code == 1
    assert '"updated": 1, "unchanged": 0, "failed": 1' in result.output
    assert "incomplete" in result.output and "sk-scripted" not in result.output
    assert (await get_agent(service, second["id"]))[0] == second
    service.client.headers["x-workspace-id"] = service.tenant.workspace_id
    current, _ = await get_agent(service, first["id"])
    assert current["default_revision_id"] != first["default_revision_id"]

    service.client.headers["x-workspace-id"] = other_workspace
    enabled = await service.client.patch(
        f"{service.api}/model-providers/{provider_id}",
        json={"enabled": True},
        headers={"if-match": disabled.headers["etag"]},
    )
    assert enabled.status_code == 200, enabled.text
    retried = await asyncio.to_thread(invoke)
    assert retried.exit_code == 0, retried.output
    assert json.loads(retried.output.splitlines()[-1]) == {"updated": 1, "unchanged": 1, "failed": 0}
    service.client.headers["x-workspace-id"] = service.tenant.workspace_id
    assert (await get_agent(service, first["id"]))[0] == current
