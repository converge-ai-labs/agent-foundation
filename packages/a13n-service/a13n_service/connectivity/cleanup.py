"""One-shot remote cleanup evidence after local invalidation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ConnectionCleanupReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connection_id: str
    local_status: Literal["pending", "ready", "action_required", "disabled", "deleted"]
    remote_status: Literal["not_required", "succeeded", "failed", "unknown"]


def cleanup_receipt(connection) -> ConnectionCleanupReceipt:
    return ConnectionCleanupReceipt(
        connection_id=connection.id,
        local_status="deleted" if connection.deleted_at is not None else connection.status,
        remote_status=connection.remote_cleanup_status or "unknown",
    )
