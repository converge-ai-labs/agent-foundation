import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.tools.metadata import HARNESS_TOOL_METADATA_KEY
from a13n_harness.toolsets import FINAL_TOOL_OUTPUT_HARD_CHARS, tool_output_bytes, tool_output_text
from mcp.server.fastmcp import FastMCP
from pydantic_ai.capabilities import MCP
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel


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

    async with executable:
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
