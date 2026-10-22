"""Managed Agent edits keep user instructions in revisions and official guidance in Host capabilities."""

import json

import pytest
from a13n_service.infra.db import transaction
from a13n_service.resources.agents import composer, instructions
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.agents.service import resolve_agent
from a13n_service.resources.agents.tables import AgentRevisionRow
from a13n_service.resources.revisions import publish
from a13n_service.runs.findings import preset as finding_preset
from a13n_service.tenancy.authorize import Principal

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
async def test_edit_saves_only_user_instructions_and_keeps_selected_version(
    service, scripted_model, runs_kit, endpoint
):
    model = await runs_kit.create_model(service, scripted_model)
    head, path, original = await prepared(service, endpoint)
    assert original["uses_builtin_instructions"]
    assert original["config"]["instructions"] == ""
    assert original["builtin_instructions"]
    started = await runs_kit.start_thread(service, head.json(), "Use the original revision")
    body = {
        "config": {
            **original["config"],
            "model": model,
            "model_settings": {"max_tokens": 2048, "temperature": 0.2},
            "instructions": "Answer in Chinese.",
        }
    }
    valid = await service.client.post(f"{service.api}/agents/validate", json={**body, "agent_id": head.json()["id"]})
    assert valid.status_code == 204, valid.text
    saved = await service.client.post(f"{path}/revisions", json=body, headers={"If-Match": head.headers["etag"]})
    assert saved.status_code == 201, saved.text
    edited = saved.json()
    assert edited["id"] != original["id"]
    assert edited["config"] == body["config"]
    assert edited["builtin_instructions"] == original["builtin_instructions"]
    assert edited["uses_builtin_instructions"]
    # An accepted run still retains the supplement and format of its original revision.
    executed = await service.client.get(f"{service.api}/runs/{started['run']['id']}/agent")
    assert executed.status_code == 200, executed.text
    assert executed.json()["config"]["instructions"] == ""
    assert executed.json()["revision"]["uses_builtin_instructions"]
    # The read never presents today's official text as a snapshot of a historical Run's prompt.
    assert executed.json()["revision"]["builtin_instructions"] is None
    again, _, latest = await prepared(service, endpoint)
    assert latest["id"] == edited["id"]
    stale = await service.client.post(f"{path}/revisions", json=body, headers={"If-Match": head.headers["etag"]})
    assert stale.status_code == 412
    repeated = await service.client.post(f"{path}/revisions", json=body, headers={"If-Match": again.headers["etag"]})
    assert repeated.json()["id"] == edited["id"]
    assert (await service.client.get(path)).headers["etag"] == again.headers["etag"]
    second = await service.client.post(
        f"{path}/revisions",
        json={"config": {**edited["config"], "instructions": "Be concise."}},
        headers={"If-Match": again.headers["etag"]},
    )
    assert second.status_code == 201, second.text
    assert second.json()["config"]["instructions"] == "Be concise."
    copied = await service.client.post(f"{path}/duplicate", json={"name": "Independent copy"})
    assert copied.status_code == 201, copied.text
    copy = copied.json()
    copied_revision = await service.client.get(
        f"{service.api}/agents/{copy['id']}/revisions/{copy['default_revision_id']}"
    )
    assert copied_revision.json()["config"]["instructions"] == original["builtin_instructions"] + "\n\nBe concise."
    assert not copied_revision.json()["uses_builtin_instructions"]
    assert copied_revision.json()["builtin_instructions"] is None
    current = await service.client.get(path)
    restored = await service.client.post(
        f"{path}/revisions/{original['id']}/set-default", headers={"If-Match": current.headers["etag"]}
    )
    assert restored.status_code == 200, restored.text
    _, _, latest = await prepared(service, endpoint)
    assert latest == original


