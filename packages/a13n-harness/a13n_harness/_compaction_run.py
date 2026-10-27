"""Host-triggered compaction within the shared logical Run lifecycle."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    NativeToolCallPart,
    TextPart,
    ToolCallPart,
    _merge_consecutive_messages,
)
from pydantic_ai.models import ModelRequestContext

from a13n_harness._context_history import _COMPACTION_PROMPT
from a13n_harness._handoff import _HANDOFF_STATE_VERSION, HANDOFF_CAPABILITY_ID, _HandoffState
from a13n_harness._run_stream import HarnessRunStream
from a13n_harness.capabilities.context import _restore_compacted_state
from a13n_harness.compaction import (
    COMPACTION_CAPABILITY_ID,
    CompactionBasis,
    CompactionResult,
    _prefix_digest,
    _prepare_compaction,
)
from a13n_harness.context import AgentContext
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.errors import DefinitionError, HarnessError, PluginError
from a13n_harness.events import ContextOperationFailedPayload, ContextOperationStartedPayload, emit_harness_event
from a13n_harness.model_context import _requires_exact_history
from a13n_harness.plugins import PluginRunExchange
from a13n_harness.result import HarnessRunResult
from a13n_harness.tools.invocation import disabled_tool_execution
from a13n_harness.usage import UsageCapability


@dataclass
class _SummaryRequestCapability(AbstractCapability[AgentContext]):
    """Reject prefix-changing preparation before the single summary dispatch."""

    history: list[ModelMessage]
    context: RunContext[AgentContext] | None = None
    request: ModelRequestContext | None = None

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost", wrapped_by=(UsageCapability,))

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        prefix = request_context.messages[: len(self.history)]
        # Native preparation extends an existing trailing request in place.
        # Its original parts remain the prefix; run metadata can be refreshed.
        if (
            self.history
            and isinstance(self.history[-1], ModelRequest)
            and prefix
            and isinstance(prefix[-1], ModelRequest)
        ):
            prefix[-1] = replace(self.history[-1], parts=prefix[-1].parts[: len(self.history[-1].parts)])
        if prefix != self.history:
            raise DefinitionError(
                "Compaction preparation changed the historical prefix.", code="compaction_prefix_changed"
            )
        parameters = request_context.model_request_parameters
        if not parameters.allow_text_output or parameters.output_mode in ("native", "prompted"):
            raise DefinitionError(
                "Cache-friendly compaction requires an Agent that already permits text output.",
                code="compaction_text_output_required",
            )
        self.context = ctx
        self.request = request_context
        return request_context


class _CompactionRunStream(HarnessRunStream[str]):
    """Reuse Run preparation/cleanup while replacing only native execution."""

    def __init__(self, *, instruction: str | None, **kwargs: Any) -> None:
        if instruction is not None and not isinstance(instruction, str):
            raise TypeError("instruction must be a string or None")
        super().__init__(**kwargs)
        self._instruction = instruction
        self._result_output_adapter = TypeAdapter(str)

    async def _create_context(self, environment: BoundEnvironment) -> AgentContext:
        return replace(await super()._create_context(environment), _compacting=True)

    async def _run_native(self, exchange: PluginRunExchange) -> HarnessRunResult[str]:
        if exchange.context is not self.context or exchange.input.value is not None:
            raise PluginError(
                "Compaction middleware must preserve its context and empty input.", code="plugin_input_invalid"
            )
        history = list(await self.context._storage.resolve_messages(self._initial_history))
        self._latest_messages = tuple(history)
        self._new_message_index = len(history)
        if not history or not any(isinstance(message, ModelRequest) for message in history):
            return await self._failed_candidate(
                code="compaction_history_empty", message="Compaction requires conversation history."
            )
        handoff = await self.context.state.read(HANDOFF_CAPABILITY_ID, _HandoffState, version=_HANDOFF_STATE_VERSION)
        if (
            _requires_exact_history(history)
            or self.context._tool_executions.history_calls
            or (handoff is not None and handoff.summary is not None)
        ):
            return await self._failed_candidate(
                code="compaction_boundary_pending", message="Compaction requires a settled history boundary."
            )

        operation_id = f"compaction-{uuid4().hex}"
        await emit_harness_event(
            self.context.events,
            kind="context",
            payload=ContextOperationStartedPayload(
                type="compaction_started",
                operation_id=operation_id,
            ),
        )
        guidance = _COMPACTION_PROMPT if self._instruction is None else self._instruction
        prompt = (
            "Summarize only the existing conversation history. Return only the summary text. "
            "Do not call tools, investigate unresolved questions, or continue the task. "
            "Record uncertainties for the resumed Agent.\n\n" + guidance
        )
        capture = _SummaryRequestCapability(_merge_consecutive_messages(deepcopy(history)))
        phase = "summary"
        try:
            with disabled_tool_execution():
                async with self._executable._agent.iter(
                    prompt,
                    message_history=deepcopy(history),
                    deps=self.context,
                    conversation_id=self.thread_id,
                    usage=self._usage,
                    usage_limits=self._usage_limits,
                    capabilities=(*self._bindings.capabilities, capture),
                ) as run:
                    requested = False
                    async for node in run:
                        if Agent.is_model_request_node(node):
                            if requested:
                                raise DefinitionError(
                                    "Compaction requested another model turn.", code="compaction_summary_invalid"
                                )
                            requested = True
                            # Consume the native stream for provider compatibility, but publish
                            # the summary only through compaction observations, not user input/output.
                            async with node.stream(run.ctx) as events:
                                async for _ in events:
                                    pass
                        elif Agent.is_call_tools_node(node):
                            response = node.model_response
                            if any(isinstance(part, (ToolCallPart, NativeToolCallPart)) for part in response.parts):
                                raise DefinitionError(
                                    "Compaction returned tool calls.", code="compaction_summary_invalid"
                                )
                            summary = "\n\n".join(
                                part.content for part in response.parts if isinstance(part, TextPart)
                            ).strip()
                            if not summary:
                                raise DefinitionError(
                                    "Compaction returned an empty summary.", code="compaction_summary_invalid"
                                )
                            assert capture.context is not None and capture.request is not None
                            # Commit a resumable pending-restoration boundary before notifying
                            # listeners. Failure during restoration can safely resume next Run.
                            result = CompactionResult(
                                basis=CompactionBasis(
                                    operation_id=operation_id,
                                    thread_id=self.thread_id,
                                    prefix_length=len(history),
                                    prefix_digest=_prefix_digest(history),
                                    replay_json="[]",
                                ),
                                summary=summary,
                            )
                            messages, marker = _prepare_compaction(
                                result, thread_id=self.thread_id, messages=history, previous=None
                            )
                            assert marker is not None
                            await self.context.state.write(COMPACTION_CAPABILITY_ID, marker, version="1")
                            self._latest_messages = tuple(messages)
                            self._new_message_index = 0
                            capture.context.messages[:] = messages
                            phase = "restoration"
                            restored = await _restore_compacted_state(capture.context, capture.request)
                            self._latest_messages = tuple(restored.messages)
                            state = await self.context.export_state(self._latest_messages)
                            return self._record_inner_candidate(
                                HarnessRunResult(
                                    thread_id=self.thread_id,
                                    run_id=self.run_id,
                                    status="completed",
                                    output=summary,
                                    state=state,
                                    usage=self.usage,
                                    _messages=self._latest_messages,
                                    _new_message_index=0,
                                )
                            )
                    raise DefinitionError("Compaction produced no model response.", code="compaction_summary_invalid")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._diagnostic_error = exc
            code = exc.code if isinstance(exc, HarnessError) else "compaction_failed"
            await emit_harness_event(
                self.context.events,
                kind="context",
                payload=ContextOperationFailedPayload(
                    type="compaction_failed",
                    operation_id=operation_id,
                    failed_phase=phase,
                    error_code=code,
                    retryable=False,
                ),
            )
            return await self._failed_candidate(code=code, message="Explicit compaction failed.")
