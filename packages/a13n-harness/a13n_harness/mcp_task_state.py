"""Durable evidence for an externally owned MCP task."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from a13n_harness.mcp_inputs import McpTaskInputRequest as McpTaskInputRequest


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
    answered_inputs: frozenset[str] = frozenset()
    input_required: dict[str, McpTaskInputRequest] | None = None

    @property
    def due_at(self) -> datetime:
        """Wake parked tasks only for expiry; cancellation is scheduled separately."""
        if self.next_poll_at is None:
            return self.expires_at
        return min(self.next_poll_at, self.expires_at)
