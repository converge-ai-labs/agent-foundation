"""Managed preset edits freeze instructions without changing generic execution configuration."""

import json

import pytest
from a13n_service.infra.db import transaction
from a13n_service.resources.agents import composer, preset_edits
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.agents.tables import AgentRevisionRow
from a13n_service.resources.revisions import publish
from a13n_service.runs.findings import preset as finding_preset

pytestmark = pytest.mark.anyio


async def prepared(service, endpoint):
    response = await service.client.post(f"{service.api}/{endpoint}")
    assert response.status_code == 200, response.text
    head = response.json()
    path = f"{service.api}/agents/{head['id']}"
    revision = await service.client.get(f"{path}/revisions/{head['default_revision_id']}")
    assert revision.status_code == 200, revision.text
    return response, path, revision.json()


@pytest.mark.parametrize("endpoint", ["agent-composer", "finding-agent"])
async def test_edit_freezes_instructions_and_keeps_selected_version(service, scripted_model, runs_kit, endpoint):
    model = await runs_kit.create_model(service, scripted_model)
    head, path, original = await prepared(service, endpoint)
    assert original["preset"]["instructions"] == ""
    assert original["preset"]["official_instructions"] == original["config"]["instructions"]
    started = await runs_kit.start_thread(service, head.json(), "Use the original revision")
    body = {
        "config": {**original["config"], "model": model, "model_settings": {"max_tokens": 2048, "temperature": 0.2}},
        "preset": {"base_revision_id": original["id"], "instructions": "Answer in Chinese."},
    }
    valid = await service.client.post(f"{service.api}/agents/validate", json={**body, "agent_id": head.json()["id"]})
    assert valid.status_code == 204, valid.text
    saved = await service.client.post(f"{path}/revisions", json=body, headers={"If-Match": head.headers["etag"]})
    assert saved.status_code == 201, saved.text
    edited = saved.json()
    assert edited["id"] != original["id"]
    assert edited["preset"]["instructions"] == "Answer in Chinese."
    assert edited["preset"]["official_instructions"] == original["config"]["instructions"]
    assert (
        edited["config"]["instructions"]
        == original["config"]["instructions"] + "\n\nWorkspace instructions:\nAnswer in Chinese."
    )
    assert edited["config"]["model_settings"] == body["config"]["model_settings"]
    assert "preset" not in edited["config"]
    # Already accepted input retains its original revision even before any worker starts it.
    executed = await service.client.get(f"{service.api}/runs/{started['run']['id']}/agent")
    assert executed.status_code == 200, executed.text
    assert executed.json()["config"]["instructions"] == original["config"]["instructions"]
    again, _, latest = await prepared(service, endpoint)
    assert latest["id"] == edited["id"]
    stale = await service.client.post(f"{path}/revisions", json=body, headers={"If-Match": head.headers["etag"]})
    assert stale.status_code == 412
    repeated = await service.client.post(f"{path}/revisions", json=body, headers={"If-Match": again.headers["etag"]})
    assert repeated.json()["id"] == edited["id"]
    assert (await service.client.get(path)).headers["etag"] == again.headers["etag"]
    # Re-edit from the new revision replaces the supplement, never appends it twice.
    second = await service.client.post(
        f"{path}/revisions",
        json={"config": edited["config"], "preset": {"base_revision_id": edited["id"], "instructions": "Be concise."}},
        headers={"If-Match": again.headers["etag"]},
    )
    assert second.status_code == 201, second.text
    assert "Answer in Chinese." not in second.json()["config"]["instructions"]
    assert second.json()["config"]["instructions"].count("Workspace instructions:") == 1
    copied = await service.client.post(f"{path}/duplicate", json={"name": "Independent copy"})
    assert copied.status_code == 201, copied.text
    copy = copied.json()
    copied_revision = await service.client.get(
        f"{service.api}/agents/{copy['id']}/revisions/{copy['default_revision_id']}"
    )
    assert copied_revision.json()["config"] == second.json()["config"]
    assert copied_revision.json()["preset"] is None
    current = await service.client.get(path)
    restored = await service.client.post(
        f"{path}/revisions/{original['id']}/set-default", headers={"If-Match": current.headers["etag"]}
    )
    assert restored.status_code == 200, restored.text
    _, _, latest = await prepared(service, endpoint)
    assert latest == original


@pytest.mark.parametrize("endpoint", ["agent-composer", "finding-agent"])
async def test_builtin_edit_rejects_protected_fields_and_foreign_bases(service, scripted_model, runs_kit, endpoint):
    await runs_kit.create_model(service, scripted_model)
    head, path, original = await prepared(service, endpoint)
    other_endpoint = "finding-agent" if endpoint == "agent-composer" else "agent-composer"
    _, _, other = await prepared(service, other_endpoint)
    preset = {"base_revision_id": original["id"], "instructions": "Be brief."}
    for fields in (
        {"instructions": "Replace official instructions"},
        {"toolsets": {}},
        {"user_questions": not original["config"]["user_questions"]},
        {"model_settings": {"extra_body": {"reasoning_effort": "high"}}},
        {"model_settings": {"parallel_tool_calls": False}},
        {"memory": {"enabled": not original["config"]["memory"]["enabled"]}},
    ):
        body = {"config": {**original["config"], **fields}, "preset": preset}
        for url, payload in (
            (f"{path}/revisions", body),
            (f"{service.api}/agents/validate", {**body, "agent_id": head.json()["id"]}),
        ):
            denied = await service.client.post(url, json=payload, headers={"If-Match": head.headers["etag"]})
            assert denied.status_code == 400, denied.text
            assert denied.json()["error"]["code"] == "invalid_argument"
    foreign = await service.client.post(
        f"{path}/revisions",
        json={"config": original["config"], "preset": {**preset, "base_revision_id": other["id"]}},
        headers={"If-Match": head.headers["etag"]},
    )
    assert foreign.status_code == 404, foreign.text
    forged = await service.client.post(
        f"{path}/revisions",
        json={"config": original["config"], "preset": {**preset, "official_instructions": "Forged"}},
        headers={"If-Match": head.headers["etag"]},
    )
    assert forged.status_code == 400, forged.text
    for method, suffix, body in (("patch", "", {"name": "New identity"}), ("post", "/archive", None)):
        denied = await getattr(service.client, method)(
            path + suffix, json=body, headers={"If-Match": head.headers["etag"]}
        )
        assert denied.status_code == 409, denied.text
    assert (await service.client.get(path)).headers["etag"] == head.headers["etag"]


