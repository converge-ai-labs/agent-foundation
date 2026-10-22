"""Tools served by a connector provider: its runtime over the host transport, tool discovery and the toolset.

The Harness defines each connector (Composio) and its account operations; this module only adapts them to
the host HTTP client and to Pydantic AI tools.
"""

import hashlib
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager

from a13n_harness import AgentContext
from a13n_harness.providers.connector.bounds import DISCOVERY_MAX_PAGES, DISCOVERY_MAX_TOOLS
from a13n_harness.providers.connector.contracts import (
    ConnectorConnectionRuntime,
    ConnectorProviderError,
    ConnectorProviderRuntime,
    ToolCatalog,
)
from a13n_harness.providers.connector.definition import ConnectorProviderDefinition
from a13n_harness.providers.connector.http import ConnectorHttpClient
from a13n_harness.providers.connector.tool_errors import rejected_tool_outcome
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import JsonValue
from pydantic_ai import RunContext, Tool
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

from a13n_service.infra.outbound import open_http
from a13n_service.providers.tools import CheckedToolset, DispatchCheck, ToolInfo

_UNKNOWN_OUTCOME = "The action's outcome is unknown; check the external state before calling it again."


@asynccontextmanager
async def open_connector(
    definition: ConnectorProviderDefinition,
    config: Mapping[str, JsonValue],
    credential: JsonValue,
    *,
    policy: EndpointPolicy,
    timeout: float,
    max_bytes: int,
) -> AsyncGenerator[ConnectorProviderRuntime]:
    async with open_http(policy, timeout=timeout, max_bytes=max_bytes) as client:
        http = ConnectorHttpClient(client, policy, response_max_bytes=max_bytes, timeout_seconds=timeout)
        async with definition.open(config, credential, http=http) as runtime:
            yield runtime


async def list_connector_tools(catalog: ToolCatalog | ConnectorConnectionRuntime) -> list[ToolInfo]:
    """Every page of the catalogue; a catalogue beyond the discovery bounds fails instead of truncating."""
    tools: list[ToolInfo] = []
    cursor: str | None = None
    for _ in range(DISCOVERY_MAX_PAGES):
        page = await catalog.discover_tools(cursor=cursor)
        tools.extend(
            ToolInfo(
                name=tool.key,
                description=tool.description,
                input_schema=tool.input_schema,
                output_schema=tool.output_schema,
                annotations=tool.annotations,
                provider_version=tool.provider_version,
            )
            for tool in page.items
        )
        if len(tools) > DISCOVERY_MAX_TOOLS:
            break
        cursor = page.next_cursor
        if cursor is None:
            return tools
    raise ConnectorProviderError("directory_too_large")


def connector_toolset(
    connection_id: str,
    provider_id: str,
    account: ConnectorConnectionRuntime,
    tools: Sequence[ToolInfo],
    *,
    check: DispatchCheck,
    timeout: float,
) -> AbstractToolset[AgentContext]:
    functions = [_tool(connection_id, account, tool) for tool in tools]
    return CheckedToolset(
        FunctionToolset[AgentContext](functions, max_retries=0, timeout=timeout, id=connection_id),
        connection_id,
        provider_id,
        check,
    )


def _tool(connection_id: str, account: ConnectorConnectionRuntime, tool: ToolInfo) -> Tool[AgentContext]:
    if tool.provider_version is None:
        raise ValueError("Connector tools execute at their catalogue version")
    version = tool.provider_version

    async def call(ctx: RunContext[AgentContext], **arguments: JsonValue) -> JsonValue:
        # Stable across retries of one call, so the provider can deduplicate a resent request.
        request_id = hashlib.sha256(f"{connection_id}:{ctx.deps.run_id}:{ctx.tool_call_id}".encode()).hexdigest()
        try:
            outcome = await account.execute_tool(
                tool_key=tool.name, provider_version=version, arguments=arguments, request_id=request_id
            )
        except ConnectorProviderError as error:
            rejected = rejected_tool_outcome(error, request_id=request_id)
            if rejected is None or rejected.error is None:
                raise ToolFailed(
                    _UNKNOWN_OUTCOME if error.outcome_unknown else "The provider did not run the action."
                ) from None
            raise ToolFailed(rejected.error.message) from None
        if outcome.kind == "succeeded":
            return outcome.result
        if outcome.kind == "failed" and outcome.error is not None:
            raise ToolFailed(outcome.error.message)
        raise ToolFailed(_UNKNOWN_OUTCOME)

    return Tool.from_schema(
        call, name=tool.name, description=tool.description, json_schema=tool.input_schema, takes_ctx=True
    )
