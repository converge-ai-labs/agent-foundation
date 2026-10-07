"""Response provenance for direct calls and nested local tool dispatch."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelResponse, ToolCallPart

from a13n_harness.capabilities.tool_review import ToolCallSource
from a13n_harness.context import AgentContext

_SOURCE: ContextVar[tuple[AgentContext, ToolCallSource | None] | None] = ContextVar("tool_call_source", default=None)


def tool_call_source(ctx: RunContext[AgentContext]) -> ToolCallSource | None:
    """Match the issuing response, never a Run's most recent unrelated response."""
    if ctx.tool_call_id:
        for message in reversed(ctx.messages):
            if isinstance(message, ModelResponse) and any(
                isinstance(part, ToolCallPart)
                and part.tool_call_id == ctx.tool_call_id
                and part.tool_name == ctx.tool_name
                for part in message.parts
            ):
                try:
                    return ToolCallSource(
                        model_name=message.model_name,
                        provider_name=message.provider_name,
                        provider_response_id=message.provider_response_id,
                    )
                except ValidationError:
                    # An oversized provider identity is unavailable, not truncated or invented.
                    return None
    inherited = _SOURCE.get()
    return inherited[1] if inherited is not None and inherited[0] is ctx.deps else None


@contextmanager
def tool_call_source_scope(ctx: RunContext[AgentContext]) -> Iterator[None]:
    token = _SOURCE.set((ctx.deps, tool_call_source(ctx)))
    try:
        yield
    finally:
        _SOURCE.reset(token)
