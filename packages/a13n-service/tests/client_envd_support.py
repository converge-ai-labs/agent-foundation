"""Checks on a connected reverse daemon, using the same run-scoped Service provider as tools."""

import asyncio
import json
import os

import pytest


async def interrupt_control_subscription(service, owner, monkeypatch):
    from a13n_service.runs.environments.clients.routing import request_channel
    from redis.asyncio.client import PubSub
    from redis.exceptions import ConnectionError

    channel = request_channel(service.runtime.client_connections.node)
    get_message = PubSub.get_message

    async def interrupted(subscription, *args, **kwargs):
        if channel in subscription.channels:
            raise ConnectionError("Subscription lost")
        return await get_message(subscription, *args, **kwargs)

    with monkeypatch.context() as changes:
        changes.setattr(PubSub, "get_message", interrupted)
        await asyncio.wait_for(owner.carrier.wait_closed(), 5)


async def check_retained_client_process(service, worker, runs_kit, model, agent, mount, workspace):
    from a13n_service.infra.db import now, transaction
    from a13n_service.runs.tables import ToolExecutionRow

    accepted = await runs_kit.start_thread(service, agent, "Start a background command", environments=mount)
    model.call("shell_exec", {"command": "echo $$ > retained.pid; read value"}, call_id="retained")
    model.say("Started.")
    await asyncio.wait_for(await runs_kit.attempt(service, runtime=worker), 20)
    assert (await runs_kit.get_run(service, accepted["run"]["id"]))["status"] == "completed"
    thread = accepted["thread"]["id"]
    (record,) = await worker.executions.summarize(thread)
    assert record.status == "running"
    pid = int((workspace / "retained.pid").read_text())
    os.kill(pid, 0)
    async with transaction(worker.storage) as session:
        row = await session.get_one(ToolExecutionRow, record.execution_id)
        row.cancel_requested_at = await now(session)
    result = await worker.executions.wait(thread, record.execution_id, timeout_seconds=10)
    assert result.record.status == "cancelled", result
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def check_client_revocation(service, worker, origin, created, identity):
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    path = f"{service.api}/client-environments/{identity}"
    socket_url = origin.replace("http://", "ws://") + created.json()["connection_path"]
    auth = {"authorization": f"Bearer {created.json()['token']}"}
    owner = service.runtime.client_connections.entries[identity]
    assert owner.carrier is not None
    with pytest.raises(InvalidStatus):
        async with connect(socket_url, subprotocols=["eip.v1"], additional_headers=auth):
            pytest.fail("Duplicate device connection replaced its live owner")
    current = await service.client.get(path)
    assert current.json()["environment"]["client_connection"]["state"] == "online"
    revoked = await service.client.post(f"{path}/revoke-credential", headers={"if-match": current.headers["etag"]})
    assert revoked.status_code == 200, revoked.text
    async with asyncio.timeout(12):
        await owner.carrier.wait_closed()
        while await worker.client_connections.presence(identity) is not None:
            await asyncio.sleep(0.02)
    with pytest.raises(InvalidStatus):
        async with connect(socket_url, subprotocols=["eip.v1"], additional_headers=auth):
            pytest.fail("Revoked credential opened another carrier")


