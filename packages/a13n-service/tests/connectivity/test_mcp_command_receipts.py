"""Discovery publishes its connection snapshot and command receipt together."""

from asyncio import create_task
from contextlib import asynccontextmanager

import pytest
from a13n_service.connectivity.mcp.domain import CreateMCPConnectionRequest, MCPAuthMode, ReplaceMCPCredentialsRequest
from a13n_service.connectivity.mcp.errors import MCPConnectionError
from anyio import Event, fail_after

from .conftest import WORKSPACE_ID, actor
from .test_mcp_service import MCP_ENDPOINT
from .test_mcp_service import mcp_services as mcp_services

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("operation", ["create", "credentials", "reconnect"])
async def test_discovery_command_replays_completed_snapshot_without_repeating_io(mcp_services, monkeypatch, operation):
    connections, _, remote = mcp_services
    remote.allow_anonymous = True
    creation = CreateMCPConnectionRequest(
        name="Command MCP",
        endpoint_url=MCP_ENDPOINT,
        auth_mode=MCPAuthMode.bearer if operation == "credentials" else MCPAuthMode.none,
    )
    created = None
    if operation != "create":
        created = await connections.create(
            actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="setup", request=creation
        )

    async def invoke():
        if operation == "create":
            return await connections.create(
                actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="command", request=creation
            )
        assert created is not None
        if operation == "credentials":
            return await connections.replace_credentials(
                actor=actor(),
                connection_id=created.id,
                idempotency_key="command",
                request=ReplaceMCPCredentialsRequest(expected_version=created.version, bearer="bearer-secret"),
            )
        return await connections.reconnect(
            actor=actor(), connection_id=created.id, idempotency_key="command", expected_version=created.version
        )

    arrived, release = Event(), Event()
    transport = connections._discovery._transport
    original = transport.connect

    @asynccontextmanager
    async def pause_before_publication(*args, **kwargs):
        async with original(*args, **kwargs) as client:
            yield client
        arrived.set()
        await release.wait()

    monkeypatch.setattr(transport, "connect", pause_before_publication)
    with fail_after(5):
        first = create_task(invoke())
        try:
            await arrived.wait()
            request_count = len(remote.requests)
            with pytest.raises(MCPConnectionError) as incomplete:
                await invoke()
            assert incomplete.value.code == "mcp_discovery_incomplete"
            assert len(remote.requests) == request_count
        finally:
            release.set()
            result = await first

        assert result.status == "ready"
        assert await connections.get(actor=actor(), connection_id=result.id) == result
        assert await invoke() == result
        assert len(remote.requests) == request_count

        disabled = await connections.set_enabled(
            actor=actor(),
            connection_id=result.id,
            idempotency_key="disable",
            expected_version=result.version,
            enabled=False,
        )
        assert disabled.status == "disabled"
        assert await invoke() == result
        assert len(remote.requests) == request_count


async def test_failed_discovery_has_no_success_receipt_and_new_reconnect_can_recover(mcp_services, monkeypatch):
    connections, _, remote = mcp_services
    remote.allow_anonymous = True
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="setup",
        request=CreateMCPConnectionRequest(name="Failure MCP", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.none),
    )
    transport = connections._discovery._transport
    original = transport.connect

    @asynccontextmanager
    async def failed_discovery(*args, **kwargs):
        async with original(*args, **kwargs) as client:
            yield client
        raise OSError("discovery interrupted")

    monkeypatch.setattr(transport, "connect", failed_discovery)
    with pytest.raises(MCPConnectionError) as failed:
        await connections.reconnect(
            actor=actor(), connection_id=created.id, idempotency_key="failed", expected_version=created.version
        )
    assert failed.value.code == "mcp_discovery_unavailable"
    request_count = len(remote.requests)
    with pytest.raises(MCPConnectionError) as replay:
        await connections.reconnect(
            actor=actor(), connection_id=created.id, idempotency_key="failed", expected_version=created.version
        )
    assert replay.value.code == "mcp_discovery_incomplete"
    assert len(remote.requests) == request_count
    pending = await connections.get(actor=actor(), connection_id=created.id)
    assert pending.status == "pending"
    monkeypatch.setattr(transport, "connect", original)
    recovered = await connections.reconnect(
        actor=actor(), connection_id=created.id, idempotency_key="recover", expected_version=pending.version
    )
    assert recovered.status == "ready"
