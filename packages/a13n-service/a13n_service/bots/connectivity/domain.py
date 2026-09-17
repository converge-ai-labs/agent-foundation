"""Public Bot verification snapshots; each result belongs to one credential generation."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from a13n_service.connectivity.inspection import ConversationInfo, InstallationInfo
from a13n_service.connectivity.providers.common.messaging import MessagingPolicy
from a13n_service.connectivity.providers.github.polling_config import GitHubReceptionPolicy


class BotSetup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    reception_mode: Literal["webhook", "polling"] = "webhook"
    poll_checked_at: datetime | None = None
    poll_error_code: str | None = None
    event_path: str | None
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
    policy: MessagingPolicy | GitHubReceptionPolicy


class DiscoverFeishuInstallationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: str = Field(min_length=1, max_length=256)
    app_secret: SecretStr = Field(min_length=1, max_length=4096)


class DiscoverGitHubUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    personal_access_token: SecretStr = Field(min_length=1, max_length=4096)
