"""Small presentation references and immutable original MCP App payloads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_harness_ui.storage.contracts import AppReference as AppReference
from a13n_harness_ui.storage.objects import ObjectRef


class AppModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AppResource(AppModel):
    schema_version: Literal["1"] = "1"
    uri: str
    html: str = Field(max_length=8 * 1024 * 1024)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AppSnapshot(AppModel):
    schema_version: Literal["1"] = "1"
    connection_generation: str
    app_id: str
    thread_id: str
    run_id: str
    tool_call_id: str
    server_id: str
    tool: dict[str, JsonValue]
    arguments: dict[str, JsonValue]
    result: dict[str, JsonValue]
    resource: ObjectRef | None = None
    unavailable: str | None = None


class AppPresentation(AppModel):
    reference: AppReference
    snapshot: AppSnapshot
    resource: AppResource | None
    sandbox_url: str | None = None
    connected: bool = False
