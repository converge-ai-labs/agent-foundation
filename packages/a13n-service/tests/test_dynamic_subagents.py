"""Snapshots retain a leaf definition through acceptance, archival and continuation."""

import pytest
from a13n_harness.capabilities import AsyncResumeRequest, SubagentOperatorContext
from a13n_service.infra.db import short_session
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride, apply_override
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.claim import claim
from a13n_service.runs.dynamic_subagents import Spawn
from a13n_service.runs.subagents import ChildRuns
from a13n_service.runs.tables import ThreadRow
from pydantic import ValidationError
from pydantic_ai.exceptions import ToolFailed


def test_unified_subagents_replace_the_whole_policy() -> None:
    config = AgentConfig(model="parent", subagents={"dynamic": {"enabled": True, "default_thinking": False}})
    assert config.subagents.mode == "async"
    assert apply_override(config, AgentOverride(subagents={})).subagents.dynamic.enabled is False
    assert apply_override(config, AgentOverride(subagents=None)) == config
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"model": "parent", "subagent_mode": "async"})
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"model": "parent", "subagents": {"mode": "inline", "dynamic": {"enabled": True}}})


@pytest.mark.anyio
async def test_dynamic_snapshot_archive_replay_execution_resume_and_fork(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    parent = await runs_kit.create_agent(
        service,
        scripted_model,
        instructions="Authored scope.",
        subagents={"dynamic": {"enabled": True, "default_thinking": False}},
    )
    submitted = await runs_kit.start_thread(service, parent, "Parent task")
    (lease,) = await claim(service.runtime, worker_id="parent", worker_build="test", limit=1)
    operator = ChildRuns(service.runtime, lease, AttemptControl(), {})
    request = Spawn(name="research", instructions="Task role.", prompt="Child task", selection={"toolsets": []})
    spawned = await operator.spawn(request, "spawn-1")
    child = await runs_kit.get_run(service, spawned.execution_id)
    assert (child["agent_id"], child["agent_revision_id"], child["revision_selection"]) == (
        parent["id"],
        None,
        "snapshot",
    )
    assert child["parent_run_id"] is None
    assert child["options"]["execution"] == submitted["run"]["options"]["execution"]
    async with short_session(service.runtime.storage) as session:
        thread = await session.get_one(ThreadRow, child["thread_id"])
        snapshot = thread.agent_snapshot
    assert snapshot["definition_id"] == child["thread_id"]
    config = AgentConfig.model_validate(snapshot["config"])
    assert not any(item.enabled for item in config.toolsets.values())
    assert not config.subagents.named and not config.subagents.dynamic.enabled
    assert config.instructions == "Authored scope.\n\nTask role."
    assert config.model_settings["thinking"] is False
    archived = await service.client.post(
        f"{service.api}/agents/{parent['id']}/archive", headers=runs_kit.if_match(parent)
    )
    assert archived.status_code == 200, archived.text
    assert (await operator.spawn(request, "spawn-1")).execution_id == child["id"]
    with pytest.raises(ToolFailed):
        await operator.spawn(request, "spawn-2")
    scripted_model.say("Child result")
    await (await runs_kit.attempt(service))
    completed = await runs_kit.get_run(service, child["id"])
    assert completed["status"] == "completed", completed
    executed = await service.client.get(f"{service.api}/runs/{child['id']}/agent")
    assert executed.status_code == 200, executed.text
    assert executed.json()["source"] == "thread_snapshot" and executed.json()["revision"] is None
    context = SubagentOperatorContext(
        parent_thread_id=lease.thread_id,
        parent_run_id=lease.run_id,
        parent_agent_instance_id=lease.run_id,
        host_refs={},
    )
    with pytest.raises(ToolFailed):
        await operator.resume_host(context, AsyncResumeRequest(execution_id=child["id"], prompt="Continue"))
    restored = await service.client.post(
        f"{service.api}/agents/{parent['id']}/unarchive", headers=runs_kit.if_match(archived.json())
    )
    assert restored.status_code == 200, restored.text
    successor = await operator.resume_host(context, AsyncResumeRequest(execution_id=child["id"], prompt="Continue"))
    assert successor.child_definition_id == spawned.child_definition_id
    continued = await runs_kit.get_run(service, successor.execution_id)
    assert continued["revision_selection"] == "snapshot" and continued["agent_revision_id"] is None
    forked = await service.client.post(
        f"{service.api}/runs/{child['id']}/fork",
        json=runs_kit.message(parent, "Fork task"),
        headers=runs_kit.fresh_key(),
    )
    assert forked.status_code == 201, forked.text
    assert forked.json()["run"]["revision_selection"] == "snapshot"
    async with short_session(service.runtime.storage) as session:
        fork = await session.get_one(ThreadRow, forked.json()["thread"]["id"])
        assert fork.agent_snapshot == snapshot
    leases = await claim(service.runtime, worker_id="fork", worker_build="test", limit=2)
    fork_lease = next(item for item in leases if item.run_id == forked.json()["run"]["id"])
    with pytest.raises(ToolFailed):
        await ChildRuns(service.runtime, fork_lease, AttemptControl(), {}).spawn(request, "nested-fork")
    rejected = await runs_kit.submit(
        service,
        child["thread_id"],
        runs_kit.message(parent, "Replace", agent_revision_id=parent["default_revision_id"]),
    )
    assert rejected.status_code == 400, rejected.text


@pytest.mark.anyio
async def test_spawn_tool_client_tool_wait_resume_and_leaf_boundary(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    parent = await runs_kit.create_agent(
        service,
        scripted_model,
        subagents={"dynamic": {"enabled": True}},
        client_tools=[
            {
                "name": "review_invoice",
                "description": "Human invoice review",
                "parameters_json_schema": {"type": "object"},
            }
        ],
    )
    scripted_model.call(
        "spawn_subagent",
        {"name": "review", "instructions": "Review the invoice.", "prompt": "Invoice 42"},
        call_id="spawn",
    )
    scripted_model.say("Review started")
    root = await runs_kit.start_thread(service, parent, "Parent task")
    await (await runs_kit.attempt(service))
    async with short_session(service.runtime.storage) as session:
        from sqlalchemy import select

        thread = (await session.scalars(select(ThreadRow).where(ThreadRow.origin_run_id == root["run"]["id"]))).one()
        child_id = thread.current_run_id
    scripted_model.call("review_invoice", {}, call_id="review")
    await (await runs_kit.attempt(service))
    waiting = await runs_kit.get_run(service, child_id)
    assert waiting["status"] == "waiting", waiting
    import json

    assert json.loads(waiting["input"]["content"][0]["text"])["delegated_task"] == "Invoice 42"
    assert "parent_history" not in waiting["input"]["content"][0]["text"]
    requests = [await scripted_model.request() for _ in range(scripted_model.requests.qsize())]
    child_request = requests[-1]
    names = {item["function"]["name"] for item in child_request["tools"]}
    assert "review_invoice" in names and not {"spawn_subagent", "delegate", "resume_subagent"} & names
    payload = {"approvals": {}, "calls": {"review": {"status": "returned", "value": {"approved": True}}}}
    archived = await service.client.post(
        f"{service.api}/agents/{parent['id']}/archive", headers=runs_kit.if_match(parent)
    )
    assert archived.status_code == 200, archived.text
    refused = await service.client.post(
        f"{service.api}/runs/{child_id}/resume", json=payload, headers=runs_kit.fresh_key()
    )
    assert refused.json()["error"]["code"] == "disabled", refused.text
    restored = await service.client.post(
        f"{service.api}/agents/{parent['id']}/unarchive", headers=runs_kit.if_match(archived.json())
    )
    assert restored.status_code == 200, restored.text
    response = await service.client.post(
        f"{service.api}/runs/{child_id}/resume", json=payload, headers=runs_kit.fresh_key()
    )
    assert response.status_code == 201, response.text
    successor = response.json()
    assert successor["revision_selection"] == "snapshot" and successor["agent_revision_id"] is None
    scripted_model.say("Invoice approved")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, successor["id"]))["status"] == "completed"
    from .test_runs_children import _deliver

    await _deliver(service)
    entries = await runs_kit.inbox(service, root["thread"]["id"])
    assert sum(entry["kind"] == "child_result" for entry in entries) == 1


