"""Provider boundaries preserve public errors and cancellation, not client errors."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from a13n_envd_client import EIPSessionStateError
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.local_envd.configuration import LocalEnvdEnvironmentConfiguration
from a13n_harness.providers.environment.local_envd.provider import LocalEnvdEnvironment
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_harness.providers.environment.models import EnvironmentError, EnvironmentState
from a13n_harness.providers.environment.remote_envd import environment as remote_module
from a13n_harness.providers.environment.remote_envd.environment import RemoteEnvdEnvironment

pytestmark = pytest.mark.anyio


@pytest.fixture(params=["local", "remote"])
def adapter(request, tmp_path):
    if request.param == "local":
        return LocalEnvdEnvironment(
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

    return RemoteEnvdEnvironment(
        provider_key="http_envd",
        environment_id="test",
        state=EnvironmentState(provider_key="http_envd", state_version="1", state={"device_id": "device"}),
        session_context=session(),
    )


def install_scope(adapter, scope):
    if isinstance(adapter, LocalEnvdEnvironment):
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

    monkeypatch.setattr(adapter, "_close", fail_cleanup)
    if isinstance(adapter, LocalEnvdEnvironment):

        async def acquire():
            raise original

        monkeypatch.setattr(adapter._runtime, "acquire_device", acquire)
    else:

        def bind(**kwargs):
            raise original

        monkeypatch.setattr(remote_module, "EIPEnvironmentSession", bind)
    with pytest.raises(asyncio.CancelledError if cancel else EnvironmentProviderError) as caught:
        await adapter.prepare()
    primary = caught.value if cancel else caught.value.__cause__
    assert primary is original
    assert primary.__cause__ is cleanup


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
    adapter = RemoteEnvdEnvironment(
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
