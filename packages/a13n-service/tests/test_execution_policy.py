"""Execution choices inherit per leaf and become immutable at Run acceptance."""

import asyncio

import pytest
from a13n_service.execution_policy import ExecutionOverride, ExecutionPolicy
from a13n_service.runs.inbox import Request
from a13n_service.runs.schemas import Message, RunOptions

pytestmark = pytest.mark.anyio


def test_sparse_layers_and_patch_resets():
    agent = ExecutionOverride.model_validate({"tools": {"mode": "inline", "foreground_wait_seconds": 20}})
    thread = ExecutionOverride.model_validate({"tools": {"mode": "auto"}, "steering": "boundary"})
    run = ExecutionOverride.model_validate({"tools": {"foreground_wait_seconds": 0}})
    assert ExecutionPolicy().overlay(agent, thread, run).model_dump() == {
        "tools": {"mode": "auto", "foreground_wait_seconds": 0},
        "steering": "boundary",
    }
    cleared = thread.patch(ExecutionOverride.model_validate({"tools": {"mode": None}}))
    assert ExecutionPolicy().overlay(agent, cleared).tools.mode == "inline"
    assert cleared.steering == "boundary"
    assert thread.patch(ExecutionOverride()).model_dump() == thread.model_dump()


def test_execution_choices_affect_request_identity_but_not_other_options_digest():
    plain = Message.model_validate(
        {"agent_id": "agt_000000000000000000000000", "payload": {"content": [{"type": "text", "text": "hi"}]}}
    )
    choice = plain.model_copy(update={"options": RunOptions(execution=ExecutionOverride(steering="boundary"))})
    assert plain.options.digest() == choice.options.digest()
    assert Request.of("key", "message", "thread", plain).digest != Request.of("key", "message", "thread", choice).digest


async def _patch(service, runs_kit, thread_id, body):
    thread = await runs_kit.get_thread(service, thread_id)
    response = await service.client.patch(
        f"{service.api}/threads/{thread_id}", json=body, headers=runs_kit.if_match(thread)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_policy_is_frozen_and_thread_patch_is_sparse(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(
        service, scripted_model, execution={"tools": {"mode": "inline", "foreground_wait_seconds": 12}}
    )
    started = await runs_kit.start_thread(
        service,
        agent,
        "first",
        execution={"tools": {"mode": "auto"}, "steering": "boundary"},
        options={"execution": {"tools": {"foreground_wait_seconds": 0}}},
    )
    thread_id, run_id = started["thread"]["id"], started["run"]["id"]
    frozen = {"tools": {"mode": "auto", "foreground_wait_seconds": 0}, "steering": "boundary"}
    assert started["run"]["options"]["execution"] == frozen
    changed = await _patch(
        service, runs_kit, thread_id, {"execution": {"tools": {"mode": None, "foreground_wait_seconds": 7}}}
    )
    assert changed["execution"]["steering"] == "boundary"
    assert changed["execution"]["tools"] == {"mode": None, "foreground_wait_seconds": 7}
    unchanged = await _patch(service, runs_kit, thread_id, {"labels": {"case": "policy"}})
    assert unchanged["execution"] == changed["execution"]
    assert (await runs_kit.get_run(service, run_id))["options"]["execution"] == frozen
    scripted_model.say("done")
    await (await runs_kit.attempt(service))
    later = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "second"))
    assert later.status_code == 201, later.text
    assert later.json()["run"]["options"]["execution"] == {
        "tools": {"mode": "inline", "foreground_wait_seconds": 7},
        "steering": "boundary",
    }
    reset_tools = await _patch(service, runs_kit, thread_id, {"execution": {"tools": None}})
    assert reset_tools["execution"]["tools"] is None
    reset = await _patch(service, runs_kit, thread_id, {"execution": None})
    assert reset["execution"] is None
    scripted_model.say("done again")
    await (await runs_kit.attempt(service))
    last = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "third"))
    assert last.json()["run"]["options"]["execution"] == {
        "tools": {"mode": "inline", "foreground_wait_seconds": 12},
        "steering": "immediate",
    }


