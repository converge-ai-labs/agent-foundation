"""Configuration used only by the schema migration connection."""

from pydantic import BaseModel, ConfigDict, Field


class MigrationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    advisory_lock_timeout_seconds: float = Field(default=900, gt=0, le=86_400)
    lock_timeout_seconds: float = Field(default=3, gt=0, le=3600)
    statement_timeout_seconds: float = Field(default=900, gt=0, le=86_400)
    idle_transaction_timeout_seconds: float = Field(default=30, gt=0, le=3600)