@pytest.mark.parametrize("endpoint", ["agent-composer", "finding-agent"])
async def test_builtin_edit_rejects_protected_fields_and_server_metadata(service, scripted_model, runs_kit, endpoint):
    await runs_kit.create_model(service, scripted_model)
    head, path, original = await prepared(service, endpoint)
    for fields in (
        {"toolsets": {}},
        {"user_questions": not original["config"]["user_questions"]},
        {"model_settings": {"extra_body": {"reasoning_effort": "high"}}},
        {"model_settings": {"parallel_tool_calls": False}},
        {"memory": {"enabled": not original["config"]["memory"]["enabled"]}},
    ):
        body = {"config": {**original["config"], **fields}}
        for url, payload in (
            (f"{path}/revisions", body),
            (f"{service.api}/agents/validate", {**body, "agent_id": head.json()["id"]}),
        ):
            denied = await service.client.post(url, json=payload, headers={"If-Match": head.headers["etag"]})
            assert denied.status_code == 400, denied.text
            assert denied.json()["error"]["code"] == "invalid_argument"
    for field, value in (("uses_builtin_instructions", False), ("builtin_instructions", "Forged"), ("preset", {})):
        forged = await service.client.post(
            f"{path}/revisions",
            json={"config": original["config"], field: value},
            headers={"If-Match": head.headers["etag"]},
        )
        assert forged.status_code == 400, forged.text
    for method, suffix, body in (("patch", "", {"name": "New identity"}), ("post", "/archive", None)):
        denied = await getattr(service.client, method)(
            path + suffix, json=body, headers={"If-Match": head.headers["etag"]}
        )
        assert denied.status_code == 409, denied.text
    assert (await service.client.get(path)).headers["etag"] == head.headers["etag"]


@pytest.mark.parametrize(("endpoint", "attribute"), [("agent-composer", "COMPOSER"), ("finding-agent", "FINDING")])
async def test_official_guidance_comes_from_the_worker_without_revising_user_config(
    service, scripted_model, runs_kit, monkeypatch, endpoint, attribute
):
    await runs_kit.pause_sweeps(service)
    await runs_kit.create_model(service, scripted_model)
    monkeypatch.setattr(instructions, attribute, "Official policy before deployment.")
    head, path, original = await prepared(service, endpoint)
    saved = await service.client.post(
        f"{path}/revisions",
        json={"config": {**original["config"], "instructions": "My saved requirements."}},
        headers={"If-Match": head.headers["etag"]},
    )
    assert saved.status_code == 201, saved.text
    current = (await service.client.get(path)).json()
    accepted = await runs_kit.start_thread(service, current, "Inspect this")
    monkeypatch.setattr(instructions, attribute, "Official policy after deployment.")
    again, _, revision = await prepared(service, endpoint)
    assert revision["id"] == saved.json()["id"]
    assert revision["config"]["instructions"] == "My saved requirements."
    assert revision["builtin_instructions"] == "Official policy after deployment."
    assert again.json()["version"] == current["version"]
    scripted_model.say("Done")
    async with runs_kit.platform_runtime(service) as runtime:
        await (await runs_kit.attempt(service, runtime=runtime))
    assert (await runs_kit.get_run(service, accepted["run"]["id"]))["status"] == "completed"
    prompt = json.dumps((await scripted_model.request())["messages"])
    assert prompt.count("Official policy after deployment.") == 1
    assert prompt.count("My saved requirements.") == 1
    assert "Official policy before deployment." not in prompt


