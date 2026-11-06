"""Session authorization must not outlive retirement or reach a replacement scope."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.environment.models import EnvironmentError
from a13n_service.runs.environments.clients import authority
from a13n_service.runs.environments.clients.sessions import Scope, Sessions

from .test_client_routing import request

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("retirement", ["takeover", "renewal", "detach"])
@pytest.mark.parametrize("pending", ["borrower", "transfer"])
async def test_concurrent_opens_wait_for_previous_native_owner_retirement(monkeypatch, retirement, pending):
    from contextlib import asynccontextmanager

    from a13n_envd_client.eip.v1 import EgressMode

    retiring, release = asyncio.Event(), asyncio.Event()
    native = AsyncMock()
    native.descriptor.working_directory = "/work"
    native.descriptor.model_dump = Mock(return_value={})
    device = SimpleNamespace(descriptor=SimpleNamespace(boundary=SimpleNamespace(egress=EgressMode.INHERIT)))
    device.descriptor.model_dump = Mock(return_value={})
    connection = SimpleNamespace(device=device, attachment=SimpleNamespace(device_id="device"))
    calls = []

    @asynccontextmanager
    async def borrow(*args, **kwargs):
        assert release.is_set()
        calls.append(kwargs)
        yield native

    runtime = SimpleNamespace(
        environment_contexts=SimpleNamespace(for_control=AsyncMock(return_value=SimpleNamespace(borrow=borrow))),
        settings=SimpleNamespace(client_environments=SimpleNamespace(max_sessions=10, request_seconds=5)),
    )
    sessions = Sessions(runtime)
    original = request("scope.open").model_copy(update={"params": {"working_directory": "/work"}})
    previous = Scope(original, connection, native, None)

    async def detach():
        retiring.set()
        await release.wait()

    if pending == "borrower":
        previous.lifetime.push_async_callback(detach)
    else:
        from a13n_service.runs.environments.clients.sessions import Transfer

        channel = SimpleNamespace(handle="old-reader", peer_reset_received=False)
        previous.transfers[channel.handle] = Transfer(channel, "read", frozenset(), original.caller)
        native.requester.retire_transfer = Mock()

        async def wait_reset(channel):
            await detach()

        native.requester.wait_transfer_retired.side_effect = wait_reset
    sessions.scopes[original.scope_id] = previous
    monkeypatch.setattr(sessions, "connection", AsyncMock(return_value=connection))
    monkeypatch.setattr(
        sessions,
        "_resolve",
        AsyncMock(return_value=SimpleNamespace(working_directory="/work", session_binding="default")),
    )
    first = original.model_copy(
        update={"scope_id": "1" * 32, "caller": original.caller.model_copy(update={"worker_id": "successor"})}
    )
    second = first.model_copy(update={"scope_id": "2" * 32})
    tasks = []
    try:
        if retirement == "renewal":
            tasks.append(asyncio.create_task(sessions.retire(original.scope_id, previous)))
        elif retirement == "detach":
            tasks.append(asyncio.create_task(sessions.dispatch(original.model_copy(update={"method": "scope.detach"}))))
        tasks.append(asyncio.create_task(sessions.open(first)))
        await asyncio.wait_for(retiring.wait(), 2)
        tasks.append(asyncio.create_task(sessions.open(second)))
        await asyncio.sleep(0)
        assert not calls
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert len(calls) == 2
        assert set(sessions.scopes) == {first.scope_id, second.scope_id}
        assert not sessions.acquisitions
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await sessions.close()


@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.parametrize("method", ["scope.close", "environment.describe"])
async def test_retired_scope_cannot_dispatch_or_delete_its_replacement(monkeypatch, replace, method):
    sessions = Sessions(SimpleNamespace())
    call = request(method)
    if method == "environment.describe":
        call = call.model_copy(update={"params": {"context": {"operation_id": "op_describe"}}})
    connection = SimpleNamespace(attachment=None)
    original = Scope(call, connection, AsyncMock(), 123)
    successor = Scope(call, connection, AsyncMock(), 456)
    sessions.scopes[call.scope_id] = original
    monkeypatch.setattr(sessions, "connection", AsyncMock(return_value=connection))
    checking, release = asyncio.Event(), asyncio.Event()

    async def resolve(*args, **kwargs):
        checking.set()
        await release.wait()

    monkeypatch.setattr(authority, "resolve", resolve)
    dispatch = asyncio.create_task(sessions.dispatch(call))
    try:
        await asyncio.wait_for(checking.wait(), 2)
        await sessions.retire(call.scope_id, original)
        if replace:
            sessions.scopes[call.scope_id] = successor
        release.set()
        with pytest.raises(EnvironmentError) as failed:
            await dispatch
        assert failed.value.code == "environment_unavailable"
        assert failed.value.details["dispatch_stage"] == "pre_dispatch"
        assert sessions.scopes.get(call.scope_id) is (successor if replace else None)
        original.session.close.assert_not_awaited()
        original.session.requester.request.assert_not_awaited()
        successor.session.close.assert_not_awaited()
        assert successor.first_use_until == 456
        assert not successor.callers
    finally:
        dispatch.cancel()
        await asyncio.gather(dispatch, return_exceptions=True)


@pytest.mark.parametrize("invalid", ["scope", "session", "handle", "direction", "caller", "duplicate"])
async def test_transfer_attachment_rejects_wrong_selection_before_creating_streams(monkeypatch, invalid):
    from a13n_service.runs.environments.clients.sessions import Transfer

    sessions = Sessions(SimpleNamespace())
    call = request("transfer.attach")
    call = call.model_copy(update={"params": {"session_id": "session", "handle": "handle", "direction": "read"}})
    native = SimpleNamespace(session_id="session")
    scope = Scope(call, SimpleNamespace(), native, None)
    transfer = Transfer(SimpleNamespace(), "read", frozenset(), call.caller)
    scope.transfers["handle"] = transfer
    sessions.scopes[call.scope_id] = scope
    authorize = AsyncMock(return_value=scope)
    monkeypatch.setattr(sessions, "authorize", authorize)
    if invalid == "scope":
        call = call.model_copy(update={"scope_id": "0" * 32})
    elif invalid == "caller":
        call = call.model_copy(update={"caller": call.caller.model_copy(update={"worker_id": "other"})})
    elif invalid == "duplicate":
        transfer.attached = True
    else:
        key = "session_id" if invalid == "session" else invalid
        call = call.model_copy(update={"params": call.params | {key: "write" if key == "direction" else "other"}})
    with pytest.raises(EnvironmentError):
        await sessions.attach_transfer(call)
    assert transfer.task is None
    if invalid != "duplicate":
        authorize.assert_not_awaited()


async def test_cancelled_attachment_does_not_leave_streams_or_start_relay(monkeypatch):
    from a13n_service.runs.environments.clients import sessions as module
    from a13n_service.runs.environments.clients.sessions import Transfer

    runtime = SimpleNamespace(
        client_routing=SimpleNamespace(redis=None),
        settings=SimpleNamespace(client_environments=SimpleNamespace(transfer_seconds=2)),
    )
    sessions = Sessions(runtime)
    call = request("transfer.attach").model_copy(
        update={"params": {"session_id": "session", "handle": "handle", "direction": "read"}}
    )
    native = SimpleNamespace(
        session_id="session", descriptor=SimpleNamespace(limits=SimpleNamespace(max_transfer_frame_bytes=100))
    )
    scope = Scope(call, SimpleNamespace(), native, None)
    transfer = Transfer(SimpleNamespace(), "read", frozenset(), call.caller)
    scope.transfers["handle"] = transfer
    sessions.scopes[call.scope_id] = scope
    monkeypatch.setattr(sessions, "authorize", AsyncMock(return_value=scope))
    creating = asyncio.Event()

    async def create():
        creating.set()
        await asyncio.Future()

    stream = SimpleNamespace(create=create, close=AsyncMock())
    monkeypatch.setattr(module, "StreamPair", lambda *args, **kwargs: stream)
    attached = asyncio.create_task(sessions.attach_transfer(call))
    await asyncio.wait_for(creating.wait(), 2)
    attached.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attached
    stream.close.assert_awaited_once()
    assert transfer.attached and transfer.task is None


@pytest.mark.parametrize("authorized", [True, False])
async def test_missing_session_requires_current_authority_before_recovery(monkeypatch, authorized):
    sessions = Sessions(SimpleNamespace())
    call = request()
    connection = SimpleNamespace(attachment=None)
    monkeypatch.setattr(sessions, "connection", AsyncMock(return_value=connection))
    resolve = AsyncMock(side_effect=None if authorized else EnvironmentError("Revoked", code="environment_denied"))
    monkeypatch.setattr(authority, "resolve", resolve)
    with pytest.raises(EnvironmentError) as failed:
        await sessions.authorize(call, frozenset())
    assert failed.value.code == ("environment_unavailable" if authorized else "environment_denied")
    assert failed.value.details.get("client_session_failed", False) is authorized
    resolve.assert_awaited_once()


async def test_renewal_batches_transfers_and_keeps_independent_valid_owners(monkeypatch):
    from a13n_service.runs.environments.clients import streaming
    from a13n_service.runs.environments.clients.sessions import Transfer

    runtime = SimpleNamespace(settings=SimpleNamespace(client_environments=SimpleNamespace(heartbeat_seconds=5)))
    sessions = Sessions(runtime)
    call = request()
    connection = SimpleNamespace(attachment=None)
    scope = Scope(call, connection, AsyncMock(), None)
    scope.remember(call.caller)
    retained = call.caller.model_copy(update={"execution_id": "retained"})
    scope.remember(retained)
    transfer = Transfer(SimpleNamespace(), "read", frozenset({"environment.file.read_bytes"}), retained)
    transfer.task = SimpleNamespace(done=lambda: False)
    scope.transfers["handle"] = transfer
    sessions.scopes[call.scope_id] = scope
    monkeypatch.setattr(sessions, "connection", AsyncMock(return_value=connection))
    denied = EnvironmentError("Revoked", code="environment_denied")
    resolve = AsyncMock(return_value=[None, denied, denied])
    stop = AsyncMock()
    monkeypatch.setattr(authority, "resolve_many", resolve)
    monkeypatch.setattr(streaming, "stop", stop)
    await sessions._renew_scope(call.scope_id, scope)
    assert sessions.scopes[call.scope_id] is scope
    assert list(scope.callers.values()) == [call.caller]
    checks = resolve.call_args.args[1]
    assert len(checks) == 3 and checks[-1].actions == transfer.actions and not checks[-1].renewing
    stop.assert_awaited_once_with(transfer)
    scope.session.abort.assert_not_awaited()


async def test_session_authorization_bounds_database_concurrency(monkeypatch):
    sessions = Sessions(SimpleNamespace())
    call = request()
    connection = SimpleNamespace(attachment=None)
    active = maximum = 0

    async def resolve(*args, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        try:
            await asyncio.sleep(0.01)
        finally:
            active -= 1

    monkeypatch.setattr(authority, "resolve", resolve)
    await asyncio.gather(*(sessions._resolve(call, connection, frozenset()) for _ in range(12)))
    assert maximum == 4 and active == 0


@pytest.mark.parametrize("attached", [False, True])
@pytest.mark.parametrize("finished", [False, True])
async def test_retiring_scope_releases_its_transfers_without_closing_shared_session(attached, finished):
    from a13n_envd_client.eip.v1 import DataFrameKind
    from a13n_service.runs.environments.clients.sessions import Transfer

    sessions = Sessions(SimpleNamespace())
    call = request()
    native = SimpleNamespace(session_id="session", requester=Mock(), close=AsyncMock(), abort=AsyncMock())
    scope = Scope(call, SimpleNamespace(), native, None)
    sibling = Scope(call.model_copy(update={"scope_id": "f" * 32}), scope.connection, native, None)
    channel = SimpleNamespace(handle="transfer", peer_reset_received=False)
    transfer = Transfer(channel, "read", frozenset(), call.caller, attached=attached, finished=finished)
    if attached:
        transfer.task = asyncio.create_task(asyncio.sleep(60))
    scope.transfers[channel.handle] = transfer
    release = AsyncMock()
    scope.lifetime.push_async_callback(release)
    sessions.scopes[call.scope_id] = scope
    sessions.scopes[sibling.request.scope_id] = sibling
    await sessions.retire(call.scope_id, scope)
    assert sessions.scopes == {sibling.request.scope_id: sibling}
    assert not scope.transfers
    native.requester.retire_transfer.assert_called_once()
    assert native.requester.retire_transfer.call_args.kwargs["reset"].kind is DataFrameKind.RESET
    if attached:
        assert transfer.task.done()
    release.assert_awaited_once()
    native.close.assert_not_awaited()
    native.abort.assert_not_awaited()


@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
async def test_unconfirmed_transfer_retirement_closes_carrier_before_detach(monkeypatch, failure):
    from a13n_envd_client import AcceptedWebSocketTransport, EIPDeviceConnection, RequestCoordinator
    from a13n_envd_client.eip.v1 import DeviceDescriptor, EIPLimits, SessionDescriptor
    from a13n_envd_client.session import EIPSession
    from a13n_service.runs.environments.clients.connections import Connection
    from a13n_service.runs.environments.clients.sessions import Transfer

    closed = asyncio.Event()
    carrier = SimpleNamespace(subprotocol="eip.v1", send=AsyncMock())
    call = request("scope.detach")
    transport = AcceptedWebSocketTransport(carrier)
    coordinator = RequestCoordinator(transport)
    device = EIPDeviceConnection(coordinator, DeviceDescriptor.model_construct(), max_in_flight=4)
    connection = Connection(SimpleNamespace(environment_id=call.environment_id), call.epoch, device, carrier)
    sessions = Sessions(
        SimpleNamespace(client_connections=SimpleNamespace(entries={call.environment_id: connection}, recovering=False))
    )

    async def disconnect(**kwargs):
        # Local admission is fenced even before asynchronous carrier closure finishes.
        with pytest.raises(EnvironmentError):
            await sessions.connection(call)
        closed.set()

    async def receive():
        await closed.wait()
        raise EOFError

    carrier.close = AsyncMock(side_effect=disconnect)
    carrier.recv = receive
    limits = EIPLimits.model_construct(max_concurrent_operations=4, max_concurrent_file_transfers=2)
    native_sessions = []
    for identity in ("ses-a", "ses-b"):
        requester = coordinator.session(identity, limits=limits)
        native = EIPSession(device, requester, SessionDescriptor.model_construct(session_id=identity))
        native._ready = True
        device._sessions[identity] = native
        native_sessions.append(native)
    native, sibling = native_sessions
    channel = native.requester.register_transfer("reader", direction="read")
    scope = Scope(call, connection, native, None)
    scope.transfers[channel.handle] = Transfer(channel, "read", frozenset(), call.caller)
    monkeypatch.setattr(native.requester, "wait_transfer_retired", AsyncMock(side_effect=failure))
    try:
        with pytest.raises(failure):
            await sessions.stop_transfers(scope)
        await asyncio.wait_for(sibling.requester.wait_finished(), 1)
        assert connection.device is None
        assert closed.is_set()
        assert not scope.transfers
        # Real SDK Device.close() would emit session.close for both bound Sessions.
        assert all(isinstance(item.args[0], bytes) for item in carrier.send.await_args_list)
    finally:
        await coordinator.close()


async def test_scope_cannot_be_readdressed_to_another_view(monkeypatch):
    sessions = Sessions(SimpleNamespace())
    call = request()
    connection = SimpleNamespace(attachment=None)
    scope = Scope(call, connection, AsyncMock(), None)
    sessions.scopes[call.scope_id] = scope
    monkeypatch.setattr(sessions, "connection", AsyncMock(return_value=connection))
    resolve = AsyncMock()
    monkeypatch.setattr(authority, "resolve", resolve)
    with pytest.raises(EnvironmentError) as failed:
        await sessions.authorize(call.model_copy(update={"mount_name": "other"}), frozenset())
    assert failed.value.code == "environment_denied"
    resolve.assert_not_awaited()