@pytest.mark.anyio
async def test_spawn_rejects_capability_expansion_and_preserves_parent_overrides(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    parent = await runs_kit.create_agent(
        service, scripted_model, subagents={"dynamic": {"enabled": True}}, instructions="Original"
    )
    await runs_kit.start_thread(
        service,
        parent,
        "Task",
        options={"overrides": {"instructions": "Frozen override", "model_settings": {"thinking": False}}},
    )
    (lease,) = await claim(service.runtime, worker_id="parent", worker_build="test", limit=1)
    operator = ChildRuns(service.runtime, lease, AttemptControl(), {})
    for values in (
        {"model": "unlisted"},
        {"selection": {"connections": ["con_unknown"]}},
        {"selection": {"toolsets": ["execution"]}},
    ):
        with pytest.raises(ToolFailed):
            await operator.spawn(
                Spawn(name="worker", instructions="Task role", prompt="Work", **values), "call-refused"
            )
    accepted = await operator.spawn(Spawn(name="worker", instructions="Task role", prompt="Work"), "call-refused")
    async with short_session(service.runtime.storage) as session:
        thread = await session.get_one(ThreadRow, accepted.thread_id)
        assert thread.agent_snapshot["config"]["instructions"] == "Frozen override\n\nTask role"
        assert thread.agent_snapshot["config"]["model_settings"]["thinking"] is False
    (child_lease,) = await claim(service.runtime, worker_id="child", worker_build="test", limit=1)
    with pytest.raises(ToolFailed):
        await ChildRuns(service.runtime, child_lease, AttemptControl(), {}).spawn(
            Spawn(name="grandchild", instructions="No", prompt="No"), "nested"
        )


@pytest.mark.anyio
async def test_model_and_thinking_defaults_and_cross_model_settings(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    key = await runs_kit.create_model(service, scripted_model)
    first = (await service.client.get(f"{service.api}/models/{key}")).json()
    other = await service.client.post(
        f"{service.api}/models",
        json={
            "provider_id": first["provider_id"],
            "key": "other",
            "name": "Other",
            "config": {"model_name": "other", "model_api": "openai.chat_completions", "settings": {"thinking": "low"}},
        },
    )
    assert other.status_code == 201, other.text
    parent = await runs_kit.add_agent(
        service,
        "parent",
        key,
        model_settings={"thinking": "high", "temperature": 0.2},
        subagents={
            "dynamic": {
                "enabled": True,
                "allowed_models": ["other"],
                "default_model": "other",
                "default_thinking": "medium",
            }
        },
    )
    await runs_kit.start_thread(service, parent, "Task")
    (lease,) = await claim(service.runtime, worker_id="parent", worker_build="test", limit=1)
    operator = ChildRuns(service.runtime, lease, AttemptControl(), {})
    for index, (arguments, expected_model, thinking) in enumerate(
        (
            ({}, "other", "medium"),
            ({"thinking": False}, "other", False),
            ({"model": key, "thinking": "low"}, key, "low"),
        )
    ):
        child = await operator.spawn(
            Spawn(name="same-label", instructions="Role", prompt="Work", **arguments), f"model-{index}"
        )
        async with short_session(service.runtime.storage) as session:
            thread = await session.get_one(ThreadRow, child.thread_id)
            config = thread.agent_snapshot["config"]
        assert config["model"] == expected_model and config["model_settings"]["thinking"] == thinking
        assert ("temperature" in config["model_settings"]) == (expected_model == key)
    with pytest.raises(ToolFailed):
        await operator.spawn(
            Spawn(name="bad", instructions="Role", prompt="Work", thinking="invented-effort"), "bad-thinking"
        )
