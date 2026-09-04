"""Shared typed messaging policy and default input mapping."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class MessagingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interaction_mode: Literal["mention", "discussion", "chat"]
    reply_mode: Literal["auto", "thread", "main"]
