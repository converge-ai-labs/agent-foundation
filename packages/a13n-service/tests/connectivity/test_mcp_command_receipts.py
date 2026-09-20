"""Credential authorization retries read their live operation without repeating I/O."""

from asyncio import create_task
from contextlib import asynccontextmanager

import pytest
from a13n_service.connectivity.connections.domain import CreateAuthorizationRequest, CreateConnectionRequest, MCPSource
from a13n_service.connectivity.mcp.domain import MCPAuthMode
from anyio import Event, fail_after

from .conftest import WORKSPACE_ID, actor
from .connection_helpers import authorizations, management, mcp_checks
from .test_mcp_service import MCP_ENDPOINT
from .test_mcp_service import mcp_services as mcp_services

pytestmark = pytest.mark.anyio


async def test_credential_command_projects_current_result_without_repeating_io(mcp_services, monkeypatch):
    connections, oauth, remote = mcp_services
    remote.allow_anonymous = True
    creation = CreateConnectionRequest(
        name="Command MCP", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.bearer)
    )
    created = await management(connections).create(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="setup", request=creation
    )

    service = authorizations(connections, oauth)

    async def invoke():
        return await service.create(
            actor=actor(),
            connection_id=created.id,
            idempotency_key="command",
            request=CreateAuthorizationRequest(
                expected_version=created.version, method="credentials", credentials={"bearer": "bearer-secret"}
            ),
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
            pending = await invoke()
            assert pending.status == "preparing"
            assert len(remote.requests) == request_count
        finally:
            release.set()
            result = await first

        assert result.status == "completed"
        assert await service.get(actor=actor(), authorization_id=result.id) == result
        assert await invoke() == result
        assert len(remote.requests) == request_count

        disabled = await management(connections).set_enabled(
            actor=actor(),
            connection_id=created.id,
            idempotency_key="disable",
            expected_version=(await management(connections).get(actor=actor(), connection_id=created.id)).version,
            enabled=False,
        )
        assert disabled.status == "disabled"
        replay = await invoke()
        assert replay.id == result.id and replay.status == "completed"
        assert replay.next_action.type == "check_connection"
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
