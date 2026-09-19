"""A recoverable, multi-turn application over the Agent Harness stream."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, aclosing
from pathlib import Path

from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessEvent,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessState,
)
from a13n_harness.providers.environment.management import Environment
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.models import Model

_DEFAULT_INSTRUCTIONS = "Respond clearly and remember relevant details from earlier turns."


class ConversationApplication:
    """Run one serialized conversation and persist its portable Harness state."""

    def __init__(
        self,
        *,
        model: Model,
        state_path: Path,
        environment_factory: Callable[[], Environment],
        instructions: str = _DEFAULT_INSTRUCTIONS,
    ) -> None:
        self._state_path = state_path
        self._environment_factory = environment_factory
        self._turn_lock = asyncio.Lock()
        self._executable = HarnessBuilder(configured_plugins_enabled=False).build(
            AgentSpec(instructions=instructions),
            output_type=str,
            model=model,
        )

    @property
    def state_path(self) -> Path:
        return self._state_path

    async def load_state(self) -> HarnessState | None:
        """Load the last completed turn, if this conversation has one."""

        try:
            payload = await asyncio.to_thread(self._state_path.read_text, encoding="utf-8")
        except FileNotFoundError:
            return None
        return HarnessState.model_validate_json(payload)

    def stream_turn(self, prompt: str) -> AbstractAsyncContextManager[AsyncGenerator[str]]:
        """Return an explicitly scoped stream for one serialized turn."""

        return aclosing(self._stream_turn(prompt))

    async def _stream_turn(self, prompt: str) -> AsyncGenerator[str]:
        async with self._turn_lock:
            previous_state = await self.load_state()
            terminal_result: HarnessRunResult[str] | None = None

            async with self._executable.stream(
                prompt,
                environment=self._environment_factory(),
                previous_state=previous_state,
            ) as stream:
                async for item in stream:
                    if isinstance(item, HarnessRunResultEvent):
                        terminal_result = item.result
                        continue
                    if isinstance(item, HarnessEvent) and item.run_id == stream.run_id:
                        text = _event_text(item)
                        if text:
                            yield text

            if terminal_result is None:
                raise RuntimeError("Harness stream ended without a terminal result")
            terminal_result.raise_for_status()
            if terminal_result.state is None:
                raise RuntimeError("Completed Harness run did not return continuation state")
            await self._save_state(terminal_result.state)

    async def _save_state(self, state: HarnessState) -> None:
        payload = state.model_dump_json(indent=2)
        await asyncio.to_thread(_atomic_write_text, self._state_path, payload)


def _event_text(item: HarnessEvent) -> str | None:
    event = item.event
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta
    return None


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        temporary_path.write_text(payload, encoding="utf-8")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


__all__ = ["ConversationApplication"]
