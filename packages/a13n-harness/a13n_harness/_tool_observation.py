"""Owner-reported operation outcomes on the existing Pydantic tool span."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from re import fullmatch
from typing import TYPE_CHECKING, Any, Literal

from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, Instrumentation

from a13n_harness._trace_details import record_span_metadata

if TYPE_CHECKING:
    from pydantic_ai.capabilities import ValidatedToolArgs, WrapToolExecuteHandler
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import RunContext, ToolDefinition


@dataclass
class _ToolObservation:
    span: Span
    failure: dict[str, str | bool] | None = None
    outcome_unknown: bool = False


_current_tool: ContextVar[_ToolObservation | None] = ContextVar("a13n_harness_tool_observation", default=None)


def _active_tool() -> _ToolObservation | None:
    observation = _current_tool.get()
    if observation is not None:
        span = observation.span
        if span.is_recording() and trace.get_current_span().get_span_context() == span.get_span_context():
            return observation
    return None


def set_tool_span_attributes(attributes: Mapping[str, str]) -> None:
    """Best-effort enrichment of the exact active Harness-selected tool span.

    Trusted owners supply only safe, bounded structural attributes, never payloads.
    This does not create spans or infer execution outcomes.
    """
    try:
        if (observation := _active_tool()) is not None:
            observation.span.set_attributes(attributes)
    except Exception:
        pass


def record_tool_outcome_unknown() -> None:
    """Report an owner-classified uncertain outcome without failing or retrying the call."""
    try:
        if (observation := _active_tool()) is not None:
            observation.outcome_unknown = True
    except Exception:
        pass


def record_tool_operation_failure(
    code: str,
    *,
    reason: object = None,
    stage: Literal["preparation", "execution"] = "execution",
) -> None:
    """Called only by trusted error projectors, never by inspecting business results."""
    try:
        observation = _active_tool()
        if observation is None or observation.failure is not None:
            return
        values: dict[str, str | bool] = {"tool.failure.stage": stage}
        # Classifications only: do not copy exception text, details, paths or hints.
        if fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            values["tool.failure.code"] = code
        if isinstance(reason, str) and fullmatch(r"[a-z][a-z0-9_]{0,63}", reason):
            values["tool.failure.reason"] = reason
        observation.failure = values
    except Exception:
        pass


class _ToolObservationCapability(AbstractCapability[Any]):
    """Scope enrichment inside native instrumentation, including nested ToolManager calls."""

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(wrapped_by=(Instrumentation,))

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        observation = None
        try:
            span = trace.get_current_span()
            if span.is_recording() and span.get_span_context().is_valid:
                observation = _ToolObservation(span)
        except Exception:
            pass
        token = _current_tool.set(observation)
        try:
            result = await handler(args)
            if observation is not None:
                try:
                    failure = observation.failure
                    status = (
                        "operation_failed"
                        if failure
                        else "outcome_unknown"
                        if observation.outcome_unknown
                        else "returned"
                    )
                    record_span_metadata(
                        observation.span,
                        {"tool.result.status": status, **(failure or {})},
                    )
                    if failure:
                        observation.span.set_status(StatusCode.ERROR)
                except Exception:
                    pass
            return result
        finally:
            # Native instrumentation retains exceptions, retries, deferrals and cancellation.
            _current_tool.reset(token)
