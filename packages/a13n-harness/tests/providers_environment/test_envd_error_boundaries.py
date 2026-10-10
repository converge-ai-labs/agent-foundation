"""Provider boundaries preserve public errors and cancellation, not client errors."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from a13n_envd_client import EIPSessionStateError
from a13n_environment.errors import EnvironmentProviderError
from a13n_environment.local_envd.configuration import LocalEnvdEnvironmentConfiguration
from a13n_environment.local_envd.provider import LocalEnvdExecution
from a13n_environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_environment.models import EnvironmentError, EnvironmentState
from a13n_environment.remote_envd import environment as remote_module
from a13n_environment.remote_envd.environment import RemoteEnvdExecution

pytestmark = pytest.mark.anyio


@pytest.fixture(params=["local", "remote"])
def adapter(request, tmp_path):
    if request.param == "local":
        return LocalEnvdExecution(
            LocalEnvdEnvironmentConfiguration(),
            LocalEnvdProviderRuntime(
                executable=tmp_path / "unused",
                allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
            ),
            environment_id="test",
        )

    @asynccontextmanager
    async def session():
        yield SimpleNamespace()

    return RemoteEnvdExecution(
        provider_key="http_envd",
        environment_id="test",
        state=EnvironmentState(provider_key="http_envd", state_version="1", state={"device_id": "device"}),
        session_context=session(),
    )


def install_scope(adapter, scope):
    if isinstance(adapter, LocalEnvdExecution):
        adapter._eip_scope = scope
    else:
        adapter._session_context = scope
        adapter._entered_session = True


@pytest.mark.parametrize("kind", ["client", "public", "cancel"])
async def test_close_normalizes_client_failure_and_preserves_public_or_cancel(adapter, kind):
    error = {
        "client": EIPSessionStateError("private carrier detail"),
        "public": EnvironmentError("public failure", code="environment_unavailable"),
        "cancel": asyncio.CancelledError(),
    }[kind]

    @asynccontextmanager
    async def failing_close():
        yield
        raise error

    scope = failing_close()
    await scope.__aenter__()
    install_scope(adapter, scope)
    with pytest.raises(EnvironmentProviderError if kind == "client" else type(error)) as caught:
        await adapter.close()
    if kind == "client":
        assert caught.value.code == "provider_session_close_failed"
        assert caught.value.__cause__ is error
        assert "private" not in str(caught.value)
    else:
        assert caught.value is error
    assert adapter.availability.status == "unavailable"
    await adapter.close()


@pytest.mark.parametrize("cancel", [False, True])
async def test_prepare_failure_stays_primary_when_cleanup_also_fails(adapter, monkeypatch, cancel):
    original = asyncio.CancelledError() if cancel else RuntimeError("preparation failure")
    cleanup = OSError("cleanup failure")

    async def fail_cleanup():
        raise cleanup

    monkeypatch.setattr(adapter, "close", fail_cleanup)
    if isinstance(adapter, LocalEnvdExecution):

        async def acquire():
            raise original

        monkeypatch.setattr(adapter._runtime, "acquire_device", acquire)
    else:

        def bind(**kwargs):
            raise original

        monkeypatch.setattr(remote_module, "EIPEnvironmentSession", bind)
    with pytest.raises(asyncio.CancelledError if cancel else EnvironmentProviderError) as caught:
        await adapter.open(execution_id="exec-test")
    primary = caught.value if cancel else caught.value.__cause__
    assert primary is original
    assert primary.__cause__ is cleanup


@pytest.mark.parametrize("failure", ["not_ready", "error"])
async def test_eip_readiness_recovers_through_real_adapter_and_aggregate(failure):
    from unittest.mock import AsyncMock

    from a13n_envd_client.eip import v1 as eip
    from a13n_harness import RunBindings
    from a13n_harness.environment.advanced import create_environment_runtime

    limits = SimpleNamespace(
        **dict.fromkeys(
            (
                "max_request_bytes",
                "max_response_bytes",
                "max_concurrent_operations",
                "max_processes",
                "max_operation_duration_ms",
                "max_output_preview_bytes",
                "max_output_bytes_per_stream",
                "max_transfer_frame_bytes",
                "max_concurrent_file_transfers",
                "max_file_transfer_bytes",
            ),
            1024,
        )
    )
    first = (
        SimpleNamespace(ready=False)
        if failure == "not_ready"
        else EnvironmentError("Transient readiness failure", code="environment_unavailable")
    )
    readiness = AsyncMock(
        side_effect=[SimpleNamespace(ready=True), first, SimpleNamespace(ready=True), SimpleNamespace(ready=True)]
    )
    stat = AsyncMock(
        return_value=eip.FileStatResult(
            info=eip.FileInfo(path=eip.EIPPath(path="/note.txt"), kind=eip.FileKind.FILE, size_bytes=4)
        )
    )
    session = SimpleNamespace(
        descriptor=SimpleNamespace(
            device_id="device",
            session_id="session",
            generation=1,
            working_directory="/",
            available_methods=("file.stat",),
            limits=limits,
            boundary=eip.ExecutionBoundary(
                sandbox=eip.DisabledSandbox(mode="disabled"),
                egress="inherit",
                privilege_gain_blocked=False,
                backend="native",
                policy_digest="0" * 64,
            ),
        ),
        readiness=readiness,
        client=SimpleNamespace(file_stat=stat),
    )
    closed = []

    @asynccontextmanager
    async def scope():
        try:
            yield session
        finally:
            closed.append(True)

    from a13n_environment.remote_envd.configuration import HttpEnvdConnectionConfiguration, HttpEnvdCredential
    from a13n_environment.remote_envd.http import HTTP_ENVD, HttpEnvdProviderRuntime

    class Runtime(HttpEnvdProviderRuntime):
        def open_session(self, **kwargs):
            return scope()

    owner = Runtime(
        HttpEnvdConnectionConfiguration(endpoint="https://fixture.example"), HttpEnvdCredential(token="fixture")
    )
    adapter = HTTP_ENVD.execution_connector(
        {},
        configuration=owner.configuration,
        runtime=owner,
        state=EnvironmentState(provider_key="http_envd", state_version="1", state={"device_id": "device"}),
    )
    runtime = create_environment_runtime(mounts={"workspace": adapter}, default_mount="workspace")
    async with runtime.bind(
        thread_id="thread-1", run_id="run-1", instance=RunBindings.embedded().instance, host_refs={}
    ) as bound:
        with pytest.raises(EnvironmentError) as caught:
            await bound.files.stat("note.txt")
        assert caught.value.code == "environment_unavailable"
        stat.assert_not_awaited()
        for _ in range(2):
            assert (await bound.files.stat("note.txt")).size == 4
        assert readiness.await_count == 4
        assert stat.await_count == 2
        assert closed == []
    assert closed == [True]


async def test_remote_cleanup_attempts_both_owners_and_retains_all_failures():
    first, second = RuntimeError("facets"), EIPSessionStateError("session")
    closed = []

    async def close_bound():
        closed.append("facets")
        raise first

    @asynccontextmanager
    async def session():
        yield
        closed.append("session")
        raise second

    scope = session()
    await scope.__aenter__()
    adapter = RemoteEnvdExecution(
        provider_key="http_envd",
        environment_id="test",
        state=EnvironmentState(provider_key="http_envd", state_version="1", state={"device_id": "device"}),
        session_context=scope,
    )
    adapter._entered_session = True
    adapter._bound = SimpleNamespace(close=close_bound)
    with pytest.raises(EnvironmentProviderError) as caught:
        await adapter.close()
    assert closed == ["facets", "session"]
    assert isinstance(caught.value.__cause__, ExceptionGroup)
    assert caught.value.__cause__.exceptions == (first, second)
