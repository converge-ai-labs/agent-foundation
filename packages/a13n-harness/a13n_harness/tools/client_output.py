"""Native output-tool declarations for values supplied by an external client."""

from hashlib import sha256
from typing import Any

from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.output import ToolOutput

from a13n_harness._json import dump_json_bytes
from a13n_harness.output_schema import structured_output_type

CLIENT_OUTPUT_PREFIX = "client-output:"


class ClientOutputRequested(Exception):
    """Transfer a validated output selection to the Harness suspension boundary."""

    def __init__(self, call: ToolCallPart, identity: str) -> None:
        super().__init__(call.tool_name)
        self.call = call
        self.identity = identity


class ClientOutputTool(ToolOutput[JsonValue]):
    identity: str


def client_output_tool(name: str, schema: dict[str, Any], description: str | None = None) -> ClientOutputTool:
    if schema.get("type") != "object":
        raise ValueError("Client output tool parameters must use an object schema")
    parameters = structured_output_type(schema)
    identity = CLIENT_OUTPUT_PREFIX + name + ":" + sha256(dump_json_bytes(schema, sort_keys=True)).hexdigest()

    async def suspend(ctx: RunContext[Any], arguments: dict[str, Any]) -> JsonValue:
        assert ctx.tool_call_id is not None
        raise ClientOutputRequested(ToolCallPart(name, arguments, ctx.tool_call_id), identity)

    suspend.__annotations__["arguments"] = parameters
    tool = ClientOutputTool(suspend, name=name, description=description)
    tool.identity = identity
    return tool


def client_output_identities(spec: Any) -> frozenset[str]:
    if isinstance(spec, ClientOutputTool):
        return frozenset({spec.identity})
    if isinstance(spec, tuple | list):
        return frozenset(identity for item in spec for identity in client_output_identities(item))
    return frozenset()
