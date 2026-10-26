"""Small public references, private saved originals, and App bridge requests."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.providers.tools.mcp import ToolName
from a13n_service.resources.connections.schemas import ConnectionSelection


class AppReference(BaseModel):
    app_id: str
    connection_id: str
    agent_revision_id: str
    source_run_id: str
    tool_call_id: str
    tool_name: str
    resource_uri: str
    unavailable: bool = False


class AppOriginal(BaseModel):
    reference: AppReference
    tool: dict[str, JsonValue]
    arguments: dict[str, JsonValue]
    result: dict[str, JsonValue]


class AppBinding(BaseModel):
    """Private source and authorization facts committed with the original's object reference."""

    reference: AppReference
    run_id: str
    # Private authorization facts; never returned to the browser.
    connection_url: str
    selection: ConnectionSelection


class SavedApp(AppOriginal, AppBinding):
    """An original combined with its separately committed authorization binding."""


class AppResource(BaseModel):
    uri: str
    mime_type: str
    html: str
    metadata: dict[str, JsonValue]


class OpenedApp(AppOriginal):
    resource: AppResource


class AppToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: ToolName
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class AppResourceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uri: str = Field(min_length=1, max_length=2048)


class AppToolResult(BaseModel):
    # The unmodified MCP CallToolResult, including content, structuredContent and _meta.
    result: dict[str, JsonValue]
