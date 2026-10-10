"""Duplex native realtime execution over the shared Harness Run lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, ValidatedToolArgs, WrapperCapability
from pydantic_ai.capabilities.abstract import WrapToolExecuteHandler
from pydantic_ai.exceptions import ModelRetry, RunCancelled, ToolFailed, UsageLimitExceeded, UserError
from pydantic_ai.messages import (
    AgentStreamEvent,
    EnqueuedMessagesEvent,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    SpeechPart,
    SpeechPartDelta,
    ToolCallPart,
    ToolReturn,
)
from pydantic_ai.realtime import (
    AudioRetention,
    RealtimeModel,
    RealtimeModelSettings,
    RealtimeSession,
    RealtimeSessionInput,
)
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDefinition, ToolDenied
from pydantic_ai.usage import RunUsage, UsageLimits

from a13n_harness._run_stream import HarnessRunStream
from a13n_harness._usage_pricing import _cost_source, price_response
from a13n_harness.content import ContentItem, ContentMetadata, native_content
from a13n_harness.context import AgentContext, RunBindings
from a13n_harness.environment.sources import EnvironmentEntry
from a13n_harness.errors import PluginError, RunError
from a13n_harness.events import enqueued_input_events
from a13n_harness.model_context import (
    ModelContextInputOrigin,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
    _project_model_context,
)
from a13n_harness.plugins import PluginRunExchange
from a13n_harness.result import HarnessRunResult
from a13n_harness.state import HarnessState
from a13n_harness.tools._output import _render_tool_return
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, _stable_id

if TYPE_CHECKING:
    from a13n_harness.execution import ExecutableAgent


def _semantic_event(event: Any) -> Any | None:
    """Keep media on native playback, never in the Harness observation channel."""
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, SpeechPartDelta):
        delta = replace(event.delta, audio_chunk=None)
        if delta.transcript is None and delta.transcript_delta is None:
            return None
        return replace(event, delta=delta)
    if isinstance(event, (PartStartEvent, PartEndEvent)) and isinstance(event.part, SpeechPart):
        return replace(event, part=replace(event.part, audio=None))
    return event


class _LiveCapability(AbstractCapability[AgentContext]):
    def __init__(self, stream: HarnessLiveStream) -> None:
        self.stream = stream

    def get_instructions(self):
        return self.instructions

    async def instructions(self, ctx: RunContext[AgentContext]) -> str:
        self.stream._native_context = ctx
        manager = ctx.tool_manager
        assert manager is not None and manager.root_capability is not None
        # Decorate only the prepared native tool dispatcher, not the Agent's
        # registered capability tree. This stateless adapter adds no lifecycle,
        # tools, or ordering requirements; run-step managers retain the wrapper.
        if not isinstance(manager.root_capability, _LiveDeferredResults):
            manager.root_capability = _LiveDeferredResults(manager.root_capability, self)
        if self.stream._cancel_event.is_set():
            ctx.cancel()
        projection = await _project_model_context(
            ctx,
            ModelContextProjectionRequest(
                kind=ModelContextRequestKind.INPUT,
                input_origin=ModelContextInputOrigin.USER,
            ),
        )
        return "\n\n".join(block.content for block in projection.blocks)

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        result = await handler(args)
        if ctx.run_id != self.stream.run_id:
            return result
        return await self._with_context(ctx, call.tool_call_id, result)

    async def _with_context(self, ctx: RunContext[AgentContext], call_id: str, result: Any) -> Any:
        projection = await _project_model_context(
            ctx,
            ModelContextProjectionRequest(
                kind=ModelContextRequestKind.TOOL_RESULTS,
                tool_call_ids=(call_id,),
            ),
        )
        if not projection.blocks:
            return result
        extra = native_content(
            [
                ContentItem(block.content, ContentMetadata(display=False, source_id=block.source_id))
                for block in projection.blocks
            ]
        )
        assert isinstance(extra, list)
        # Native realtime sends supplemental content together with the tool result,
        # before automatic continuation. Result events occur too late for this.
        if isinstance(result, ToolReturn):
            previous = [result.content] if isinstance(result.content, str) else list(result.content or ())
            return replace(result, content=[*previous, *extra])
        return ToolReturn(return_value=result, content=extra)

    async def on_event(self, ctx: RunContext[AgentContext], *, event: Any) -> None:
        if ctx.run_id == self.stream.run_id and (semantic := _semantic_event(event)) is not None:
            self.stream._emitter.observe(semantic)


class _LiveDeferredResults(WrapperCapability[AgentContext]):
    """Adapt resolved external values before native normalization and wire send."""

    def __init__(self, wrapped: AbstractCapability[AgentContext], bridge: _LiveCapability) -> None:
        super().__init__(wrapped)
        self.bridge = bridge

    async def handle_deferred_tool_calls(
        self, ctx: RunContext[AgentContext], *, requests: DeferredToolRequests
    ) -> DeferredToolResults | None:
        results = await self.wrapped.handle_deferred_tool_calls(ctx, requests=requests)
        if results is not None and ctx.run_id == self.bridge.stream.run_id:
            for call in requests.calls:
                if call.tool_call_id not in results.calls:
                    continue
                result = results.calls[call.tool_call_id]
                # Denials and retries are control signals, not successful values.
                if isinstance(result, (ToolDenied, ModelRetry, RetryPromptPart, ToolFailed)):
                    continue
                if isinstance(result, ToolReturn):
                    result = _render_tool_return(result)
                results.calls[call.tool_call_id] = await self.bridge._with_context(ctx, call.tool_call_id, result)
        return results


class HarnessLiveStream(HarnessRunStream[None]):
    """One Live Run with a semantic iterator and a separate native audio view.

    Entering starts connection preparation. Consume events concurrently with
    sending/playing media. ``close`` ends the session normally; consume the final
    HarnessRunResultEvent to receive the post-cleanup result. Exiting early still
    closes resources and retains a checkpoint, but is not a completion receipt.
    """

    def __init__(
        self,
        *,
        executable: ExecutableAgent[Any],
        model: RealtimeModel,
        model_settings: RealtimeModelSettings | None = None,
        environment: EnvironmentEntry | None = None,
        environments: Mapping[str, EnvironmentEntry] | None = None,
        default_environment: str | None = None,
        bindings: RunBindings | None = None,
        previous_state: HarnessState | None = None,
        audio_retention: AudioRetention = "transcript_only",
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> None:
        if not isinstance(model, RealtimeModel):
            raise TypeError("live model must be a native RealtimeModel instance")
        if audio_retention not in {"transcript_only", "input_audio", "output_audio", "all"}:
            raise ValueError("audio_retention must be a native AudioRetention value")
        super().__init__(
            executable=executable,
            environment=environment,
            environments=environments,
            default_environment=default_environment,
            bindings=bindings,
            previous_state=previous_state,
            tool_recovery="never",
            usage=usage,
            usage_limits=usage_limits,
        )
        self._live_model = model
        self._live_model_settings = deepcopy(model_settings)
        self._audio_retention: AudioRetention = audio_retention
        self._result_output_adapter = TypeAdapter(type(None))
        self._session: RealtimeSession | None = None
        self._native_context: RunContext[AgentContext] | None = None
        self._session_ready = asyncio.Event()
        self._response_records: dict[int, ModelUsageRecord] = {}
        self._usage_refusal: UsageLimitExceeded | None = None
        if self._bindings.model_call_check is not None:
            raise RunError(
                "Live does not support per-request Host model reservations.",
                code="live_model_check_unsupported",
            )

    async def __aenter__(self) -> HarnessLiveStream:
        await super().__aenter__()
        try:
            self._start_logical_event_mux()
            execution = self._execution_task
            assert execution is not None
            ready = asyncio.create_task(self._session_ready.wait())
            try:
                await asyncio.wait((ready, execution), return_when=asyncio.FIRST_COMPLETED)
                if execution.done() and not self._session_ready.is_set():
                    self._execution_task = None
                    execution.result()
                    raise RunError("Live execution ended before connecting.", code="live_session_unavailable")
            finally:
                ready.cancel()
                await asyncio.gather(ready, return_exceptions=True)
            return self
        except BaseException as exc:
            await super().__aexit__(type(exc), exc, exc.__traceback__)
            raise

    def _active_session(self) -> RealtimeSession:
        if self._session is None or self._closed:
            raise RunError("The Live session is not active.", code="run_not_active")
        return self._session

    async def send(
        self,
        content: RealtimeSessionInput | Sequence[RealtimeSessionInput],
        *,
        respond: bool | None = None,
    ) -> None:
        await self._active_session().send(content, respond=respond)

    async def send_audio(self, data: bytes | AsyncIterable[bytes]) -> None:
        await self._active_session().send_audio(data)

    def stream_audio(self) -> AsyncIterator[bytes]:
        return self._active_session().stream_audio()

    async def commit_audio(self) -> None:
        await self._active_session().commit_audio()

    async def clear_audio(self) -> None:
        await self._active_session().clear_audio()

    async def create_response(self) -> None:
        await self._active_session().create_response()

    async def interrupt(self, *, played_ms: int | None = None, played_bytes: int | None = None) -> bool | None:
        """Interrupt speech without undoing tools; native playback accounting applies."""
        session = self._active_session()
        if played_bytes is None:
            return await session.interrupt(played_ms=played_ms)
        if played_ms is not None:
            raise UserError("interrupt() accepts either played_ms or played_bytes, not both.")
        return await session.interrupt(played_bytes=played_bytes)

    async def close(self) -> None:
        """End the native session normally; terminal delivery follows event consumption."""
        if self._session is not None:
            await self._session.close()

    def cancel(self) -> None:
        super().cancel()
        if not self._closed and self._native_context is not None:
            self._native_context.cancel()

    @property
    def native_usage(self) -> RunUsage:
        """Detached native accumulator, not an attributed Run accounting total.

        Includes session-only usage, caller baseline and any work sharing the
        native accumulator. Do not add it to ``usage`` or use it as a receipt.
        """
        return deepcopy(self._usage)

    def _refresh_live_messages(self) -> None:
        if self._session is not None:
            self._latest_messages = tuple(self._session.all_messages())

    def _capture_usage(self, *, check_limits: bool = True) -> None:
        """Reconcile finalized response snapshots; native usage is already accumulated."""
        if self._session is None:
            return
        ledger = self.context.usage_attribution
        assert ledger.cost_capability is not None
        responses = (message for message in self._session.new_messages() if isinstance(message, ModelResponse))
        for index, response in enumerate(responses):
            previous = self._response_records.get(index)
            value = deepcopy(response)
            pricing = price_response(
                value,
                capability=ledger.cost_capability,
                model_id=None,
                request_started_at=value.timestamp,
            )
            known = value.usage.has_values() or value.usage.cost is not None
            call_id = previous.call_id if previous else _stable_id("call", self.run_id, str(index))
            assert call_id is not None
            record = ModelUsageRecord(
                record_id=previous.record_id if previous else _stable_id("model", self.run_id, call_id),
                run_id=ledger.run_id,
                response_ordinal=previous.response_ordinal if previous else ledger.next_model_ordinal(),
                call_id=call_id,
                model_run_id=self.run_id,
                provider_response_id=value.provider_response_id,
                agent_instance_id=ledger.instance.agent_instance_id,
                parent_agent_instance_id=ledger.instance.parent_agent_instance_id,
                delegation_id=ledger.instance.delegation_id,
                source="live",
                response_state=value.state,
                model_name=value.model_name or self._live_model.model_name,
                provider_name=value.provider_name or self._live_model.system,
                response_timestamp=value.timestamp,
                request_usage=BoundedRequestUsage.from_request_usage(value.usage),
                usage_status="unavailable" if not known else "complete" if value.state == "complete" else "partial",
                outcome="completed" if value.state == "complete" else "cancelled",
                pricing_revision=pricing.revision,
                pricing_rule_id=pricing.rule_id,
                pricing_status=pricing.status,
                cost_source=_cost_source(value, pricing) if value.usage.cost is not None else "unknown",
            )
            if record == previous:
                continue
            # Checkpoint capture may defer raising, but cannot consume a refusal.
            self._usage_refusal = ledger.finish(call_id, record) or self._usage_refusal
            self._response_records[index] = record
        if check_limits and self._usage_refusal is not None:
            raise self._usage_refusal

    async def export_state(self) -> HarnessState:
        if not self._closed:
            self._refresh_live_messages()
            self._capture_usage(check_limits=False)
        return await super().export_state()

    async def _run_native(self, exchange: PluginRunExchange) -> HarnessRunResult[None]:
        if exchange.context is not self.context:
            raise PluginError("Plugin replaced the trusted Live context.", code="plugin_context_replaced")
        initial_input = exchange.input.value
        if initial_input is not None and not isinstance(initial_input, str):
            raise RunError("Live plugin input must be text.", code="live_input_unsupported")
        self.context.usage_attribution.model_usage_coverage = "responses_only"
        self._latest_messages = await self.context._storage.resolve_messages(self._initial_history)
        bridge = _LiveCapability(self)
        status = "completed"
        try:
            async with self._executable._agent.realtime(
                self._live_model,
                deps=self.context,
                model_settings=self._live_model_settings,
                instructions=self._executable._system_prompt,
                capabilities=(bridge, *self._bindings.capabilities),
                usage=self._usage,
                usage_limits=self._usage_limits,
                run_id=self.run_id,
                conversation_id=self.thread_id,
                message_history=list(self._latest_messages),
            ).session(audio_retention=self._audio_retention) as session:
                self._session = session
                self._session_ready.set()
                if initial_input is not None:
                    await session.send(initial_input)
                async for event in session:
                    if isinstance(event, EnqueuedMessagesEvent):
                        await self.context._steering.mark_applied(event.enqueue_id)
                    self._refresh_live_messages()
                    self._capture_usage()
                    await self.context.usage_attribution._flush(reason="model_request")
                    if (semantic := _semantic_event(event)) is not None:
                        await self._emitter._put(self._adapt_event(cast(AgentStreamEvent, semantic)))
                    if isinstance(event, EnqueuedMessagesEvent):
                        for observed in enqueued_input_events(event):
                            await self._emitter._put(self._adapt_event(observed))
        except RunCancelled:
            status = "cancelled"
        except UsageLimitExceeded:
            status = "failed"
        finally:
            # Native context exit settles interrupted tool returns and speech.
            # Capture the settled state before constructing any terminal result.
            self._refresh_live_messages()
            self._capture_usage(check_limits=False)
        if status == "failed" or (status == "completed" and self._usage_refusal is not None):
            return await self._failed_candidate(code="usage_limit_exceeded", message="Run usage limit exceeded.")
        return self._record_inner_candidate(
            HarnessRunResult(
                thread_id=self.thread_id,
                run_id=self.run_id,
                status=status,
                output=None,
                state=await self.context.export_state(self._latest_messages),
                usage=self.usage,
                _messages=self._latest_messages,
                _new_message_index=self._new_message_index,
            )
        )


__all__ = ["HarnessLiveStream"]
