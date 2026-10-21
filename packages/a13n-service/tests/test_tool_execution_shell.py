"""Real Shell controls cross worker runtimes through Redis after the original Service Run completes."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.toolsets.shell_control import ShellControl
from a13n_service.infra.db import transaction
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from a13n_service.runs.tables import ToolExecutionRow
from a13n_service.runs.tool_executions.routing import Caller, Request
from a13n_service.settings import LocalProvisioning, Provisioning

pytestmark = pytest.mark.anyio


async def test_successor_worker_controls_original_shell(
    serve, settings, scripted_model, runs_kit, tmp_path, monkeypatch
):
    settings = settings.model_copy(
        update={
            "provisioning": Provisioning(local=LocalProvisioning(enabled=True, root=tmp_path / "environments")),
        }
    )
    async with serve(settings=settings) as service:
        await runs_kit.pause_sweeps(service)
        provider = await service.client.post(
            f"{service.api}/environment-providers",
            json={"type": "local", "name": "Local"},
        )
        assert provider.status_code == 201, provider.text
        template = await service.client.post(
            f"{service.api}/environment-templates",
            json={
                "name": "Shell",
                "provider_id": provider.json()["id"],
                "config": {
                    "recipe": {
                        "root": {"path": str(tmp_path / "shell")},
                        "shell_profiles": [{"profile_id": "default", "executable": "/bin/sh"}],
                    }
                },
            },
        )
        assert template.status_code == 201, template.text
        agent = await runs_kit.create_agent(
            service,
            scripted_model,
            default_environment_template_id=template.json()["id"],
        )
        await runs_kit.start_thread(service, agent, "start shell")
        (origin,) = await claim(service.runtime, worker_id="worker-a", worker_build="test", limit=1)
        scripted_model.call(
            "shell_exec", {"command": 'printf ready; read line; printf "done:%s" "$line"'}, call_id="shell"
        )
        scripted_model.say("Shell continues independently")
        owner = service.runtime.executions
        route = asyncio.create_task(owner.routing.run("worker-a"))
        successor_runtime = replace(service.runtime)
        cleanup_started, cleanup_allowed = asyncio.Event(), asyncio.Event()
        try:
            await asyncio.wait_for(owner.routing.ready.wait(), 5)
            await asyncio.wait_for(execute(service.runtime, origin, AttemptControl()), 20)
            assert (await runs_kit.get_run(service, origin.run_id))["status"] == "completed"
            records = await owner.summarize(origin.thread_id)
            assert len(records) == 1, [
                message
                for request in [scripted_model.requests.get_nowait() for _ in range(scripted_model.requests.qsize())]
                for message in request["messages"]
                if message.get("role") == "tool"
            ]
            record = records[0]
            assert record.status == "running", json.dumps(
                (await owner.result(origin.thread_id, record.execution_id)).result, indent=2
            )
            async with asyncio.timeout(5):
                while owner.resource(origin.thread_id, record.execution_id) is None:
                    await asyncio.sleep(0.01)
            resource = owner.resource(origin.thread_id, record.execution_id)
            native_handle = resource.controller._entry(resource.process_id).handle
            assert successor_runtime.executions.resource(origin.thread_id, record.execution_id) is None
            environment_id = (await runs_kit.get_run(service, origin.run_id))["environment_mounts"][0]["environment_id"]
            environment_url = f"{service.api}/environments/{environment_id}"

            async def stop_environment():
                current = await service.client.get(environment_url)
                return await service.client.post(
                    f"{environment_url}/stop", headers={"if-match": current.headers["etag"]}
                )

            refused = await stop_environment()
            assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "in_use", refused.text
            original_close = resource.close

            async def delayed_close():
                cleanup_started.set()
                await cleanup_allowed.wait()
                await original_close()

            monkeypatch.setattr(resource, "close", delayed_close)
            # A legitimate new Run freezes reduced permissions. The receiver must use that snapshot,
            # not the original Run's permission ceiling or a later edit to the Thread.
            mounts_url = f"{service.api}/threads/{origin.thread_id}/environments"
            listing = await service.client.get(mounts_url)
            original_permissions = listing.json()["items"][0]["permission_ceiling"]
            reduced = await service.client.patch(
                f"{mounts_url}/workspace",
                json={"permission_ceiling": {"operations": ["environment.process.inspect"]}},
                headers={"if-match": listing.headers["etag"]},
            )
            assert reduced.status_code == 200, reduced.text
            submitted = await runs_kit.submit(service, origin.thread_id, runs_kit.message(agent, "inspect only"))
            assert submitted.status_code == 201, submitted.text
            (restricted,) = await claim(successor_runtime, worker_id="worker-b", worker_build="test", limit=1)
            denied_input = Request(
                thread_id=origin.thread_id,
                execution_id=record.execution_id,
                caller=Caller(
                    run_id=restricted.run_id, attempt_id=restricted.attempt_id, worker_id=restricted.worker_id
                ),
                control=ShellControl(operation="input", data="must not arrive\n"),
                expires_at=datetime.now(UTC) + timedelta(seconds=10),
            )
            with pytest.raises(EnvironmentError) as denied:
                await successor_runtime.executions.routing.call("worker-a", denied_input)
            assert denied.value.details["dispatch_stage"] == "pre_dispatch"
            scripted_model.say("Read-only Run")
            await execute(successor_runtime, restricted, AttemptControl())
            listing = await service.client.get(mounts_url)
            restored = await service.client.patch(
                f"{mounts_url}/workspace",
                json={"permission_ceiling": original_permissions},
                headers={"if-match": listing.headers["etag"]},
            )
            assert restored.status_code == 200, restored.text
            submitted = await runs_kit.submit(service, origin.thread_id, runs_kit.message(agent, "control it"))
            assert submitted.status_code == 201, submitted.text
            (successor,) = await claim(successor_runtime, worker_id="worker-b", worker_build="test", limit=1)
            control = Request(
                thread_id=origin.thread_id,
                execution_id=record.execution_id,
                caller=Caller(run_id=successor.run_id, attempt_id=successor.attempt_id, worker_id=successor.worker_id),
                control=ShellControl(operation="input", data="rejected\n"),
                expires_at=datetime.now(UTC) + timedelta(seconds=10),
            )
            # Receiver-side fencing rejects a stale caller and a cancelled owner before native input.
            stale = control.model_copy(update={"caller": control.caller.model_copy(update={"worker_id": "old-worker"})})
            with pytest.raises(EnvironmentError) as denied:
                await successor_runtime.executions.routing.call("worker-a", stale)
            assert denied.value.details["dispatch_stage"] == "pre_dispatch"
            assert (
                await successor_runtime.executions.routing.call(
                    "worker-a", control.model_copy(update={"thread_id": "other-thread"})
                )
                is None
            )
            async with transaction(service.runtime.storage) as session:
                execution = await session.get(ToolExecutionRow, record.execution_id)
                execution.cancel_requested_at = datetime.now(UTC)
            with pytest.raises(EnvironmentError) as denied:
                await successor_runtime.executions.routing.call("worker-a", control)
            assert denied.value.details["dispatch_stage"] == "pre_dispatch"
            async with transaction(service.runtime.storage) as session:
                execution = await session.get(ToolExecutionRow, record.execution_id)
                execution.cancel_requested_at = None
            assert (await owner.result(origin.thread_id, record.execution_id)).record.status == "running"
            scripted_model.call("shell_info", {"process_id": record.execution_id}, call_id="inspect")
            scripted_model.call("shell_wait", {"process_id": record.execution_id, "timeout_seconds": 0}, call_id="poll")
            scripted_model.call(
                "shell_input", {"process_id": record.execution_id, "data": "accepted\n"}, call_id="input"
            )
            scripted_model.say("Controlled original shell")
            await asyncio.wait_for(execute(successor_runtime, successor, AttemptControl()), 20)
            assert (await runs_kit.get_run(service, successor.run_id))["status"] == "completed"
            await asyncio.wait_for(cleanup_started.wait(), 5)
            refused = await stop_environment()
            assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "in_use", refused.text
            cleanup_allowed.set()
            async with asyncio.timeout(5):
                await owner.join()
            assert (await stop_environment()).status_code == 202
            result = await successor_runtime.executions.result(origin.thread_id, record.execution_id)
            assert result.record.status == "succeeded", result
            assert result.result["stdout"]["text"] == "readydone:accepted"
            assert resource.identity == native_handle.identity
            assert resource._closed
            returns = {
                message["tool_call_id"]: json.loads(message["content"])
                for request in [scripted_model.requests.get_nowait() for _ in range(scripted_model.requests.qsize())]
                for message in request["messages"]
                if message.get("role") == "tool" and message.get("tool_call_id") in {"inspect", "poll", "input"}
            }
            assert returns["inspect"]["status"]["phase"] == "running", returns
            assert returns["poll"]["stdout"]["text"] == "ready", returns
            assert returns["input"]["accepted_bytes"] == len("accepted\n"), returns
        finally:
            cleanup_allowed.set()
            await owner.close()
            route.cancel()
            await asyncio.gather(route, return_exceptions=True)
            await successor_runtime.executions.close()
            await successor_runtime.executions.routing.client.aclose()


async def test_routing_lost_reply_does_not_repeat_mutation(serve, runs_kit, monkeypatch):
    async with serve() as service:
        await runs_kit.pause_sweeps(service)
        owner = service.runtime.executions
        caller = replace(service.runtime).executions
        calls = []
        dispatched = asyncio.Event()

        async def control(request):
            calls.append(request.id)
            dispatched.set()
            # A mutation has happened, but the owner cannot confirm it before expiry.
            await asyncio.Event().wait()

        monkeypatch.setattr(owner, "local_control", control)
        route = asyncio.create_task(owner.routing.run("worker-a"))
        try:
            await asyncio.wait_for(owner.routing.ready.wait(), 5)
            request = Request(
                thread_id="thread-test",
                execution_id="exec-test",
                caller=Caller(run_id="run-test", attempt_id="attempt-test", worker_id="worker-b"),
                control=ShellControl(operation="input", data="once"),
                expires_at=datetime.now(UTC) + timedelta(seconds=0.3),
            )
            with pytest.raises(EnvironmentError) as unknown:
                await caller.routing.call("worker-a", request)
            assert dispatched.is_set()
            assert calls == [request.id]
            assert unknown.value.details.get("dispatch_stage") != "pre_dispatch"
            absent = request.model_copy(update={"expires_at": datetime.now(UTC) + timedelta(seconds=2)})
            assert await caller.routing.call("absent-worker", absent) is None
            assert calls == [request.id]
        finally:
            route.cancel()
            await asyncio.gather(route, return_exceptions=True)
            await caller.routing.client.aclose()
