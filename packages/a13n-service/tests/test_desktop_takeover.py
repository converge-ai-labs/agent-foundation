"""Computer-only takeover crosses the real Run, HTTP and checkpoint paths."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.models import EnvironmentAvailability, EnvironmentPermissionSet
from a13n_harness.environment.operations import EnvironmentOperations
from a13n_service.infra.db import transaction
from a13n_service.infra.http import etag
from a13n_service.runs.environments import execution
from a13n_service.runs.environments.desktop import control
from a13n_service.runs.environments.desktop.tables import DesktopBlockedRow
from sqlalchemy import select

from . import environments_support as support
from .test_desktop_control import begin
from .test_desktop_control import desktop as desktop

pytestmark = pytest.mark.anyio


@pytest.fixture
async def connected(desktop, monkeypatch):
    permissions = EnvironmentPermissionSet(operations={"environment.computer.observe", "environment.computer.click"})
    monkeypatch.setattr(
        support,
        "DESCRIPTOR",
        support.DESCRIPTOR.model_copy(
            update={"operation_families": frozenset({"computer"}), "permissions": permissions}
        ),
    )
    monkeypatch.setattr(
        support.FakeExecution,
        "availability",
        property(lambda self: EnvironmentAvailability(status="available", ready_families=frozenset({"computer"}))),
    )

    computer = AsyncMock()
    monkeypatch.setattr(
        support.FakeExecution, "operations", property(lambda self: EnvironmentOperations(computer=computer))
    )
    monkeypatch.setattr(support.FakeConnector, "provider_key", property(lambda self: "e2b"))
    monkeypatch.setattr(support.FakeExecution, "provider_key", property(lambda self: "e2b"))

    async def connector(runtime, target, **kwargs):
        support.BACKEND.instances[target.environment_id] = "running"
        return support.FakeConnector(target.environment_id, target.state)

    monkeypatch.setattr(execution, "execution_connector", connector)
    return desktop


async def test_blocked_computer_does_not_pause_model_or_other_tools(service, connected, scripted_model, runs_kit):
    first, environment_id, actor, *_ = connected
    gate = asyncio.Event()
    scripted_model.call("computer_observe", {}, call_id="blocked", gate=gate)
    scripted_model.call("tool_execution_info", {}, call_id="unrelated")
    scripted_model.say("Other work completed while the human controls the desktop.")
    running = await runs_kit.attempt(service)
    try:
        request = await asyncio.wait_for(scripted_model.request(), 10)
        assert "request_desktop_assistance" not in str(request)
        human = await begin(service, connected)
        assert human.control == "human"
        gate.set()
        await asyncio.wait_for(running, 15)
        run = await runs_kit.get_run(service, first["run"]["id"])
        assert run["status"] == "completed" and run["pending"] is None, run.get("failure")
        items = await runs_kit.items(service, run["id"])
        results = {
            item["content"]["toolCallId"]: item["content"]["result"]
            for item in items["items"]
            if item["kind"] == "tool_call"
        }
        assert "human control" in results["blocked"], results
        assert "unrelated" in results
        async with transaction(service.runtime.storage) as session:
            registrations = list(await session.scalars(select(DesktopBlockedRow)))
            assert len(registrations) == 1 and registrations[0].run_id == run["id"], items
        current = await control.get(service.runtime, actor, service.tenant.workspace_id, environment_id)
        assert current.control == "human"
        await control.handback(
            service.runtime,
            actor,
            service.tenant.workspace_id,
            environment_id,
            if_match=etag(current.id, current.version),
        )
    finally:
        gate.set()
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


async def test_takeover_without_computer_use_does_not_register_a_recipient(
    service, connected, scripted_model, runs_kit
):
    first, environment_id, actor, *_ = connected
    human = await begin(service, connected)
    scripted_model.say("Please help with the desktop. I have finished other work.")
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, first["run"]["id"])
    assert run["status"] == "completed", run.get("failure")
    async with transaction(service.runtime.storage) as session:
        assert not list(await session.scalars(select(DesktopBlockedRow)))
    returned = await control.handback(
        service.runtime,
        actor,
        service.tenant.workspace_id,
        environment_id,
        if_match=etag(human.id, human.version),
    )
    assert returned.control == "automation"
    assert not [
        entry
        for entry in await runs_kit.inbox(service, first["run"]["thread_id"])
        if entry["kind"] == "desktop_handback"
    ]


async def accepted_handback(service, connected, scripted_model, runs_kit):
    from a13n_service.runs.attempts import AttemptControl
    from a13n_service.runs.claim import claim
    from a13n_service.runs.environments.desktop.delivery import handbacks
    from a13n_service.runs.execute import execute

    from .test_desktop_control import returned_notice

    origin, _, notice = await returned_notice(service, connected)
    scripted_model.say("Other work finished")
    await execute(service.runtime, origin, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    assert (await runs_kit.get_run(service, origin.run_id))["status"] == "completed"
    await scripted_model.request()
    await handbacks(service.runtime)(notice)
    (lease,) = await claim(service.runtime, worker_id="notice-worker", worker_build="test", limit=1)
    return lease


@pytest.mark.parametrize("retake_at", ["before_read", "before_commit"])
async def test_obsolete_handback_is_discarded_across_checkpoint_recovery(
    service, connected, scripted_model, runs_kit, monkeypatch, retake_at
):
    from a13n_service.runs import checkpoints
    from a13n_service.runs.attempts import AttemptControl
    from a13n_service.runs.claim import claim
    from a13n_service.runs.execute import execute
    from a13n_service.runs.tables import InboxEntryRow, RunRow

    lease = await accepted_handback(service, connected, scripted_model, runs_kit)
    if retake_at == "before_read":
        await begin(service, connected)
    else:
        publish = checkpoints.publish_checkpoint
        retaken = False

        async def retake_before_commit(*args, **kwargs):
            nonlocal retaken
            result = await publish(*args, **kwargs)
            if not retaken:
                retaken = True
                await begin(service, connected)
            return result

        monkeypatch.setattr(checkpoints, "publish_checkpoint", retake_before_commit)
    await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    run = await runs_kit.get_run(service, lease.run_id)
    assert run["status"] == "accepted", run.get("failure")
    async with transaction(service.runtime.storage) as session:
        registration = (await session.scalars(select(DesktopBlockedRow))).one()
        entry = await session.get_one(InboxEntryRow, registration.entry_id)
        assert entry.status == "failed" and entry.failure["code"] == "desktop_notice_stale"
    (recovered,) = await claim(service.runtime, worker_id="replacement", worker_build="test", limit=1)
    assert recovered.run_id == lease.run_id
    await execute(service.runtime, recovered, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    run = await runs_kit.get_run(service, lease.run_id)
    assert run["status"] == "completed", run.get("failure")
    assert run["usage_at_seal"]["requests"] == 0
    assert (await runs_kit.get_thread(service, lease.thread_id))["current_run_id"] is None
    async with transaction(service.runtime.storage) as session:
        pointer = checkpoints.require_compatible(await session.get_one(RunRow, lease.run_id))
    state = await checkpoints.load_state(service.runtime.objects, pointer)
    assert "Human control ended for desktop" not in str(state.harness.message_history)


async def test_committed_handback_is_historical_and_retake_still_blocks_computer(
    service, connected, scripted_model, runs_kit
):
    from a13n_service.runs.attempts import AttemptControl
    from a13n_service.runs.execute import execute
    from a13n_service.runs.tables import InboxEntryRow

    lease = await accepted_handback(service, connected, scripted_model, runs_kit)
    gate = asyncio.Event()
    scripted_model.call("computer_observe", {}, call_id="retaken", gate=gate)
    scripted_model.say("The human has control again; unrelated work is done.")
    running = asyncio.create_task(
        execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    )
    try:
        request = await asyncio.wait_for(scripted_model.request(), 10)
        assert "Human control ended for desktop on mount" in str(request["messages"])
        async with transaction(service.runtime.storage) as session:
            registration = (await session.scalars(select(DesktopBlockedRow))).one()
            assert (await session.get_one(InboxEntryRow, registration.entry_id)).status == "consumed"
        await begin(service, connected)
        gate.set()
        await asyncio.wait_for(running, 15)
        run = await runs_kit.get_run(service, lease.run_id)
        assert run["status"] == "completed", run.get("failure")
        items = await runs_kit.items(service, lease.run_id)
        result = next(
            item["content"]["result"]
            for item in items["items"]
            if item["kind"] == "tool_call" and item["content"]["toolCallId"] == "retaken"
        )
        assert "human control" in result
        async with transaction(service.runtime.storage) as session:
            assert len(list(await session.scalars(select(DesktopBlockedRow)))) == 2
    finally:
        gate.set()
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


async def test_human_control_allows_real_files_shell_and_new_codeact_scripts(
    service, connected, scripted_model, runs_kit, monkeypatch, tmp_path
):
    from dataclasses import replace
    from pathlib import Path

    from a13n_harness.capabilities import CodeActCapability
    from a13n_harness.providers.catalog import ProviderCatalog
    from a13n_harness.providers.environment.direct_local.configuration import (
        DirectLocalEnvironmentConfiguration,
        DirectLocalRootConfiguration,
        DirectLocalShellProfile,
    )
    from a13n_harness.providers.environment.direct_local.execution import DirectLocalExecution
    from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
    from a13n_service.runs.host import Host
    from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering

    class ProgramTools(AbstractCapability):
        def get_ordering(self):
            return CapabilityOrdering(wrapped_by=(CodeActCapability,))

        def get_wrapper_toolset(self, toolset):
            return CodeActPolicyToolset(
                wrapped=toolset,
                policy=CodeActToolPolicy(
                    tools={name: True for name in ("view", "shell_exec", "shell_info", "shell_input", "shell_wait")}
                ),
            )

    capabilities = Host.capabilities

    def with_codeact(self, agent, *, retain_tools=False):
        return [*capabilities(self, agent, retain_tools=retain_tools), ProgramTools(), CodeActCapability()]

    monkeypatch.setattr(Host, "capabilities", with_codeact)
    # The real local facets represent this fixture's managed e2b target, including
    # the provider identity carried by process and output handles.
    monkeypatch.setattr(DirectLocalExecution, "provider_key", property(lambda self: "e2b"))
    local = DirectLocalExecution(
        DirectLocalEnvironmentConfiguration(
            root=DirectLocalRootConfiguration(path=tmp_path),
            shell_profiles=(DirectLocalShellProfile(profile_id="default", executable=Path("/bin/sh")),),
        ),
        environment_id=connected[1],
    )
    await local.open(execution_id="desktop-local-facets")
    try:
        computer = AsyncMock()
        descriptor = local.descriptor.model_copy(
            update={
                "operation_families": local.descriptor.operation_families | {"computer"},
                "permissions": EnvironmentPermissionSet(
                    operations=local.descriptor.permissions.operations
                    | {
                        "environment.computer.observe",
                        "environment.computer.click",
                    }
                ),
            }
        )
        monkeypatch.setattr(support, "DESCRIPTOR", descriptor)
        definition = service.runtime.registry.get("environment", "e2b")
        replacement = replace(
            definition, execution=replace(definition.execution, describe_environment=lambda recipe: descriptor)
        )
        registry = replace(
            service.runtime.registry,
            environments=ProviderCatalog(
                replacement if item.type == "e2b" else item for item in service.runtime.registry.environments.values()
            ),
        )
        service.runtime = replace(service.runtime, registry=registry)
        service.app.state.runtime = service.runtime
        monkeypatch.setattr(support.FakeExecution, "execution_id", property(lambda self: "desktop-local-facets"))
        monkeypatch.setattr(
            support.FakeExecution,
            "availability",
            property(
                lambda self: EnvironmentAvailability(
                    status="available",
                    ready_families=descriptor.operation_families,
                )
            ),
        )
        monkeypatch.setattr(
            support.FakeExecution,
            "operations",
            property(
                lambda self: replace(
                    local.operations,
                    computer=computer,
                )
            ),
        )
        first = connected[0]["run"]
        # Change the desired mount through its API, then freeze it in a normal successor.
        await service.client.post(f"{service.api}/runs/{first['id']}/interrupt")
        thread = await runs_kit.get_thread(service, first["thread_id"])
        mount = {"permission_ceiling": descriptor.permissions.model_dump(mode="json")}
        response = await service.client.patch(
            f"{service.api}/threads/{thread['id']}/environments/workspace",
            json=mount,
            headers=runs_kit.if_match(thread),
        )
        assert response.status_code == 200, response.text
        response = await runs_kit.submit(
            service,
            thread["id"],
            runs_kit.message(
                {"id": first["agent_id"]},
                "Continue with files and commands",
            ),
        )
        assert response.status_code == 201, response.text
        thread = await runs_kit.get_thread(service, thread["id"])
        run_id = thread["current_run_id"]
        await begin(service, connected)
        scripted_model.call(
            "write", {"file_path": "/workspace/proof.txt", "content": "before\n"}, call_id="write-proof"
        )
        scripted_model.call(
            "shell_exec", {"command": "printf 'after\\n' >> proof.txt", "cwd": "/workspace"}, call_id="shell-proof"
        )
        scripted_model.call(
            "run_code", {"code": "print(await view(file_path='/workspace/proof.txt'))"}, call_id="code-proof"
        )
        scripted_model.call(
            "run_code",
            {
                "code": "\n".join(
                    [
                        "started = await shell_exec(command='read line; printf \\\"%s\\\\n\\\" \\\"$line\\\"', cwd='/workspace')",
                        "print(started)",
                        "process_id = started['process_id']",
                        "print(await shell_info(process_id=process_id))",
                        "print(await shell_input(process_id=process_id, data='process-control-proof\\n', close_stdin=True))",
                        "print(await shell_wait(process_id=process_id, timeout_seconds=5))",
                    ]
                )
            },
            call_id="process-proof",
        )
        running = await runs_kit.attempt(service)
        try:
            async with asyncio.timeout(30):
                while True:
                    request = await scripted_model.request()
                    receipts = [
                        message["content"]
                        for message in request["messages"]
                        if message.get("tool_call_id") == "process-proof"
                    ]
                    if receipts:
                        break
            receipt = json.loads(receipts[0])
            if receipt.get("type") == "tool_execution_receipt":
                scripted_model.call(
                    "tool_execution_wait",
                    {"execution_id": receipt["execution_id"], "timeout_seconds": 30},
                    call_id="process-result",
                )
            scripted_model.call("computer_observe", {}, call_id="blocked")
            scripted_model.say("File and shell work finished while Computer remains blocked.")
            await asyncio.wait_for(running, 40)
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
        run = await runs_kit.get_run(service, run_id)
        items = await runs_kit.items(service, run_id)
        results = [item["content"] for item in items["items"] if item["kind"] == "tool_call"]
        assert run["status"] == "completed", (
            str(run.get("failure")) + "\n" + "\n".join(str(item.get("result")) for item in results)
        )
        assert (tmp_path / "proof.txt").exists(), "\n".join(str(item.get("result")) for item in results)
        assert (tmp_path / "proof.txt").read_text() == "before\nafter\n", "\n".join(
            str(item.get("result")) for item in results
        )
        assert any(item["toolCallName"] == "run_code" and "after" in str(item["result"]) for item in results), results
        assert any(item["toolCallId"] == "blocked" and "human control" in str(item["result"]) for item in results)
        process = [item["result"] for item in results if item["toolCallId"] in {"process-proof", "process-result"}]
        assert "process-control-proof" in str(process) and "exited" in str(process), process
        computer.observe.assert_not_awaited()
    finally:
        await local.close()


@pytest.mark.parametrize("settled", [False, True])
async def test_handback_reaches_live_child_without_resurrecting_settled_child(
    service, connected, scripted_model, runs_kit, settled, caplog
):
    from a13n_service.infra import outbox
    from a13n_service.runs.environments.desktop.delivery import handbacks
    from a13n_service.runs.tables import RunRow

    from .test_runs_children import _child_run

    first, environment_id, actor, *_ = connected
    response = await service.client.post(f"{service.api}/runs/{first['run']['id']}/interrupt")
    assert response.status_code == 200, response.text
    from .conftest import add_agent

    worker = await add_agent(
        service, "worker", "scripted", instructions="Role: worker", toolsets={"computer": {"enabled": True}}
    )
    coordinator = await add_agent(
        service,
        "coordinator",
        "scripted",
        instructions="Role: coordinator",
        subagent_mode="async",
        subagents={
            "helper": {"agent_id": worker["id"], "description": "Desktop work", "environment": {"mode": "shared"}}
        },
    )
    scripted_model.call(
        "delegate", {"subagent_name": "helper", "prompt": "Use the desktop"}, call_id="spawn", to="Role: coordinator"
    )
    scripted_model.say("Delegated", to="Role: coordinator")
    submitted = await runs_kit.start_thread(
        service,
        coordinator,
        "Delegate the desktop work",
        environments=[
            {
                "name": "workspace",
                "environment_id": environment_id,
                "permission_ceiling": {"operations": ["environment.computer.observe"]},
            }
        ],
    )
    await (await runs_kit.attempt(service))
    child = await _child_run(service, submitted["run"]["id"])
    while not scripted_model.requests.empty():
        await scripted_model.request()
    human = await begin(service, connected)
    gate = asyncio.Event()
    scripted_model.call("computer_observe", {}, call_id="child-blocked", to="Role: worker")
    scripted_model.call("tool_execution_info", {}, call_id="child-info", gate=gate, to="Role: worker")
    scripted_model.say("Other work finished", to="Role: worker")
    scripted_model.say("Handback received", to="Role: worker")
    running = await runs_kit.attempt(service)
    try:
        await asyncio.wait_for(scripted_model.request(), 10)
        blocked = await asyncio.wait_for(scripted_model.request(), 10)
        assert "human control" in str(blocked["messages"])
        if settled:
            gate.set()
            await asyncio.wait_for(running, 15)
            assert (await runs_kit.get_run(service, child.id))["status"] == "completed"
        await control.handback(
            service.runtime,
            actor,
            service.tenant.workspace_id,
            environment_id,
            if_match=etag(human.id, human.version),
        )
        (notice,) = await outbox.claim(
            service.runtime.storage, "desktop_handback", owner="child-test", limit=10, lease_seconds=60, max_attempts=12
        )
        await handbacks(service.runtime)(notice)
        entries = [
            entry for entry in await runs_kit.inbox(service, child.thread_id) if entry["kind"] == "desktop_handback"
        ]
        assert len(entries) == (0 if settled else 1)
        gate.set()
        await asyncio.wait_for(running, 15)
        finished = await runs_kit.get_run(service, child.id)
        assert finished["status"] == "completed", json.dumps(
            [record.exception_details for record in caplog.records if record.getMessage() == "Attempt failed"],
            indent=2,
        )
        if not settled:
            (entry,) = [
                entry for entry in await runs_kit.inbox(service, child.thread_id) if entry["kind"] == "desktop_handback"
            ]
            assert entry["status"] == "consumed" and entry["assigned_run_id"] == child.id
            requests = [await scripted_model.request() for _ in range(scripted_model.requests.qsize())]
            assert any("Human control ended for desktop on mount" in str(item["messages"]) for item in requests)
        assert (await runs_kit.get_thread(service, child.thread_id))["current_run_id"] is None
        async with transaction(service.runtime.storage) as session:
            assert len(list(await session.scalars(select(RunRow).where(RunRow.thread_id == child.thread_id)))) == 1
            (registration,) = list(await session.scalars(select(DesktopBlockedRow)))
            assert registration.thread_id == child.thread_id
            assert registration.suppressed is settled
    finally:
        gate.set()
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
