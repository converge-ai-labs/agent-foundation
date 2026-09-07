import json
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    AgentSpec,
    DefinitionError,
    HarnessBuilder,
    ModelRecoveryPolicy,
    RunBindings,
    RunError,
)
from a13n_harness.context import AgentContext
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)
from a13n_harness.tools.metadata import HARNESS_TOOL_METADATA_KEY
from a13n_harness.toolsets import FINAL_TOOL_OUTPUT_HARD_CHARS, tool_output_bytes, tool_output_text
from mcp.server.fastmcp import FastMCP
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP, AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RunUsage


def _tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _mcp_model(kind: str) -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        tool = next(tool for tool in info.function_tools if tool.name == "oversized")
        assert tool.kind == "function"
        assert HARNESS_TOOL_METADATA_KEY not in (tool.metadata or {})

        if not _tool_returns(messages):
            yield {
                0: DeltaToolCall(
                    name="oversized",
                    json_args=json.dumps({"kind": kind}),
                    tool_call_id="mcp-call-1",
                )
            }
        else:
            yield "done"

    return FunctionModel(stream_function=stream)


def test_native_mcp_capability_reconstructs_from_agent_spec_and_builds() -> None:
    spec = AgentSpec.from_dict(
        {
            "capabilities": [
                {
                    "MCP": {
                        "url": "https://mcp.example.com/mcp",
                        "id": "example",
                        "native": True,
                        "local": False,
                        "allowed_tools": ["search"],
                        "headers": {},
                        "defer_loading": False,
                    }
                }
            ]
        }
    )

    assert len(spec.capabilities) == 1
    assert spec.capabilities[0].name == "MCP"
    assert spec.capabilities[0].arguments["local"] is False
    schema = AgentSpec.model_json_schema_with_capabilities()
    capability_variants = schema["properties"]["capabilities"]["items"]["anyOf"]
    assert {"$ref": "#/$defs/spec_MCP"} in capability_variants
    assert not any("MCPToolset" in key for key in schema["$defs"])

    executable = HarnessBuilder().build(
        spec,
        output_type=str,
        model=FunctionModel(lambda messages, info: "unused"),
    )
    assert executable.definition.agent.capabilities == spec.capabilities
    leaves: list[Any] = []
    executable._agent.root_capability.apply(leaves.append)
    capability = next(item for item in leaves if isinstance(item, MCP))
    assert capability.id == "example"
    assert capability.url == "https://mcp.example.com/mcp"


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["text", "json"])
async def test_local_mcp_oversized_results_use_unmanaged_truncation(kind: str) -> None:
    server = FastMCP("oversized-results")

    @server.tool()
    def oversized(kind: str) -> Any:
        if kind == "text":
            return "x" * 300_000
        return {"kind": "json", "content": "y" * 300_000}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_mcp_model(kind),
        capabilities=(MCP(local=server, id="oversized-results"),),
    )

    result = await executable.run("Call the MCP tool", bindings=RunBindings.embedded())

    assert result.status == "completed"
    returns = _tool_returns(list(result.all_messages()))
    assert len(returns) == 1
    content = returns[0].content
    assert isinstance(content, dict)
    assert content["truncated"] is True
    assert content["output_file_path"] is None
    assert content["output_chars"] > FINAL_TOOL_OUTPUT_HARD_CHARS
    assert content["output_bytes"] > 256 * 1024
    assert len(tool_output_text(content)) <= FINAL_TOOL_OUTPUT_HARD_CHARS
    assert len(tool_output_bytes(content)) <= 256 * 1024


class _MCPContextDeps:
    def __init__(self, run_id: str = "run-1") -> None:
        self.run_id = run_id
        self.thread_id = "thread-1"
        self.instance = AgentInstanceContext(
            identity=AgentIdentityRef(
                issuer="test",
                subject="workload",
                user_id="user-1",
                tenant_id="tenant-1",
            ),
            agent_instance_id="instance-1",
            parent_agent_instance_id="parent-1",
            delegation_id="delegation-1",
            actor="actor-1",
        )
        self.metadata = {
            "text": "direct",
            "number": 7,
            "enabled": True,
            "object": {"z": [True, False], "a": 1},
            "items": ["one", 2],
            "absent": None,
        }
        self._capabilities: dict[str, AbstractCapability[AgentContext]] = {}

    def _run_capability(self, capability_id: str) -> AbstractCapability[AgentContext] | None:
        return self._capabilities.get(capability_id)

    def _record_run_capability(
        self,
        capability_id: str,
        capability: AbstractCapability[AgentContext],
    ) -> None:
        self._capabilities[capability_id] = capability


