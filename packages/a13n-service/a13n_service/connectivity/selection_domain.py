"""Declared and accepted connection selections, independent of live tool definitions."""

from typing import Annotated, Literal

from a13n_harness.tools import ToolPermissionSetting
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from a13n_service.ids import ObjectId

from .naming import CONNECTION_ALIAS_LIMIT

ToolKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]


def _unique_tool_keys(value: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if value is not None and len(value) != len(set(value)):
        raise ValueError("tool names must be unique")
    return value


ToolSelection = Annotated[tuple[ToolKey, ...] | None, Field(max_length=2048), AfterValidator(_unique_tool_keys)]


class ConnectionToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connection_id: ObjectId
    tools: ToolSelection = None
    defer_loading: bool = False
    permission: ToolPermissionSetting = "inherit"
    permissions: dict[ToolKey, ToolPermissionSetting] = Field(default_factory=dict, max_length=2048)

    @model_validator(mode="after")
    def validate_permissions(self) -> "ConnectionToolSelection":
        if self.tools is not None and not self.permissions.keys() <= set(self.tools):
            raise ValueError("connection permissions must name selected tools")
        return self


class ConnectionRunSelection(ConnectionToolSelection):
    kind: Literal["connector", "mcp"]
    model_alias: str = Field(min_length=6, max_length=CONNECTION_ALIAS_LIMIT, pattern=r"^conn_[a-z][a-z0-9_]*$")
    authorization_generation: int = Field(ge=1)
    connector_provider_id: ObjectId | None = None

    @model_validator(mode="after")
    def validate_provider(self) -> "ConnectionRunSelection":
        if (self.kind == "connector") != (self.connector_provider_id is not None):
            raise ValueError("Only connector selections require a provider")
        return self