async def test_queued_acceptance_uses_new_agent_defaults_and_current_thread_choices(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(service, scripted_model)
    started = await runs_kit.start_thread(service, agent, "first")
    thread_id = started["thread"]["id"]
    queued = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "later", delivery="next_run"))
    assert queued.status_code == 201, queued.text
    revision = await service.client.get(f"{service.api}/agents/{agent['id']}/revisions/{agent['default_revision_id']}")
    config = revision.json()["config"]
    config["execution"] = {"tools": {"mode": "inline", "foreground_wait_seconds": 11}, "steering": "boundary"}
    updated = await service.client.post(
        f"{service.api}/agents/{agent['id']}/revisions", json={"config": config}, headers=runs_kit.if_match(agent)
    )
    assert updated.status_code == 201, updated.text
    await _patch(service, runs_kit, thread_id, {"execution": {"tools": {"foreground_wait_seconds": 2}}})
    scripted_model.say("done")
    await (await runs_kit.attempt(service))
    thread = await runs_kit.get_thread(service, thread_id)
    later = await runs_kit.get_run(service, thread["current_run_id"])
    assert later["agent_revision_id"] == updated.json()["id"]
    assert later["options"]["execution"] == {
        "tools": {"mode": "inline", "foreground_wait_seconds": 2},
        "steering": "boundary",
    }
    assert started["run"]["options"]["execution"]["tools"]["mode"] == "auto"


async def test_steer_cannot_change_execution_but_next_run_can(skill_service, scripted_model, runs_kit):
    service = skill_service
    agent = await runs_kit.create_agent(service, scripted_model, **runs_kit.LOOKUP)
    started = await runs_kit.start_thread(
        service, agent, "first", options={"execution": {"tools": {"mode": "inline"}, "steering": "boundary"}}
    )
    thread_id = started["thread"]["id"]
    for policy in ({"tools": {"mode": "auto"}}, {"steering": "immediate"}, {"tools": {"foreground_wait_seconds": 9}}):
        refused = await runs_kit.submit(
            service, thread_id, runs_kit.message(agent, "change", options={"execution": policy})
        )
        assert refused.status_code == 409 and "execution_policy_immutable" in refused.text
    same = await runs_kit.submit(
        service, thread_id, runs_kit.message(agent, "same", options={"execution": {"tools": {"mode": "inline"}}})
    )
    assert same.status_code == 201, same.text
    edited = await service.client.patch(
        f"{service.api}/threads/{thread_id}/inbox/{same.json()['entry']['id']}",
        json={"options": {"execution": {"steering": "immediate"}}},
        headers=runs_kit.if_match(await runs_kit.get_thread(service, thread_id)),
    )
    assert edited.status_code == 409 and "execution_policy_immutable" in edited.text
    later = await runs_kit.submit(
        service,
        thread_id,
        runs_kit.message(agent, "new run", delivery="next_run", options={"execution": {"steering": "immediate"}}),
    )
    assert later.status_code == 201, later.text
    runs_kit.lookup(scripted_model, call_id="call_boundary")
    scripted_model.say("done")
    await (await runs_kit.attempt(service))
    entries = {entry["id"]: entry for entry in await runs_kit.inbox(service, thread_id)}
    assert entries[same.json()["entry"]["id"]]["assigned_run_id"] == started["run"]["id"]
    assert entries[later.json()["entry"]["id"]]["assigned_run_id"] != started["run"]["id"]


