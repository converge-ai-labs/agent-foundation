"""Bounded provider observations; discovery does not grant conversation access."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InstallationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    app_id: str = Field(min_length=1, max_length=256)
    organization_id: str = Field(min_length=1, max_length=256)
    organization_name: str = Field(min_length=1, max_length=256)
    bot_id: str = Field(min_length=1, max_length=256)
    bot_name: str = Field(min_length=1, max_length=256)
    enabled: bool
    enterprise_id: str | None = Field(default=None, min_length=1, max_length=256)


class ConversationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=256)
    organization_id: str | None = Field(default=None, min_length=1, max_length=256)
    audience: Literal["public", "private", "direct", "unknown"]
    is_member: bool | None
    is_active: bool | None
    external: bool | None


class ConversationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=256)


class ConversationPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    items: tuple[ConversationCandidate, ...] = Field(max_length=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