@pytest.mark.parametrize(
    ("endpoint", "module", "attribute"),
    [("agent-composer", composer, "_INSTRUCTIONS"), ("finding-agent", finding_preset, "INSTRUCTIONS")],
)
async def test_official_update_is_a_candidate_and_preserves_user_settings(
    service, scripted_model, runs_kit, monkeypatch, endpoint, module, attribute
):
    await runs_kit.create_model(service, scripted_model)
    head, path, original = await prepared(service, endpoint)
    saved = await service.client.post(
        f"{path}/revisions",
        json={
            "config": {**original["config"], "model_settings": {"max_tokens": 1536}},
            "preset": {"base_revision_id": original["id"], "instructions": "Keep my preferences."},
        },
        headers={"If-Match": head.headers["etag"]},
    )
    assert saved.status_code == 201, saved.text
    selected = saved.json()
    monkeypatch.setattr(module, attribute, "Updated official instructions.")
    refreshed, _, current = await prepared(service, endpoint)
    assert current == selected
    versions = (await service.client.get(f"{path}/revisions")).json()["items"]
    assert len(versions) == 3
    candidate = versions[0]
    assert candidate["note"] == "Official preset update"
    assert candidate["config"]["model_settings"] == {"max_tokens": 1536}
    assert candidate["preset"]["instructions"] == "Keep my preferences."
    assert (
        candidate["config"]["instructions"]
        == "Updated official instructions.\n\nWorkspace instructions:\nKeep my preferences."
    )
    again, _, _ = await prepared(service, endpoint)
    assert again.headers["etag"] == refreshed.headers["etag"]
    adopted = await service.client.post(
        f"{path}/revisions/{candidate['id']}/set-default", headers={"If-Match": again.headers["etag"]}
    )
    assert adopted.status_code == 200, adopted.text
    _, _, current = await prepared(service, endpoint)
    assert current["id"] == candidate["id"]
    # Editing a historical revision still uses that revision's official instruction snapshot.
    edit_old = await service.client.post(
        f"{path}/revisions",
        json={
            "config": selected["config"],
            "preset": {"base_revision_id": selected["id"], "instructions": "Old base."},
        },
        headers={"If-Match": adopted.headers["etag"]},
    )
    assert edit_old.status_code == 201, edit_old.text
    assert edit_old.json()["config"]["instructions"].startswith(original["config"]["instructions"])


async def test_legacy_revision_treats_full_instructions_as_official(service, scripted_model, runs_kit):
    from a13n_service.resources.agents.service import resolve_agent
    from a13n_service.tenancy.authorize import Principal

    await runs_kit.create_model(service, scripted_model)
    head, path, _ = await prepared(service, "agent-composer")
    # A legacy row has no editing metadata. Insert a historical definition through the normal publisher.
    async with transaction(service.runtime.storage) as session:
        row = await resolve_agent(session, service.tenant.workspace_id, head.json()["id"], lock=True)
        legacy, _ = await publish(
            session,
            row,
            AgentRevisionRow,
            composer.configuration("scripted").model_copy(update={"instructions": "Historical official instructions."}),
            actor=Principal(service.tenant.principal_id, "user", ()),
            note="Legacy",
            preset=None,
        )
        legacy_id = legacy.id
    current = await service.client.get(path)
    old = (await service.client.get(f"{path}/revisions/{legacy_id}")).json()
    assert old["preset"] is None
    changed = await service.client.post(
        f"{path}/revisions",
        json={"config": old["config"], "preset": {"base_revision_id": legacy_id, "instructions": "My supplement."}},
        headers={"If-Match": current.headers["etag"]},
    )
    assert changed.status_code == 201, changed.text
    assert (
        changed.json()["config"]["instructions"]
        == "Historical official instructions.\n\nWorkspace instructions:\nMy supplement."
    )
    assert (await service.client.get(f"{path}/revisions/{legacy_id}")).json() == old


def test_composition_bounds_and_config_shape():
    from a13n_service.infra.errors import ServiceError

    config = AgentConfig(model="test", instructions="Official")
    preset = preset_edits.definition_snapshot(config)
    assert preset_edits.compose(config, preset) == config
    with pytest.raises(ServiceError):
        preset_edits.compose(config, preset.model_copy(update={"instructions": "x" * (256 * 1024)}))
    # The ordinary execution configuration has acquired no preset-specific fields.
    assert "official_instructions" not in json.dumps(config.model_dump(mode="json"))
