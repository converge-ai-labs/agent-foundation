"""Trusted submission metadata supplied by internal entry points."""

from dataclasses import dataclass

from a13n_service.memory.bots.binding import BotMemoryBinding

from .domain import JsonObject


@dataclass(frozen=True, slots=True)
class SubmissionOrigin:
    trigger_type: str = "user_input"
    native_tool_contexts: tuple[JsonObject, ...] = ()
    bot_memory: BotMemoryBinding | None = None