async def check_long_client_command(execution, workspace):
    from a13n_harness.environment.commands import ArgvCommand, CommandLimits, CommandRequest
    from a13n_harness.environment.models import EnvironmentError
    from a13n_harness.environment.retention import EnvironmentOutputPolicy

    processes = execution.operations.processes
    assert processes is not None
    started = await processes.start(
        CommandRequest(
            command=ArgvCommand(
                executable="sh", arguments=("-c", "echo once >> started; while [ ! -f finish ]; do sleep 0.1; done")
            ),
            cwd=str(workspace),
            output_policy=EnvironmentOutputPolicy(max_inline_bytes=128, max_output_bytes=4096, overflow="retain"),
        )
    )
    handle = started.process.handle
    try:
        with pytest.raises(EnvironmentError) as expired:
            await processes.wait(handle, condition="tree_cleaned", timeout_seconds=0.1)
        assert expired.value.code == "environment_timeout"
        assert (await processes.inspect(handle)).status.phase == "running"
    finally:
        (workspace / "finish").touch()
    assert (await processes.wait(handle, condition="tree_cleaned", timeout_seconds=5)).status.exit_code == 0
    assert (workspace / "started").read_text().splitlines() == ["once"]
    await processes.release(handle)

    limited = await processes.start(
        CommandRequest(
            command=ArgvCommand(executable="sleep", arguments=("30",)),
            cwd=str(workspace),
            limits=CommandLimits(wall_time_seconds=0.3),
            output_policy=EnvironmentOutputPolicy(max_inline_bytes=128, max_output_bytes=4096, overflow="retain"),
        )
    )
    expired = await processes.wait(limited.process.handle, condition="tree_cleaned", timeout_seconds=5)
    assert (expired.status.phase, expired.status.termination_reason) == ("timed_out", "timeout")
    await processes.release(limited.process.handle)


def following_tool_results(request):
    messages = request["messages"]
    last_response = max(i for i, message in enumerate(messages) if message["role"] == "assistant")
    return json.dumps(messages[last_response + 1 :])


async def check_interrupted_upload(files, workspace):
    from a13n_harness.environment.models import EnvironmentError

    async def interrupted():
        yield b"partial upload"
        raise OSError("upload interrupted")

    path = workspace / "incomplete"
    with pytest.raises(EnvironmentError) as failure:
        await files.write_bytes_stream(str(path), interrupted(), mode="create")
    assert failure.value.code == "environment_provider_failure"
    assert not path.exists()


async def check_lost_start_reply(execution, workspace, worker, monkeypatch):
    from a13n_harness.environment.commands import ArgvCommand, CommandRequest
    from a13n_harness.environment.models import EnvironmentError
    from a13n_harness.environment.retention import EnvironmentOutputPolicy

    original = worker.client_routing.call
    operations = []

    async def lose_reply(node, request):
        response = await original(node, request)
        if request.method == "process.start":
            operations.append((request.method, request.params["context"]["operation_id"]))
            raise EnvironmentError("Reply lost after dispatch", code="environment_unavailable")
        if request.method == "receipt.get":
            operations.append((request.method, request.params["operation_id"]))
        return response

    with monkeypatch.context() as changes:
        changes.setattr(worker.client_routing, "call", lose_reply)
        with pytest.raises(EnvironmentError) as failed:
            await execution.operations.processes.start(
                CommandRequest(
                    command=ArgvCommand(executable="sh", arguments=("-c", "echo once >> receipt-proof")),
                    cwd=str(workspace),
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=128, max_output_bytes=4096, overflow="retain"
                    ),
                )
            )
    assert failed.value.code == "environment_unknown_outcome"
    assert failed.value.details["receipt_stage"] in {"exec_confirmed", "completed"}
    start, lookup = operations
    assert start == ("process.start", lookup[1]) and lookup[0] == "receipt.get"
    async with asyncio.timeout(3):
        while not (workspace / "receipt-proof").exists():
            await asyncio.sleep(0.02)
    assert (workspace / "receipt-proof").read_text().splitlines() == ["once"]


async def check_stale_client_request(service, worker, workspace):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from a13n_service.runs.environments.clients.routing import Failure

    (scope,) = service.runtime.client_sessions.scopes.values()
    request = scope.request.model_copy(
        update={
            "id": uuid4().hex,
            "method": "file.mkdir",
            "expires_at": datetime.now(UTC) + timedelta(seconds=5),
            "caller": scope.request.caller.model_copy(update={"worker_id": "old-owner"}),
            "params": {"context": {"operation_id": "op-stale"}, "path": {"path": str(workspace / "unauthorized")}},
        }
    )
    response = await worker.client_routing.call(service.runtime.client_connections.node, request)
    assert isinstance(response.outcome, Failure) and response.outcome.pre_dispatch
    assert response.outcome.code == "environment_denied"
    assert not (workspace / "unauthorized").exists()


