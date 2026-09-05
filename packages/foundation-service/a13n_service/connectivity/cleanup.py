"""One-shot remote cleanup evidence after local invalidation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ConnectionCleanupReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connection_id: str
    local_status: Literal["disabled", "deleted"]
    remote_status: Literal["not_required", "succeeded", "failed", "unknown"]
