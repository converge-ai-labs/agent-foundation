"""Public Bot verification snapshots; each result belongs to one credential generation."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from a13n_service.connectivity.providers.common.messaging import MessagingPolicy

from .observations import ConversationInfo, InstallationInfo


class BotSetup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    event_path: str
    event_url: str | None


class BotCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    credential_generation: int = Field(ge=1)
    checked_at: datetime
    conversation_id: str | None = None
    installation: InstallationInfo | None = None
    conversation: ConversationInfo | None = None
    error_code: str | None = None


class BotCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_version: int = Field(ge=1)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)


class BotCheckHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    latest: BotCheck | None


class ActivateBotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_version: int = Field(ge=1)
    target_id: str = Field(min_length=1, max_length=128)
    target_version: int = Field(ge=1)
    conversation_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    execution_service_account_id: str = Field(min_length=1, max_length=128)
    policy: MessagingPolicy