def _run_context(deps: _MCPContextDeps) -> RunContext[AgentContext]:
    return RunContext(
        deps=cast(AgentContext, deps),
        model=FunctionModel(lambda messages, info: "unused"),
        usage=RunUsage(),
    )


def test_mcp_context_headers_resolve_exact_sources_and_canonical_json() -> None:
    resolver = MCPContextHeaders(
        MCPContextHeadersConfig(
            headers={
                "X-Issuer": MCPContextHeaderBinding("identity.issuer"),
                "X-User": MCPContextHeaderBinding("identity.user_id"),
                "X-Instance": MCPContextHeaderBinding("instance.agent_instance_id"),
                "X-Run": MCPContextHeaderBinding("context.run_id"),
                "X-Text": MCPContextHeaderBinding("context.metadata.text"),
                "X-Number": MCPContextHeaderBinding("context.metadata.number"),
                "X-Enabled": MCPContextHeaderBinding("context.metadata.enabled"),
                "X-Object": MCPContextHeaderBinding("context.metadata.object"),
                "X-Items": MCPContextHeaderBinding("context.metadata.items"),
                "X-Optional": MCPContextHeaderBinding("context.metadata.absent", required=False),
            }
        )
    )

    headers = resolver(cast(AgentContext, _MCPContextDeps()))

    assert headers == {
        "X-Issuer": "test",
        "X-User": "user-1",
        "X-Instance": "instance-1",
        "X-Run": "run-1",
        "X-Text": "direct",
        "X-Number": "7",
        "X-Enabled": "true",
        "X-Object": '{"a":1,"z":[true,false]}',
        "X-Items": '["one",2]',
    }


def test_mcp_context_headers_report_required_missing_values() -> None:
    resolver = MCPContextHeaders(
        MCPContextHeadersConfig(
            headers={"X-Missing": MCPContextHeaderBinding("identity.agent_id")},
        )
    )

    with pytest.raises(RunError) as error:
        resolver(cast(AgentContext, _MCPContextDeps()))

    assert error.value.code == "mcp_context_header_missing"
    assert error.value.details == {
        "header": "X-Missing",
        "source": "identity.agent_id",
    }


@pytest.mark.parametrize("source", ["identity", "context.metadata", "context.unknown", "metadata.value"])
def test_mcp_context_header_bindings_reject_unsupported_sources(source: str) -> None:
    with pytest.raises(DefinitionError) as error:
        MCPContextHeaderBinding(source)

    assert error.value.code == "mcp_context_header_invalid"


def test_mcp_context_header_config_rejects_case_insensitive_duplicates() -> None:
    with pytest.raises(DefinitionError) as error:
        MCPContextHeadersConfig(
            headers={
                "X-Tenant": MCPContextHeaderBinding("identity.tenant_id"),
                "x-tenant": MCPContextHeaderBinding("identity.user_id"),
            }
        )

    assert error.value.code == "mcp_context_header_conflict"


