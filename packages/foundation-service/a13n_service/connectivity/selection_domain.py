"""Typed Run selections and the protected external-tool snapshot."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from a13n_service.connectivity.ingress.domain import JsonObject

ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ExposureMode = Literal["direct", "catalog"]
SourceKind = Literal["connector_connection", "mcp_connection"]
ToolKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]


def _unique_tool_keys(value: tuple[str, ...]) -> tuple[str, ...]:
    if len(value) != len(set(value)):
        raise ValueError("allowed tool keys must be unique")
    return value


AllowedToolKeys = Annotated[
    tuple[ToolKey, ...],
    Field(max_length=2_048),
    AfterValidator(_unique_tool_keys),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectorConnectionRunSelection(StrictModel):
    connector_connection_id: ObjectId
    connector_id: ObjectId
    exposure: ExposureMode
    allowed_tool_keys: AllowedToolKeys
    tool_catalog_digest: Sha256Digest


class MCPConnectionRunSelection(StrictModel):
    mcp_connection_id: ObjectId
    exposure: ExposureMode
    allowed_tool_keys: AllowedToolKeys
    tool_catalog_digest: Sha256Digest


class SnapshotBinding(StrictModel):
    source_kind: SourceKind
    selection_index: int = Field(ge=0, le=511)
    tool_key: ToolKey


class SnapshotTool(StrictModel):
    reference: str = Field(pattern=r"^mcpref_[0-9a-f]{64}$")
    source_alias: str = Field(min_length=1, max_length=128)
    source_tool_name: ToolKey
    model_name: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    description: str = Field(max_length=16_384)
    input_schema: JsonObject
    output_schema: JsonObject | None = None
    annotations: JsonObject = Field(default_factory=dict)
    binding: SnapshotBinding


class MCPToolSnapshot(StrictModel):
    schema_version: Literal["1"] = "1"
    direct_tools: tuple[SnapshotTool, ...] = Field(max_length=2_048)
    catalog_tools: tuple[SnapshotTool, ...] = Field(max_length=2_048)

    @model_validator(mode="after")
    def tools_are_unique_and_partitioned(self) -> MCPToolSnapshot:
        tools = self.direct_tools + self.catalog_tools
        references = tuple(tool.reference for tool in tools)
        model_names = tuple(tool.model_name for tool in self.direct_tools)
        if len(references) != len(set(references)) or len(model_names) != len(set(model_names)):
            raise ValueError("snapshot tool identities must be unique")
        if any(tool.model_name is None for tool in self.direct_tools):
            raise ValueError("direct snapshot tools require model names")
        if any(tool.model_name is not None for tool in self.catalog_tools):
            raise ValueError("catalog snapshot tools cannot expose direct model names")
        return self


__all__ = [
    "ConnectorConnectionRunSelection",
    "ExposureMode",
    "MCPConnectionRunSelection",
    "MCPToolSnapshot",
    "SnapshotBinding",
    "SnapshotTool",
    "SourceKind",
]