@pytest.mark.parametrize("mode", ["inline", "auto"])
@pytest.mark.parametrize("steering", ["boundary", "immediate"])
async def test_independent_modes_reach_worker(skill_service, scripted_model, runs_kit, mode, steering, monkeypatch):
    service = skill_service
    from a13n_harness import HarnessRunStream
    from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
    from a13n_service.infra.db import short_session
    from a13n_service.runs.host import Host
    from a13n_service.runs.mailbox import Mailbox
    from a13n_service.runs.tables import InboxEntryRow

    original = Host.capabilities
    original_steer = HarnessRunStream.steer
    delivered = []

    async def steer(self, *args, **kwargs):
        entry_id = kwargs["input_id"]
        async with short_session(service.runtime.storage) as session:
            entry = await session.get(InboxEntryRow, entry_id)
            assert entry.status == "assigned"
            assert entry.assigned_run_id == started["run"]["id"]
            assert entry.incorporated_checkpoint_seq is None
        delivered.append(entry_id)
        return await original_steer(self, *args, **kwargs)

    monkeypatch.setattr(HarnessRunStream, "steer", steer)
    captured = []
    listened = []
    original_listen = Mailbox.listen

    def listen(self, thread_id):
        listened.append(thread_id)
        return original_listen(self, thread_id)

    monkeypatch.setattr(Mailbox, "listen", listen)

    async def no_remote_input(self, thread_ids):
        return set()

    # A local committed message must preempt without waiting for a database scan.
    monkeypatch.setattr(Mailbox, "_pending", no_remote_input)

    def capabilities(self, agent, *, retain_tools=False):
        features = original(self, agent, retain_tools=retain_tools)
        if retain_tools:
            captured.append(self.plan.execution.model_dump())
            assert any(isinstance(feature, ToolExecutionCapability) for feature in features)
        return features

    monkeypatch.setattr(Host, "capabilities", capabilities)
    agent = await runs_kit.create_agent(service, scripted_model, **runs_kit.LOOKUP)
    gate = asyncio.Event()
    runs_kit.lookup(scripted_model, call_id="call_lookup", gate=gate)
    scripted_model.say("done")
    started = await runs_kit.start_thread(
        service,
        agent,
        "start",
        options={"execution": {"tools": {"mode": mode, "foreground_wait_seconds": 0}, "steering": steering}},
    )
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    submitted = await runs_kit.submit(service, started["thread"]["id"], runs_kit.message(agent, "additional input"))
    assert submitted.status_code == 201, submitted.text
    if steering == "immediate":
        following = await asyncio.wait_for(scripted_model.request(), 10)
        assert "additional input" in str(runs_kit.user_texts(following))
    else:
        await asyncio.sleep(0.1)
        assert scripted_model.requests.empty()
    gate.set()
    await running
    assert captured == [{"tools": {"mode": mode, "foreground_wait_seconds": 0}, "steering": steering}]
    assert listened == ([started["thread"]["id"]] if steering == "immediate" else [])
    assert service.runtime.mailbox._task is None
    entry_id = submitted.json()["entry"]["id"]
    assert delivered == [entry_id]
    async with short_session(service.runtime.storage) as session:
        entry = await session.get(InboxEntryRow, entry_id)
        assert entry.status == "consumed"
        assert entry.assigned_run_id == started["run"]["id"]
        assert entry.incorporated_checkpoint_seq is not None
        assert entry.finished_at is not None


async def test_resume_keeps_frozen_policy_after_thread_edit(service, scripted_model, runs_kit):
    lookup = {"name": "lookup", "description": "Look up", "parameters_json_schema": {"type": "object"}}
    agent = await runs_kit.create_agent(service, scripted_model, client_tools=[lookup])
    scripted_model.call("lookup", {}, call_id="call_lookup")
    started = await runs_kit.start_thread(
        service,
        agent,
        "lookup",
        options={"execution": {"tools": {"mode": "inline"}, "steering": "boundary", "environment": {"MODE": "frozen"}}},
    )
    await (await runs_kit.attempt(service))
    await _patch(
        service,
        runs_kit,
        started["thread"]["id"],
        {"execution": {"tools": {"mode": "auto"}, "steering": "immediate", "environment": {"MODE": "changed"}}},
    )
    resumed = await service.client.post(
        f"{service.api}/runs/{started['run']['id']}/resume",
        json={"approvals": {}, "calls": {"call_lookup": {"status": "returned", "value": 42}}},
        headers=runs_kit.fresh_key(),
    )
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["options"]["execution"] == started["run"]["options"]["execution"]


