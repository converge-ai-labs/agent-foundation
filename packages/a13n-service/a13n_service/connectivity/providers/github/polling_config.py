"""GitHub user identity and notification reception configuration."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

POLLING_VERSION = "github_notifications_v1"


class GitHubPollingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    api_origin: str = Field(default="https://api.github.com", min_length=1, max_length=2048)
    web_origin: str = Field(default="https://github.com", min_length=1, max_length=2048)
    user_id: int = Field(gt=0)
    poll_interval_seconds: int = Field(default=60, ge=60, le=3600)
    initial_lookback_seconds: int = Field(default=0, ge=0, le=86400)


class GitHubPollingCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    personal_access_token: SecretStr = Field(min_length=1, max_length=4096)


class GitHubReceptionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    allowed_senders: tuple[
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=64,
                pattern=r"^(\*|[A-Za-z0-9][A-Za-z0-9-]{0,38}(\[bot\])?)$",
            ),
        ],
        ...,
    ] = Field(default=("*",), min_length=1, max_length=100)
    event_actions: tuple[str, ...] = Field(default=(), max_length=50)
