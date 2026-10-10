"""Adapter failures remain Environment errors before native transfer dispatch."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from a13n_envd_client.errors import EIPSessionStateError
from a13n_environment.eip.files import EIPFileOperator
from a13n_environment.models import EnvironmentError

pytestmark = pytest.mark.anyio


def operator(*, methods=(), failure=None):
    session = SimpleNamespace(
        descriptor=SimpleNamespace(
            mounts=[SimpleNamespace(execution_id="workspace", logical_root="/")],
            available_methods=methods,
        ),
        open_reader=Mock(side_effect=failure),
        open_writer=Mock(side_effect=failure),
    )
    return EIPFileOperator(session=session, environment_id="env", execution_id="workspace", generation="1"), session


@pytest.mark.parametrize("path", ["/../outside", "/a/../../outside", "/a//b", "/a/./b", "/a/"])
def test_invalid_wire_paths_become_environment_errors(path):
    files, _ = operator()
    with pytest.raises(EnvironmentError) as error:
        files.to_eip_path(path)
    assert error.value.code == "environment_request_invalid"


async def forbidden_upload():
    raise AssertionError("Rejected transfer consumed input")
    yield b"never"


async def invoke_transfer(files, action):
    if action == "read":
        return await files.read_bytes("/file")
    if action == "read-stream":
        return [chunk async for chunk in files.read_bytes_stream("/file")]
    return await files.write_bytes_stream("/file", forbidden_upload(), mode="create")


@pytest.mark.parametrize("action", ["read", "read-stream", "write"])
async def test_unadvertised_transfers_fail_before_native_dispatch(action):
    files, session = operator()
    with pytest.raises(EnvironmentError) as error:
        await invoke_transfer(files, action)
    assert error.value.code == "environment_unsupported"
    session.open_reader.assert_not_called()
    session.open_writer.assert_not_called()


@pytest.mark.parametrize("action", ["read", "read-stream", "write"])
async def test_closed_session_transfer_setup_is_converted(action):
    files, _ = operator(methods=("file.open_reader", "file.open_writer"), failure=EIPSessionStateError("closed"))
    with pytest.raises(EnvironmentError) as error:
        await invoke_transfer(files, action)
    assert error.value.code == "environment_unavailable"


@pytest.mark.parametrize("action", ["read", "read-stream", "write"])
async def test_cancelled_transfer_setup_preserves_cancellation(action):
    files, _ = operator(methods=("file.open_reader", "file.open_writer"), failure=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await invoke_transfer(files, action)


@pytest.mark.parametrize("action", ["read", "stat", "write"])
async def test_closed_session_control_setup_is_converted(action):
    class ClosedSession:
        descriptor = SimpleNamespace(mounts=[SimpleNamespace(execution_id="workspace", logical_root="/")])

        @property
        def client(self):
            raise EIPSessionStateError("closed")

    files = EIPFileOperator(session=ClosedSession(), environment_id="env", execution_id="workspace", generation="1")
    with pytest.raises(EnvironmentError) as error:
        if action == "read":
            await files.read_text("/file")
        elif action == "stat":
            await files.stat("/file")
        else:
            await files.write_text("/file", "replacement", mode="replace")
    assert error.value.code == "environment_unavailable"
