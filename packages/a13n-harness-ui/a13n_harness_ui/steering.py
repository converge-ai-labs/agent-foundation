"""Host steering policy for tools that must finish in the foreground."""

from typing import Any

from a13n_harness import HarnessRunStream
from a13n_harness.errors import RunError
from a13n_harness.input import RunInputValue


async def steer_stream(stream: HarnessRunStream[Any], message: RunInputValue, *, immediate: bool) -> str:
    """Prefer immediate delivery, queue when an active tool cannot hand off."""
    try:
        return await stream.steer(message, immediate=immediate)
    except RunError as exc:
        if not immediate or exc.code != "tool_handoff_unsupported":
            raise
        # Harness rejects this specific condition before accepting input. Ordinary
        # enqueue preserves the running tool and delivers once at its next boundary.
        return await stream.steer(message, immediate=False)
