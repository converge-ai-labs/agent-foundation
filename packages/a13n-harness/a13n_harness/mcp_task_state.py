"""Durable evidence for an externally owned MCP task."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue


class McpTaskInputRequest(BaseModel):
    """A pending input request. Its contents are untrusted server data."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    mode: Literal["form", "url"]
    message: str
    schema_: dict[str, JsonValue] | None = Field(default=None, alias="schema")
    url: str | None = None
    expires_at: AwareDatetime | None = None
    elicitation_id: str | None = None


class McpTaskExecutionState(BaseModel):
    """A handle scoped to a Host connection, without credentials or executable state."""

    model_config = ConfigDict(frozen=True)

    connection_id: str
    server_id: str
    tool_name: str
    mcp_task_id: str
    status: Literal["working", "input_required", "completed", "failed", "cancelled"] = "working"
    status_message: str | None = None
    poll_interval_seconds: float = Field(default=1, gt=0, allow_inf_nan=False)
    next_poll_at: AwareDatetime | None = None
    expires_at: AwareDatetime
    input_required: dict[str, McpTaskInputRequest] | None = None

    @property
    def due_at(self) -> datetime:
        """Wake parked tasks only for expiry; cancellation is scheduled separately."""
        if self.status == "input_required" or self.next_poll_at is None:
            return self.expires_at
        return min(self.next_poll_at, self.expires_at)
