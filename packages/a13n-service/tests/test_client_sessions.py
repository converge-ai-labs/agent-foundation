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
    original = Scope(call, connection, AsyncMock(), "token", 123)
    successor = Scope(call, connection, AsyncMock(), "replacement", 456)
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
