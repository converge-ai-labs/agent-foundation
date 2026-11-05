"""Session authorization must not outlive retirement or reach a replacement scope."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.models import EnvironmentError
from a13n_service.runs.environments.clients import authority
from a13n_service.runs.environments.clients.sessions import Scope, Sessions

from .test_client_routing import request

pytestmark = pytest.mark.anyio


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

    async def resolve(*args):
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
    monkeypatch.setattr(sessions, "remember_binding", AsyncMock())
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

    async def resolve(*args):
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
