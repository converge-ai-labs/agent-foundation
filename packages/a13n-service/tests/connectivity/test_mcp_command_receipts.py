"""Discovery publishes its connection snapshot and command receipt together."""

from asyncio import create_task
from contextlib import asynccontextmanager

import pytest
from a13n_service.connectivity.connections.domain import CreateConnectionRequest, MCPSource
from a13n_service.connectivity.mcp.domain import MCPAuthMode, ReplaceMCPCredentialsRequest
from a13n_service.connectivity.mcp.errors import MCPConnectionError
from anyio import Event, fail_after

from .conftest import WORKSPACE_ID, actor
from .connection_helpers import management, mcp_checks
from .test_mcp_service import MCP_ENDPOINT
from .test_mcp_service import mcp_services as mcp_services

pytestmark = pytest.mark.anyio


async def test_credential_command_replays_completed_snapshot_without_repeating_io(mcp_services, monkeypatch):
    connections, _, remote = mcp_services
    remote.allow_anonymous = True
    creation = CreateConnectionRequest(
        name="Command MCP", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.bearer)
    )
    created = await management(connections).create(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="setup", request=creation
    )

    async def invoke():
        return await connections.replace_credentials(
            actor=actor(),
            connection_id=created.id,
            idempotency_key="command",
            request=ReplaceMCPCredentialsRequest(expected_version=created.version, bearer="bearer-secret"),
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
        assert await management(connections).get(actor=actor(), connection_id=result.id) == result
        assert await invoke() == result
        assert len(remote.requests) == request_count

        disabled = await management(connections).set_enabled(
            actor=actor(),
            connection_id=result.id,
            idempotency_key="disable",
            expected_version=result.version,
            enabled=False,
        )
        assert disabled.status == "disabled"
        assert await invoke() == result
        assert len(remote.requests) == request_count


async def test_failed_check_records_unavailability_and_an_explicit_check_can_recover(mcp_services, monkeypatch):
    connections, _, remote = mcp_services
    remote.allow_anonymous = True
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="setup",
        request=CreateConnectionRequest(
            name="Failure MCP", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.none)
        ),
    )
    transport = connections._discovery._transport
    original = transport.connect

    @asynccontextmanager
    async def failed_discovery(*args, **kwargs):
        async with original(*args, **kwargs) as client:
            yield client
        raise OSError("discovery interrupted")

    monkeypatch.setattr(transport, "connect", failed_discovery)
    pending = await mcp_checks(connections).check(
        actor=actor(), connection_id=created.id, expected_version=created.version
    )
    assert pending.status == "pending"
    assert pending.last_check is not None
    assert pending.last_check.status == "unavailable" and pending.last_check.error_code == "mcp_discovery_unavailable"
    monkeypatch.setattr(transport, "connect", original)
    recovered = await mcp_checks(connections).check(
        actor=actor(), connection_id=created.id, expected_version=pending.version
    )
    assert recovered.status == "ready"
