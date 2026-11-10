"""Bot inputs and intentional communication, independent of execution receipts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BotModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


BOT_THREAD_ID = "thread_bot"


class BotInput(BotModel):
    input_id: str = Field(min_length=1, max_length=128)
    source: Literal["user", "heartbeat", "connector", "automatic"]
    text: str = Field(max_length=64 * 1024)
    created_at: datetime
    state: Literal["queued", "dispatching", "admitted", "completed", "interrupted", "discarded"] = "queued"
    receipt_id: str | None = None
    continuation_id: str | None = None
    error: str | None = None
    source_id: str | None = None
    causation_id: str | None = None


class BotState(BotModel):
    activated: bool = False
    paused: bool = False
    next_heartbeat_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_error: str | None = None
    heartbeat_interval_seconds: int | None = None


class BotMessage(BotModel):
    message_id: str
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime
    input_id: str | None = None


class BotSubmit(BotModel):
    input_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(min_length=1, max_length=64 * 1024)


class BotPause(BotModel):
    paused: bool


class BotInboxInput(BotModel):
    input_id: str
    source: Literal["connector", "heartbeat"]
    connector_id: str | None = None
    source_event_id: str
    event_type: str
    text: str
    created_at: datetime

    @model_validator(mode="after")
    def consistent_source(self) -> "BotInboxInput":
        if (self.source == "connector") != (self.connector_id is not None):
            raise ValueError("Connector identity must match the input source")
        return self


class BotNote(BotModel):
    thread_id: str
    content: str = ""
    updated_at: datetime | None = None
    last_writer_thread_id: str | None = None
