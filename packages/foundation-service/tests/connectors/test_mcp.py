from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx2
import pytest
from a13n_harness import AgentContext
from a13n_harness.tools import HARNESS_TOOL_METADATA_KEY, HarnessToolMetadata
from a13n_service.connectors import (
    ConnectorCapabilityClaims,
    ConnectorCapabilityCodec,
    ConnectorError,
    ConnectorMCPGateway,
    ConnectorMCPInvocation,
    ConnectorProviderRuntime,
    ConnectorProviderTool,
    ConnectorProviderToolResult,
    ConnectorRunSelection,
    PrincipalRef,
    build_connector_mcp_client,
)
from a13n_service.connectors.client import _ConnectorManagedMetadataToolset
from a13n_service.iam import PrincipalType
from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount

ORG_ID = "org_0000000000000001"
WORKSPACE_ID = "ws_0000000000000001"
USER_ID = "usr_0000000000000001"
CONNECTOR_ID = "con_0000000000000001"
REVISION_ID = "conrev_0000000000000001"


def _principal() -> PrincipalRef:
    return PrincipalRef(principal_type=PrincipalType.user, principal_id=USER_ID)


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def list_selected_tools(self, **values: Any) -> tuple[ConnectorProviderTool, ...]:
        self.calls.append({"operation": "list", **values})
        return (
            ConnectorProviderTool(
                name="search",
                tool_id="tool-search",
                description="Search records",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                effects=("read",),
                credential_audiences=("search_api",),
                idempotency="read_only",
                output_policy={
                    "max_inline_bytes": 1_024,
                    "max_output_bytes": 4_096,
                    "overflow": "spill",
                    "redact": True,
                },
            ),
        )

    async def call_tool(self, **values: Any) -> ConnectorProviderToolResult:
        self.calls.append({"operation": "call", **values})
        if "query" not in values["arguments"]:
            raise ConnectorError("Connector tool arguments are invalid.", code="invalid_request")
        return ConnectorProviderToolResult(value={"matched": values["arguments"]["query"]})


class _Authenticator:
    def __init__(self) -> None:
        self.connection_ids: list[str | None] = []

    async def __call__(
        self,
        request: Request,
        *,
        connector_id: str,
        connection_id: str | None,
    ) -> ConnectorMCPInvocation:
        del request
        self.connection_ids.append(connection_id)
        return ConnectorMCPInvocation(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_id=connector_id,
            connector_revision_id=REVISION_ID,
            connection_id=connection_id,
            effective_tools=("search",),
            provider_contract_version="1",
            principal=_principal(),
            request_id="req-mcp-test",
        )


async def _post(client: httpx2.AsyncClient, payload: dict[str, Any]) -> httpx2.Response:
    return await client.post(
        f"/mcp/connectors/{CONNECTOR_ID}",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "X-Foundation-Connection-Id": "conn_0000000000000001",
        },
        json=payload,
    )