@pytest.mark.parametrize(("endpoint", "module"), [("agent-composer", composer), ("finding-agent", finding_preset)])
async def test_tool_definition_update_is_only_a_candidate(
    service, scripted_model, runs_kit, monkeypatch, endpoint, module
):
    await runs_kit.create_model(service, scripted_model)
    head, path, original = await prepared(service, endpoint)
    saved = await service.client.post(
        f"{path}/revisions",
        json={"config": {**original["config"], "model_settings": {"max_tokens": 1536}, "instructions": "Keep this."}},
        headers={"If-Match": head.headers["etag"]},
    )
    assert saved.status_code == 201, saved.text
    definition = module.configuration
    monkeypatch.setattr(
        module, "configuration", lambda model: definition(model).model_copy(update={"lazy_environment": False})
    )
    refreshed, _, current = await prepared(service, endpoint)
    assert current == saved.json()
    versions = (await service.client.get(f"{path}/revisions")).json()["items"]
    assert len(versions) == 3
    candidate = versions[0]
    assert candidate["config"]["lazy_environment"] is False
    assert candidate["config"]["model_settings"] == {"max_tokens": 1536}
    assert candidate["config"]["instructions"] == "Keep this."
    assert candidate["uses_builtin_instructions"]
    again, _, _ = await prepared(service, endpoint)
    assert again.headers["etag"] == refreshed.headers["etag"]
    adopted = await service.client.post(
        f"{path}/revisions/{candidate['id']}/set-default", headers={"If-Match": again.headers["etag"]}
    )
    assert adopted.status_code == 200, adopted.text
    _, _, current = await prepared(service, endpoint)
    assert current["id"] == candidate["id"]


@pytest.mark.parametrize("legacy_text", ["Historical official instructions.", ""])
async def test_legacy_revisions_remain_immutable_and_can_adopt_capabilities(
    service, scripted_model, runs_kit, monkeypatch, legacy_text
):
    await runs_kit.pause_sweeps(service)
    await runs_kit.create_model(service, scripted_model)
    head, path, _ = await prepared(service, "agent-composer")
    async with transaction(service.runtime.storage) as session:
        row = await resolve_agent(session, service.tenant.workspace_id, head.json()["id"], lock=True)
        legacy, _ = await publish(
            session,
            row,
            AgentRevisionRow,
            composer.configuration("scripted").model_copy(update={"instructions": legacy_text}),
            actor=Principal(service.tenant.principal_id, "user", ()),
            note="Legacy",
            reuse_default=False,
            uses_builtin_instructions=False,
        )
        legacy_id = legacy.id
    current = await service.client.get(path)
    old = (await service.client.get(f"{path}/revisions/{legacy_id}")).json()
    assert not old["uses_builtin_instructions"]
    monkeypatch.setattr(instructions, "COMPOSER", "New capability policy.")
    # Even an explicit old revision executed after a deployment retains its complete instructions.
    accepted = await runs_kit.start_thread(service, current.json(), "Legacy request")
    scripted_model.say("Done")
    async with runs_kit.platform_runtime(service) as runtime:
        await (await runs_kit.attempt(service, runtime=runtime))
    assert (await runs_kit.get_run(service, accepted["run"]["id"]))["status"] == "completed"
    prompt = json.dumps((await scripted_model.request())["messages"])
    assert "New capability policy." not in prompt
    if legacy_text:
        assert prompt.count(legacy_text) == 1
    # A model-only save does not accidentally turn the old official text into a user supplement.
    changed = await service.client.post(
        f"{path}/revisions", json={"config": old["config"]}, headers={"If-Match": current.headers["etag"]}
    )
    assert changed.status_code == 201, changed.text
    assert changed.json()["id"] != legacy_id
    assert changed.json()["uses_builtin_instructions"]
    assert changed.json()["config"]["instructions"] == ""
    historical = (await service.client.get(f"{path}/revisions/{legacy_id}")).json()
    assert historical["config"] == old["config"] and historical["digest"] == old["digest"]
    assert not historical["uses_builtin_instructions"]
    restored = await service.client.post(
        f"{path}/revisions/{legacy_id}/set-default",
        headers={"If-Match": (await service.client.get(path)).headers["etag"]},
    )
    assert restored.status_code == 200, restored.text
    prepared_again, _, selected = await prepared(service, "agent-composer")
    assert selected["id"] == legacy_id
    assert prepared_again.json()["default_revision_id"] == legacy_id


