"""Cross-request Modal borrowing retains target checks, fencing and explicit cleanup."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.modal.pool import TransportPool
from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration, ModalCredential, ModalRuntime
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.providers.environments.modal import provider
from a13n_service.runs.desktop_assistance import service

from .test_modal_desktop_session import HANDBACK, REQUESTS
from .test_modal_desktop_session import cloud as cloud


@pytest.fixture
async def pooled(cloud, monkeypatch):
    pool = TransportPool()
    fixture = SimpleNamespace(
        pool=pool,
        runtime=ModalRuntime(
            ModalConnectionConfiguration(workspace="test", app_name="test"),
            ModalCredential(token_id="fixture-id", token_secret="fixture-secret"),
            pool=pool,
            account=("provider-first", 1, "credential-first"),
        ),
    )

    async def open_provider(runtime, target):
        return await provider(configuration=fixture.runtime.configuration, credential=None, runtime=fixture.runtime)

    monkeypatch.setattr(service, "open_provider", open_provider)
    try:
        yield fixture
    finally:
        await pool.close()


async def describe(cloud, identity="dtop-test"):
    async with service.access_dispatch(None, cloud.target, operation_id=identity) as send:
        return await send(wire.Describe())


@pytest.mark.anyio
async def test_requests_share_transport_but_revalidate_owner_and_liveness(cloud, pooled):
    for identity, requests in (("dtop-grant", REQUESTS), ("dtop-return", HANDBACK)):
        async with service.access_dispatch(None, cloud.target, operation_id=identity) as send:
            for request in requests:
                await send(request)
        assert not cloud.clients[0].closed
    assert len(cloud.clients) == len(cloud.sandboxes) == cloud.lookup.await_count == 1
    target = cloud.sandboxes[0]
    assert target.get_tags.aio.await_count == target.poll.aio.await_count == 2
    assert [request for _, request in cloud.calls] == [*REQUESTS, *HANDBACK]
    target.detach.aio.assert_not_awaited()
    await pooled.pool.close()
    target.detach.aio.assert_awaited_once()
    assert cloud.clients[0].closed
    with pytest.raises(EnvironmentProviderError):
        await describe(cloud)


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["provider", "version", "credential", "backend", "target"])
async def test_account_and_fixed_target_boundaries_do_not_reuse_handles(cloud, pooled, boundary):
    await describe(cloud)
    current = pooled.runtime
    if boundary == "provider":
        pooled.runtime = replace(current, account=("provider-second", 1, "credential-first"))
    elif boundary == "version":
        pooled.runtime = replace(current, account=("provider-first", 2, "credential-first"))
    elif boundary == "credential":
        pooled.runtime = replace(current, account=("provider-first", 1, "credential-second"))
    elif boundary == "backend":
        pooled.runtime = replace(current, configuration=current.configuration.model_copy(update={"app_name": "other"}))
    else:
        # Same native ID in a different logical environment must still validate its owner.
        state = cloud.state.model_copy(deep=True)
        state.state["environment_id"] = "env-other"
        cloud.target.state = state
        cloud.target.environment_id = "env-other"
        cloud.mismatched_tags = True
    if boundary == "target":
        with pytest.raises(EnvironmentProviderError):
            await describe(cloud)
    else:
        await describe(cloud)
    assert len(cloud.clients) == cloud.lookup.await_count == 2
    assert cloud.sandboxes[0] is not cloud.sandboxes[1]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["stale_generation", "stale_epoch", "endpoint_unavailable", "timeout"])
async def test_failure_or_cancellation_retires_transport_without_command_replay(cloud, pooled, failure):
    await describe(cloud)
    cloud.failure, cloud.failure_operation = failure, "renew"
    with pytest.raises(TimeoutError if failure == "timeout" else EnvironmentProviderError):
        with anyio.fail_after(0.1):
            async with service.access_dispatch(None, cloud.target, operation_id="dtop-failed") as send:
                for request in REQUESTS:
                    await send(request)
    assert [request.operation for _, request in cloud.calls] == ["describe", "describe", "renew"]
    assert cloud.lookup.await_count == 1  # The failed operation was never repeated on a replacement.
    cloud.sandboxes[0].detach.aio.assert_awaited_once()
    assert cloud.clients[0].closed
    cloud.failure = None
    await describe(cloud)
    assert cloud.lookup.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize("changed", ["owner", "stopped"])
async def test_cached_handle_does_not_bypass_current_target_checks(cloud, pooled, changed):
    await describe(cloud)
    target = cloud.sandboxes[0]
    if changed == "owner":
        target.get_tags.aio.return_value = {"a13n_environment": "another-owner"}
    else:
        target.poll.aio.return_value = 0
    with pytest.raises(EnvironmentProviderError):
        await describe(cloud)
    assert len(cloud.calls) == 1  # No guest command after a failed ownership/liveness check.
    assert cloud.lookup.await_count == 1
    target.detach.aio.assert_awaited_once()
    assert cloud.clients[0].closed


@pytest.mark.anyio
async def test_concurrent_requests_serialize_sdk_use_and_cancelled_waiter_does_not_close_owner(cloud, pooled):
    await describe(cloud)
    entered, release = asyncio.Event(), asyncio.Event()
    target = cloud.sandboxes[0]
    original = target.exec.aio.side_effect

    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    target.exec.aio.side_effect = held
    first = asyncio.create_task(describe(cloud, "dtop-owner"))
    await entered.wait()
    waiting = asyncio.create_task(describe(cloud, "dtop-waiter"))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not cloud.clients[0].closed
    assert target.get_tags.aio.await_count == 2  # The cancelled waiter never entered SDK use.
    release.set()
    await first
    target.exec.aio.side_effect = original
    await describe(cloud)
    assert len(cloud.clients) == 1


@pytest.mark.anyio
async def test_expiry_and_capacity_close_idle_handles_without_terminating_sandbox(cloud, pooled):
    await describe(cloud)
    pooled.pool.idle_seconds = 0
    await pooled.pool.expire()
    assert cloud.clients[0].closed
    cloud.sandboxes[0].detach.aio.assert_awaited_once()
    pooled.pool.idle_seconds = 120
    pooled.pool.capacity = 1
    await describe(cloud)
    pooled.runtime = replace(pooled.runtime, account=("provider-second", 1, "credential-first"))
    await describe(cloud)
    assert cloud.clients[1].closed
    cloud.sandboxes[1].detach.aio.assert_awaited_once()
    assert not cloud.clients[2].closed


@pytest.mark.anyio
async def test_cleanup_failure_still_closes_client_and_discards_unusable_sandbox(cloud, pooled):
    await describe(cloud)
    cloud.sandboxes[0].detach.aio = AsyncMock(side_effect=RuntimeError("fixture cleanup failure"))
    with pytest.raises(RuntimeError):
        await pooled.pool.close()
    assert cloud.clients[0].closed


@pytest.mark.anyio
async def test_busy_capacity_fails_without_closing_active_borrower(cloud, pooled):
    pooled.pool.capacity = 1
    async with service.access_dispatch(None, cloud.target, operation_id="dtop-first") as send:
        await send(wire.Describe())
        pooled.runtime = replace(pooled.runtime, account=("provider-second", 1, "credential-first"))
        with pytest.raises(EnvironmentProviderError):
            await describe(cloud)
        assert not cloud.clients[0].closed
    assert cloud.lookup.await_count == 1


@pytest.mark.anyio
async def test_partial_client_open_is_closed_and_next_request_opens_a_new_client(cloud, pooled, monkeypatch):
    import modal

    original = modal.Client.__aenter__

    async def failed_open(self):
        raise RuntimeError("fixture handshake failed")

    monkeypatch.setattr(modal.Client, "__aenter__", failed_open)
    with pytest.raises(EnvironmentProviderError):
        await describe(cloud)
    assert cloud.clients[0].closed
    assert not cloud.sandboxes
    monkeypatch.setattr(modal.Client, "__aenter__", original)
    await describe(cloud)
    assert len(cloud.clients) == 2


@pytest.mark.anyio
async def test_real_sdk_reuses_client_task_id_and_router_but_reads_tags_and_poll_each_time(tmp_path, monkeypatch):
    from a13n_harness.environment.models import EnvironmentState
    from a13n_harness.providers.environment.modal.http import HttpReference
    from a13n_harness.providers.environment.modal.shared import ModalEnvironmentConfiguration, execute_command
    from grpclib.server import Server

    from .test_modal_native import ModalCloud

    root = tmp_path / "guest"
    root.mkdir()
    cloud = ModalCloud(root)
    server = Server([cloud])
    await server.start("127.0.0.1", 0)
    port = server._server.sockets[0].getsockname()[1]
    cloud.url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("MODAL_SERVER_URL", cloud.url)
    monkeypatch.setenv("MODAL_SANDBOX_V2", "false")
    pool = TransportPool()
    pool.start()
    config = ModalEnvironmentConfiguration(image="im-envd")
    identity = "sb-nGEijt9WbBMlGrsPH9FOa1"
    cloud.sandboxes[identity] = {
        "stopped": False,
        "tags": {"a13n_environment": "env-real", "a13n_configuration": config.fingerprint},
    }
    state = EnvironmentState(
        provider_key="modal",
        state_version="1",
        state={
            "target_id": identity,
            "environment_id": "env-real",
            "configuration_fingerprint": config.fingerprint,
        },
    )
    runtime = ModalRuntime(
        ModalConnectionConfiguration(workspace="fixture", app_name="fixture"),
        ModalCredential(token_id="fixture", token_secret="fixture"),
        pool=pool,
        account=("provider-real", 1, "nonce-real"),
    )
    clients = []
    try:
        for index in range(2):
            reference = HttpReference(config, "env-real", state, runtime)
            try:
                sandbox = await reference.lookup()
                assert sandbox is not None
                clients.append(reference.client)
                assert (await execute_command(sandbox, ["python3", "-c", f"print({index})"], 10)).strip() == str(index)
            finally:
                await reference.close_transport()
        assert clients[0] is clients[1] and clients[0] is not None
        assert cloud.calls.count("SandboxGetTaskId") == 1
        assert cloud.calls.count("TaskGetCommandRouterAccess") == 1
        assert cloud.calls.count("SandboxTagsGet") == 2
        assert cloud.calls.count("TaskExecStart") == 2
        # First from_id performs an additional SandboxWait; each borrowed request still polls.
        assert cloud.calls.count("SandboxWait") == 3
    finally:
        await pool.close()
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_independent_desktop_revoke_dispatch_also_borrows_cached_transport(cloud, pooled):
    await describe(cloud)
    resource = await service.open_provider(None, cloud.target)
    try:
        await resource.desktop_access(
            cloud.target.recipe,
            environment_id=cloud.target.environment_id,
            state=cloud.target.state,
            operation_id="dtop-disconnect",
            request=HANDBACK[0],
        )
    finally:
        await resource.close()
    assert cloud.lookup.await_count == 1
    assert cloud.sandboxes[0].get_tags.aio.await_count == 2
    assert not cloud.clients[0].closed


@pytest.mark.anyio
async def test_shutdown_retires_active_transport_and_borrower_performs_cleanup(cloud, pooled):
    async with service.access_dispatch(None, cloud.target, operation_id="dtop-shutdown") as send:
        await send(wire.Describe())
        await pooled.pool.close()
        assert not cloud.clients[0].closed
        with pytest.raises(EnvironmentProviderError):
            await describe(cloud)
    assert cloud.clients[0].closed
    cloud.sandboxes[0].detach.aio.assert_awaited_once()