@pytest.mark.anyio
async def test_stateless_connector_mcp_gateway_lists_and_calls_provider_tools() -> None:
    runtime = _Runtime()
    authenticator = _Authenticator()
    gateway = ConnectorMCPGateway(
        cast(ConnectorProviderRuntime, runtime),
        standard_authenticator=authenticator,
        attempt_authenticator=None,
    )
    app = Starlette(routes=[Mount("/mcp/connectors", app=gateway.standard_app)])

    async with gateway.run():
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            initialized = await _post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            listed = await _post(
                client,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            invalid = await _post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "search", "arguments": {}},
                },
            )
            called = await _post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "search", "arguments": {"query": "connector"}},
                },
            )

    assert initialized.status_code == 200
    assert initialized.json()["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert listed.json()["result"]["tools"][0]["name"] == "search"
    assert listed.json()["result"]["tools"][0]["annotations"]["readOnlyHint"] is True
    managed = listed.json()["result"]["tools"][0]["_meta"][HARNESS_TOOL_METADATA_KEY]
    assert managed["credential_audiences"] == ["search_api"]
    assert managed["output_policy"]["max_output_bytes"] == 4_096
    assert invalid.json()["result"]["isError"] is True
    assert invalid.json()["result"]["content"][0]["text"].endswith("invalid_request")
    assert called.json()["result"]["structuredContent"] == {"matched": "connector"}
    assert authenticator.connection_ids == ["conn_0000000000000001"] * 4
    assert [call["operation"] for call in runtime.calls] == ["list", "call", "call"]


def test_connector_capability_is_signed_bounded_and_expires() -> None:
    codec = ConnectorCapabilityCodec(b"k" * 32)
    now = datetime(2026, 9, 1, 9, tzinfo=UTC)
    claims = ConnectorCapabilityClaims(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_id=CONNECTOR_ID,
        connector_revision_id=REVISION_ID,
        connection_id=None,
        agent_preset_version_id="agpv_0000000000000001",
        run_id="run_0000000000000001",
        run_attempt_id="attempt_0000000000000001",
        attempt_fence=3,
        declaration_index=0,
        effective_tools=("search",),
        provider_contract_version="1",
        issued_at=now,
        expires_at=now + timedelta(minutes=2),
    )

    token = codec.issue(claims, now=now)

    assert codec.verify(token, now=now + timedelta(minutes=1)) == claims
    with pytest.raises(ConnectorError, match="invalid or expired"):
        codec.verify(token, now=now + timedelta(minutes=3))
    with pytest.raises(ConnectorError, match="invalid or expired"):
        codec.verify(f"{token[:-1]}x", now=now)
    with pytest.raises(ConnectorError, match="lifetime is invalid"):
        codec.issue(
            claims.model_copy(
                update={
                    "issued_at": now + timedelta(seconds=4),
                    "expires_at": now + timedelta(seconds=2),
                }
            ),
            now=now,
        )


def test_worker_reconstructs_local_mcp_client_from_run_selection() -> None:
    selection = ConnectorRunSelection(
        declaration_index=2,
        connector_revision_id=REVISION_ID,
        connection_id=None,
        effective_tools=("search",),
        provider_contract_version="1",
    )

    capability = build_connector_mcp_client(
        "https://connectors.example.com",
        connector_id=CONNECTOR_ID,
        selection=selection,
        capability_token="signed-capability",
    )

    assert capability.url == f"https://connectors.example.com/internal/mcp/connectors/{CONNECTOR_ID}"
    assert capability.id == f"connector-2-{CONNECTOR_ID}"
    assert capability.native is False
    assert capability.local is None


class _RawMCPToolset(AbstractToolset[AgentContext]):
    @property
    def id(self) -> str:
        return "raw-mcp"

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        del ctx
        return {
            "search": ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name="search",
                    parameters_json_schema={"type": "object"},
                    metadata={
                        "meta": {
                            HARNESS_TOOL_METADATA_KEY: {
                                "tool_id": "tool-search",
                                "effects": ["read"],
                                "credential_audiences": ["search_api"],
                                "idempotency": "read_only",
                                "output_policy": {
                                    "max_inline_bytes": 1_024,
                                    "max_output_bytes": 4_096,
                                    "overflow": "spill",
                                    "redact": True,
                                },
                            }
                        }
                    },
                ),
                max_retries=0,
                args_validator=SchemaValidator(core_schema.any_schema()),
            )
        }

    async def call_tool(self, name, tool_args, ctx, tool):
        raise AssertionError("not called")


@pytest.mark.anyio
async def test_worker_promotes_trusted_mcp_metadata_into_harness_managed_metadata() -> None:
    toolset = _ConnectorManagedMetadataToolset(_RawMCPToolset())

    tools = await toolset.get_tools(cast(RunContext[AgentContext], None))

    managed = tools["search"].tool_def.metadata[HARNESS_TOOL_METADATA_KEY]
    assert isinstance(managed, HarnessToolMetadata)
    assert managed.tool_id == "tool-search"
    assert managed.credential_audiences == ("search_api",)
    assert managed.output_policy.max_output_bytes == 4_096
