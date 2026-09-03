"""Shared typed messaging policy and default input mapping."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from a13n_service.connectivity.ingress.domain import JsonObject


class MessagingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interaction_mode: Literal["mention", "discussion", "chat"]
    reply_mode: Literal["auto", "thread", "main"]


def default_message_mapping() -> JsonObject:
    return {
        "op": "object",
        "fields": {
            "schema_version": {"op": "static", "value": "2"},
            "content": {"op": "static", "value": []},
            "structured_content": {
                "op": "object",
                "fields": {"events": {"op": "select", "path": ["events"]}},
            },
        },
    }
