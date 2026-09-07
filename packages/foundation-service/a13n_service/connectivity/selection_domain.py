"""Declared and accepted connection selections, independent of live tool definitions."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from a13n_service.ids import ObjectId

ToolKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]


def _unique_tool_keys(value: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if value is not None and len(value) != len(set(value)):
        raise ValueError("tool names must be unique")
    return value


ToolSelection = Annotated[tuple[ToolKey, ...] | None, Field(max_length=2048), AfterValidator(_unique_tool_keys)]


class ConnectorConnectionToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connector_connection_id: ObjectId
    tools: ToolSelection = None
    defer_loading: bool = False


class MCPConnectionToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mcp_connection_id: ObjectId
    tools: ToolSelection = None
    defer_loading: bool = False


class ConnectorConnectionRunSelection(ConnectorConnectionToolSelection):
    connector_provider_id: ObjectId


MCPConnectionRunSelection = MCPConnectionToolSelection