async def test_waiting_resume_uses_current_official_guidance_and_frozen_supplement(
    service, scripted_model, runs_kit, monkeypatch
):
    await runs_kit.pause_sweeps(service)
    await runs_kit.create_model(service, scripted_model)
    monkeypatch.setattr(instructions, "COMPOSER", "Earlier official policy.")
    head, path, original = await prepared(service, "agent-composer")
    saved = await service.client.post(
        f"{path}/revisions",
        json={"config": {**original["config"], "instructions": "User policy."}},
        headers={"If-Match": head.headers["etag"]},
    )
    assert saved.status_code == 201, saved.text
    scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="question")
    started = await runs_kit.start_thread(service, (await service.client.get(path)).json(), "Choose")
    async with runs_kit.platform_runtime(service) as runtime:
        await (await runs_kit.attempt(service, runtime=runtime))
        waiting = await runs_kit.get_run(service, started["run"]["id"])
        assert waiting["status"] == "waiting", waiting
        assert "Earlier official policy." in json.dumps((await scripted_model.request())["messages"])
        monkeypatch.setattr(instructions, "COMPOSER", "Later official policy.")
        resumed = await service.client.post(
            f"{service.api}/runs/{waiting['id']}/resume",
            json={"approvals": {}, "calls": {"question": {"status": "returned", "value": {"response": "Blue"}}}},
            headers=runs_kit.fresh_key(),
        )
        assert resumed.status_code == 201, resumed.text
        scripted_model.say("Done")
        await (await runs_kit.attempt(service, runtime=runtime))
    assert (await runs_kit.get_run(service, resumed.json()["id"]))["status"] == "completed"
    prompt = json.dumps((await scripted_model.request())["messages"])
    assert "Later official policy." in prompt and "Earlier official policy." not in prompt
    assert "User policy." in prompt
    assert resumed.json()["agent_revision_id"] == waiting["agent_revision_id"]


async def test_inline_builtin_instructions_apply_only_to_the_builtin_child(
    service, scripted_model, runs_kit, monkeypatch
):
    await runs_kit.pause_sweeps(service)
    model = await runs_kit.create_model(service, scripted_model)
    monkeypatch.setattr(instructions, "COMPOSER", "Official child guidance.")
    head, path, original = await prepared(service, "agent-composer")
    saved = await service.client.post(
        f"{path}/revisions",
        json={"config": {**original["config"], "instructions": "Role: worker"}},
        headers={"If-Match": head.headers["etag"]},
    )
    assert saved.status_code == 201, saved.text
    parent = await runs_kit.add_agent(
        service,
        "coordinator",
        model,
        instructions="Role: coordinator",
        subagent_mode="inline",
        subagents={"helper": {"agent_id": head.json()["id"]}},
    )
    scripted_model.call("delegate", {"subagent": "helper", "prompt": "compute"}, call_id="child", to="coordinator")
    scripted_model.say("42", to="worker")
    scripted_model.say("Done", to="coordinator")
    accepted = await runs_kit.start_thread(service, parent, "Ask the helper")
    async with runs_kit.platform_runtime(service) as runtime:
        await (await runs_kit.attempt(service, runtime=runtime))
    assert (await runs_kit.get_run(service, accepted["run"]["id"]))["status"] == "completed"
    parent_prompt = json.dumps((await scripted_model.request())["messages"])
    child_prompt = json.dumps((await scripted_model.request())["messages"])
    assert "Official child guidance." not in parent_prompt
    assert child_prompt.count("Official child guidance.") == 1
    assert "Role: worker" in child_prompt


def test_copy_materialization_bounds_and_generic_config_shape():
    from a13n_service.infra.errors import ServiceError

    config = AgentConfig(model="test", instructions="User")
    assert instructions.materialize(config, None) == config
    assert instructions.materialize(config, "Official").instructions == "Official\n\nUser"
    with pytest.raises(ServiceError):
        instructions.materialize(config, "x" * (256 * 1024))
    assert "uses_builtin_instructions" not in AgentConfig.model_fields
    assert "preset" not in AgentConfig.model_fields
