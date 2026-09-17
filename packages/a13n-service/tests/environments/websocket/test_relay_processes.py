from __future__ import annotations

import sys
from pathlib import Path

import pytest
from a13n_environment import DirectLocalEnvironmentProvider, DirectLocalProviderRuntime
from a13n_environment.commands import ArgvCommand, CommandRequest, PortTarget
from a13n_environment.models import EnvironmentAction, EnvironmentError
from a13n_environment.retention import EnvironmentOutputPolicy
from a13n_service.environments.websocket.relay_commands import CommandRelayDispatch
from a13n_service.environments.websocket.relay_processes import (
    RelayOutputOperations,
    RelayPortOperations,
    RelayProcessOperations,
    RelayShellOperations,
)
from a13n_service.environments.websocket.relay_protocol import RelayRequest
from a13n_service.environments.websocket.relay_values import BinaryValue, ProcessHandle
from a13n_service.ids import new_object_id

from .conftest import USE

pytestmark = pytest.mark.anyio
POLICY = EnvironmentOutputPolicy(max_inline_bytes=32, max_output_bytes=4096, overflow="retain")
EXECUTABLE = str(Path(sys.executable).resolve())


@pytest.fixture
async def operations(tmp_path):
    provider = DirectLocalEnvironmentProvider()
    configuration = provider.validate_configuration(
        schema_version="1",
        value={
            "root": {"path": str(tmp_path)},
            "allowed_executables": [EXECUTABLE],
            "inherit_environment": False,
            "allowed_environment_keys": [],
            "terminate_grace_seconds": 0.1,
            "max_wall_time_seconds": 10,
        },
    )
    environment = provider.create_environment(
        configuration=configuration, environment_id="process-relay", state=None, runtime=DirectLocalProviderRuntime()
    )
    await environment.enter(thread_id="thread", run_id="run", agent_instance_id="agent", mount_id="mount")
    await environment.prepare()
    try:
        yield environment.operations
    finally:
        await environment.close()


def command(script, **kwargs):
    return CommandRequest(
        command=ArgvCommand(executable=EXECUTABLE, arguments=("-c", script)), output_policy=POLICY, **kwargs
    )


async def test_binary_shell_output_and_stdin_remain_exact(control_relay, operations):
    dispatch = CommandRelayDispatch(operations, frozenset(EnvironmentAction))
    async with control_relay(dispatch) as (client, _, _, _):
        shell = RelayShellOperations(client)
        payload = b"\x00\xff\x80\r\n"
        result = await shell.exec(
            command(
                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.buffer.write(bytes([255]))",
                initial_stdin=payload,
            )
        )
        assert result.status.exit_code == 0
        assert result.output.stdout.inline == payload
        assert result.output.stderr.inline == b"\xff"
        assert result.receipt.stage == "completed"


async def test_process_start_stdin_retained_output_and_release(control_relay, operations):
    dispatch = CommandRelayDispatch(operations, frozenset(EnvironmentAction))
    async with control_relay(dispatch) as (client, _, _, _):
        processes = RelayProcessOperations(client)
        outputs = RelayOutputOperations(client)
        started = await processes.start(
            command("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())", keep_stdin_open=True)
        )
        handle = started.process.handle
        assert handle.mount_id == "mount"
        assert handle.identity.environment_id == "process-relay"
        payload = bytes(range(256))
        written = await processes.write_stdin(handle, payload, close_after_write=True)
        assert written.accepted_bytes == len(payload)
        terminal = await processes.wait(handle, condition="tree_cleaned", timeout_seconds=2)
        assert terminal.status.exit_code == 0
        assert terminal.output.stdout.reference is not None
        reference = terminal.output.stdout.reference
        page = await outputs.read(reference, policy=POLICY)
        assert b"".join(segment.data for segment in page.chunks) == payload[:32]
        assert page.next_cursor is not None
        next_page = await outputs.read(reference, cursor=page.next_cursor, policy=POLICY)
        assert b"".join(segment.data for segment in next_page.chunks) == payload[32:64]
        process_output = await processes.read_output(handle, stdout_start_offset=64, policy=POLICY)
        assert b"".join(segment.data for segment in process_output.stdout.chunks) == payload[64:96]
        await outputs.release(reference=reference)
        await processes.release(handle)
        with pytest.raises(EnvironmentError):
            await outputs.read(reference, policy=POLICY)


async def test_process_kill_and_native_handle_cannot_cross_scope(control_relay, operations):
    dispatch = CommandRelayDispatch(operations, frozenset(EnvironmentAction))
    async with control_relay(dispatch) as (client, _, _, _):
        processes = RelayProcessOperations(client)
        started = await processes.start(command("import time; time.sleep(5)"))
        handle = started.process.handle
        encoded = ProcessHandle.from_value(handle)
        assert encoded.token not in repr(encoded)
        with pytest.raises(EnvironmentError):
            await processes.inspect(handle.model_copy(update={"mount_id": "other"}))
        assert (await processes.inspect(handle)).status.phase == "running"
        result = await processes.kill(handle)
        assert result.process.status.phase in {"cancelled", "signaled", "exited"}
        await processes.release(handle)


async def test_absent_port_facet_is_a_known_predispatch_failure(control_relay, operations):
    dispatch = CommandRelayDispatch(operations, frozenset(EnvironmentAction))
    async with control_relay(dispatch) as (client, _, _, _):
        ports = RelayPortOperations(client)
        target = PortTarget(port=1)
        with pytest.raises(EnvironmentError) as error:
            await ports.inspect(target)
        assert error.value.code == "environment_unsupported"


@pytest.mark.parametrize("encoded", ["/w", "/x==", "!invalid!", "YQ==\n"])
def test_binary_codec_rejects_noncanonical_payloads(encoded):
    with pytest.raises(ValueError):
        BinaryValue(base64=encoded)


def test_command_authorization_precedes_process_execution(operations):
    dispatch = CommandRelayDispatch(operations, frozenset({EnvironmentAction.PROCESS_INSPECT}))
    with pytest.raises(EnvironmentError) as error:
        dispatch.prepare(
            RelayRequest(
                request_id=new_object_id("erq"),
                use=USE,
                deadline_ms=1,
                operation="process.kill",
                payload={
                    "handle": {
                        "mount_id": "m",
                        "token": "p",
                        "observed_generation": "1",
                        "identity": {"provider_type": "p", "environment_id": "e", "generation": "1", "process_id": "p"},
                    }
                },
            )
        )
    assert error.value.code == "environment_forbidden"