async def check_wrong_device(service, origin, identity):
    from websockets.asyncio.client import connect

    registered = await service.client.post(
        f"{service.api}/client-environments",
        json={"device_id": "expected-device", "name": "Other", "default_working_directory": "/work"},
    )
    body = registered.json()
    other = body["client"]["environment"]["id"]
    descriptor = service.runtime.client_connections.entries[identity].device.descriptor
    async with connect(
        origin.replace("http://", "ws://") + body["connection_path"],
        subprotocols=["eip.v1"],
        additional_headers={"authorization": f"Bearer {body['token']}"},
    ) as socket:
        request = json.loads(await socket.recv())
        assert request["params"]["expected_device_id"] == "expected-device"
        await socket.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {
                        "protocol_version": request["params"]["supported_protocol_versions"][0],
                        "server": {"name": "a13n-envd", "version": "0.0.0"},
                        "descriptor": descriptor.model_dump(mode="json"),
                    },
                }
            )
        )
        async with asyncio.timeout(3):
            await socket.wait_closed()
    assert await service.runtime.client_connections.presence(other) is None


async def check_screen_stream(service, logical, workspace, origin, monkeypatch):
    """Supply a fixed screenshot at the native capture boundary. All transfer frames use the real daemon."""
    from a13n_envd_client.eip import v1 as eip
    from a13n_envd_client.file_transfer import EIPComputerObservationReader
    from a13n_service.runs.environments.clients.streaming import PATH
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    (scope,) = service.runtime.client_sessions.scopes.values()
    session = scope.session
    methods = (*session.descriptor.available_methods, "computer.observe", "computer.close_observation")
    content = b"immutable-test-image"
    path = workspace / "test-image"
    path.write_bytes(content)
    native = session.requester.request

    async def capture(method, params):
        if method.name == "computer.observe":
            opened = await native(
                eip.METHODS["file.open_reader"],
                eip.FileReaderOpenParams(
                    context=params.context,
                    path=eip.EIPPath(path=str(path)),
                ),
            )
            return eip.ComputerObserveResult.model_validate(
                {
                    "observation": {
                        "observation_id": "obs-test",
                        "target_id": "screen",
                        "width": 1,
                        "height": 1,
                        "mime_type": "image/jpeg",
                        "captured_at": "2026-08-21T00:00:00Z",
                    },
                    "reader": opened.reader,
                    "size_bytes": len(content),
                    "expires_at": opened.expires_at,
                }
            )
        if method.name == "computer.close_observation":
            return await native(eip.METHODS["file.close_reader"], params)
        return await native(method, params)

    with monkeypatch.context() as changes:
        changes.setattr(session, "_descriptor", session.descriptor.model_copy(update={"available_methods": methods}))
        changes.setattr(session.requester, "request", capture)
        async with EIPComputerObservationReader(logical.requester, eip.EIPClient(logical.requester)) as reader:
            handle = reader.opened.reader.root
            headers = {
                "Authorization": f"Bearer {scope.transfer_token}",
                "X-A13N-Scope": scope.request.scope_id,
                "EIP-Session": session.session_id,
                "EIP-Transfer-Handle": handle,
                "EIP-Transfer-Direction": "read",
            }
            for key, value in (
                ("Authorization", "Bearer incorrect"),
                ("X-A13N-Scope", "other"),
                ("EIP-Session", "other"),
                ("EIP-Transfer-Handle", "other"),
                ("EIP-Transfer-Direction", "write"),
            ):
                with pytest.raises(InvalidStatus):
                    async with connect(
                        origin.replace("http://", "ws://") + PATH,
                        subprotocols=["eip.v1"],
                        additional_headers=headers | {key: value},
                    ):
                        pytest.fail("Invalid selector attached to a screenshot")
            assert b"".join([chunk async for chunk in reader]) == content
            assert reader.opened.observation.observation_id == "obs-test"
        assert handle not in scope.transfers