@pytest.mark.anyio
async def test_contextual_mcp_builds_and_reuses_one_fresh_upstream_capability_per_logical_run() -> None:
    calls: list[str] = []

    async def headers_factory(context: AgentContext) -> dict[str, str]:
        calls.append(context.run_id)
        return {"X-Run": context.run_id}

    capability = ContextualMCP(
        "https://mcp.example.com/mcp",
        id="context-server",
        headers_factory=headers_factory,
        native=True,
        local=False,
        headers={"X-Static": "static"},
        allowed_tools=["search"],
    )
    first_deps = _MCPContextDeps("run-1")

    assert capability.get_native_tools() == []
    assert capability.get_toolset() is None
    first = await capability.for_run(_run_context(first_deps))
    recovered = await capability.for_run(_run_context(first_deps))
    second = await capability.for_run(_run_context(_MCPContextDeps("run-2")))

    assert first is recovered
    assert first is not second
    assert calls == ["run-1", "run-2"]
    assert set(first_deps._capabilities) == {"a13n.mcp.context.context-server"}
    assert isinstance(first, MCP)
    native = first.get_native_tools()[0]
    assert native.headers == {"X-Static": "static", "X-Run": "run-1"}
    assert native.allowed_tools == ["search"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"url": "mcp.example.com/mcp", "native": True, "local": False},
        {"url": "https://mcp.example.com/mcp", "native": False, "local": False},
    ],
)
def test_contextual_mcp_rejects_invalid_static_recipes(arguments: dict[str, object]) -> None:
    with pytest.raises(DefinitionError) as error:
        ContextualMCP(
            id="context-server",
            headers_factory=lambda context: {"X-Run": context.run_id},
            **arguments,  # type: ignore[arg-type]
        )

    assert error.value.code == "mcp_definition_invalid"


def test_contextual_mcp_rejects_ids_containing_colons() -> None:
    with pytest.raises(DefinitionError) as error:
        ContextualMCP(
            "https://mcp.example.com/mcp",
            id="context:server",
            headers_factory=lambda context: {"X-Run": context.run_id},
        )

    assert error.value.code == "mcp_definition_invalid"


@pytest.mark.anyio
async def test_contextual_mcp_factory_runs_once_across_harness_model_recovery() -> None:
    factory_calls: list[str] = []
    model_calls = 0

    def headers_factory(context: AgentContext) -> dict[str, str]:
        factory_calls.append(context.run_id)
        return {"X-Run": context.run_id}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal model_calls
        del messages
        model_calls += 1
        native = info.model_request_parameters.native_tools[0]
        assert native.headers == {"X-Run": factory_calls[0]}
        if model_calls == 1:
            yield "partial"
            raise RuntimeError("stream disconnected")
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            ContextualMCP(
                "https://mcp.example.com/mcp",
                id="context-server",
                headers_factory=headers_factory,
                native=True,
                local=False,
            ),
        ),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )

    result = await executable.run("test", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert model_calls == 2
    assert len(factory_calls) == 1


@pytest.mark.anyio
async def test_contextual_mcp_replacement_is_reextracted_by_pydantic_ai() -> None:
    def model(messages, info):
        del messages
        native = info.model_request_parameters.native_tools[0]
        assert native.headers == {"X-Run": "run-1"}
        return ModelResponse(parts=[TextPart(content="done")])

    capability = ContextualMCP(
        "https://mcp.example.com/mcp",
        id="context-server",
        headers_factory=lambda context: {"X-Run": context.run_id},
        native=True,
        local=False,
    )
    agent = PydanticAgent(
        FunctionModel(model),
        deps_type=AgentContext,
        capabilities=(capability,),
    )

    result = await agent.run("test", deps=cast(AgentContext, _MCPContextDeps()))

    assert result.output == "done"


@pytest.mark.anyio
async def test_contextual_mcp_constructs_local_transport_after_header_resolution() -> None:
    capability = ContextualMCP(
        "https://mcp.example.com/mcp",
        id="context-server",
        headers_factory=lambda context: {"X-Run": context.run_id},
        native=False,
        local=True,
    )

    active = await capability.for_run(_run_context(_MCPContextDeps()))

    assert isinstance(active, MCP)
    assert active.headers == {"X-Run": "run-1"}
    assert active.local is not None
    assert active.get_toolset() is not None


@pytest.mark.anyio
async def test_contextual_mcp_rejects_static_and_resolved_header_conflicts() -> None:
    capability = ContextualMCP(
        "https://mcp.example.com/mcp",
        id="context-server",
        headers_factory=lambda context: {"x-tenant": context.instance.identity.require_claim("tenant_id")},
        native=True,
        local=False,
        headers={"X-Tenant": "static"},
    )

    with pytest.raises(DefinitionError) as error:
        await capability.for_run(_run_context(_MCPContextDeps()))

    assert error.value.code == "mcp_context_header_conflict"