def test_environment_precedence_patch_and_legacy_serialization():
    from pydantic import ValidationError

    agent = ExecutionOverride(environment={"MODE": "agent", "BASE": "base"})
    thread = ExecutionOverride(environment={"MODE": "thread", "THREAD": "thread"})
    run = ExecutionOverride(environment={"MODE": "", "RUN": "run"})
    policy = ExecutionPolicy().overlay(agent, thread, run)
    assert policy.environment == {"BASE": "base", "MODE": "", "THREAD": "thread", "RUN": "run"}
    assert ExecutionPolicy().overlay(policy.override()).environment == policy.environment
    replaced = thread.patch(ExecutionOverride(environment={"NEW": "new"}))
    assert ExecutionPolicy().overlay(agent, replaced).environment == {"BASE": "base", "MODE": "agent", "NEW": "new"}
    for reset in (None, {}):
        cleared = thread.patch(ExecutionOverride(environment=reset))
        assert ExecutionPolicy().overlay(agent, cleared).environment == agent.environment
    assert thread.patch(ExecutionOverride()).environment == thread.environment
    assert "environment" not in ExecutionPolicy().model_dump()
    assert "environment" not in ExecutionOverride(environment={}).model_dump()
    for values in ({"BAD-NAME": "value"}, {"KEY": "bad\x00value"}, {"OTEL_EXPORTER_OTLP_ENDPOINT": "bad"}):
        with pytest.raises(ValidationError):
            ExecutionOverride(environment=values)


async def test_environment_freezes_at_acceptance_and_thread_map_replacement(skill_service, scripted_model, runs_kit):
    service = skill_service
    agent = await runs_kit.create_agent(
        service, scripted_model, execution={"environment": {"MODE": "agent", "BASE": "base"}}, **runs_kit.LOOKUP
    )
    started = await runs_kit.start_thread(
        service,
        agent,
        "first",
        execution={"environment": {"MODE": "thread", "THREAD": "thread"}},
        options={"execution": {"environment": {"MODE": "run"}}},
    )
    thread_id = started["thread"]["id"]
    frozen = {"BASE": "base", "MODE": "run", "THREAD": "thread"}
    assert started["run"]["options"]["execution"]["environment"] == frozen
    await _patch(service, runs_kit, thread_id, {"execution": {"environment": {"NEW": "new"}}})
    assert (await runs_kit.get_run(service, started["run"]["id"]))["options"]["execution"]["environment"] == frozen
    same = await runs_kit.submit(
        service, thread_id, runs_kit.message(agent, "same", options={"execution": {"environment": {"MODE": "run"}}})
    )
    assert same.status_code == 201, same.text
    changed = await runs_kit.submit(
        service,
        thread_id,
        runs_kit.message(agent, "changed", options={"execution": {"environment": {"MODE": "different"}}}),
    )
    assert changed.status_code == 409 and "execution_policy_immutable" in changed.text
    queued = await runs_kit.submit(
        service,
        thread_id,
        runs_kit.message(
            agent, "second", delivery="next_run", options={"execution": {"environment": {"NEW": "queued"}}}
        ),
    )
    assert queued.status_code == 201, queued.text
    # A queued entry has not resolved its inherited choices yet.
    await _patch(service, runs_kit, thread_id, {"execution": {"environment": {"LATE": "late"}}})
    # A tool boundary consumes the steer before the final answer; otherwise it can become the next Run.
    runs_kit.lookup(scripted_model, call_id="call_environment_boundary")
    scripted_model.say("done")
    await (await runs_kit.attempt(service))
    current = await runs_kit.get_thread(service, thread_id)
    later = await runs_kit.get_run(service, current["current_run_id"])
    assert later["options"]["execution"]["environment"] == {
        "BASE": "base",
        "MODE": "agent",
        "LATE": "late",
        "NEW": "queued",
    }
    entries = {entry["id"]: entry for entry in await runs_kit.inbox(service, thread_id)}
    assert entries[same.json()["entry"]["id"]]["assigned_run_id"] == started["run"]["id"]
    assert entries[queued.json()["entry"]["id"]]["assigned_run_id"] == later["id"]
    reset = await _patch(service, runs_kit, thread_id, {"execution": {"environment": None}})
    assert not (reset["execution"] or {}).get("environment")
