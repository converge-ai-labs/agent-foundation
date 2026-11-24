"""Run an outbound ACP turn inside the regular Harness Run middleware lifecycle."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from a13n_harness.acp import (
    AcpChannel,
    AcpError,
    AcpRunBinding,
    AcpSessionRuntime,
    AcpSessionSetup,
    AcpUpdate,
    PromptBlock,
    SteeringOutcome,
)
from a13n_harness.content import content_items
from a13n_harness.context import AgentContext
from a13n_harness.events import input_events
from a13n_harness.plugins import AbstractHarnessPlugin, PluginRunExchange, PluginRunNext
from a13n_harness.result import HarnessRunResult
from a13n_harness.toolsets.interaction import AskUserQuestionRequest, UserQuestion
from a13n_stream_protocol.acp import AcpRunStream
from acp import schema
from pydantic import BaseModel
from pydantic_ai import BinaryContent, RunContext, TextContent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import CapabilityEvent, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset
from pydantic_ai.usage import RunUsage

from a13n_harness_ui.acp_inputs import AcpInputs
from a13n_harness_ui.acp_sessions import AcpSessions
from a13n_harness_ui.configuration.models import AcpAgentResource
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.goal import GoalCapability
from a13n_harness_ui.mcp_runtime.inputs import Inputs


class _SessionState(BaseModel):
    digest: str
    session_id: str


_NAMESPACE = "a13n.acp"


@dataclass(kw_only=True)
class AcpDisplayEvent(CapabilityEvent, namespace="a13n.acp", name="display", dispatch="immediate"):
    events: tuple[dict[str, Any], ...]


@dataclass
class AcpTurn(AbstractHarnessPlugin):
    """One admitted Run; the session owner keeps connections separate from Run policy."""

    sessions: AcpSessions
    resource: AcpAgentResource
    digest: str
    setup: AcpSessionSetup
    emit: Callable[[dict[str, Any]], None] | None = None
    capabilities: Sequence[AbstractCapability[AgentContext]] = ()
    toolsets: Sequence[AbstractToolset[AgentContext]] = ()
    inputs: Inputs | None = None
    interaction_timeout_seconds: float | None = None
    ask_user_enabled: bool = True
    channel: AcpChannel | None = None
    bind_environment: Callable[[AgentContext], AcpRunBinding] = lambda _: AcpRunBinding()
    goal: GoalCapability | None = None
    bot: bool = False
    runtime: AcpSessionRuntime | None = None

    @property
    def plugin_id(self) -> str:
        return _NAMESPACE

    async def steer(self, text: str) -> SteeringOutcome:
        if self.runtime is None:
            return "not_delivered"
        return await self.runtime.steer(text)

    async def stop(self) -> bool:
        return self.runtime is not None and await self.runtime.cancel()

    async def wrap_run(
        self, exchange: PluginRunExchange, call_next: PluginRunNext[Any]
    ) -> HarnessRunResult[str | None]:
        context = exchange.context
        if exchange.input.value is None:
            raise AcpError("acp_input_required", "An external ACP turn requires a prompt.")
        previous = await exchange.export_current_state()
        saved = await context.state.read(_NAMESPACE, _SessionState, version="1")
        saved_id = saved.session_id if saved is not None and saved.digest == self.digest else None
        pending: list[dict[str, Any]] = []
        transcript = AcpRunStream(context.thread_id, context.run_id, self.emit or pending.append)

        async def publish() -> None:
            if pending:
                events = tuple(
                    event for event in pending if event["type"] not in {"RUN_STARTED", "RUN_FINISHED", "RUN_ERROR"}
                )
                pending.clear()
                if events:
                    await context.events.emit(AcpDisplayEvent(events=events))

        async with self.sessions.lease(
            thread_id=context.thread_id,
            digest=self.digest,
            resource=self.resource,
            cwd=self.setup.cwd,
            saved_session_id=saved_id,
            channel=self.channel,
            interactive=self.inputs is not None and not context.unattended,
        ) as (runtime, bridge):
            blocks: list[PromptBlock] = []
            authored = []
            prompt_capabilities = runtime.capabilities.prompt_capabilities or schema.PromptCapabilities()
            for item in content_items(exchange.input.value):
                value = item.value
                if isinstance(value, str):
                    block = schema.TextContentBlock(text=value)
                elif isinstance(value, TextContent):
                    block = schema.TextContentBlock(text=value.content)
                elif isinstance(value, BinaryContent) and value.is_image and prompt_capabilities.image:
                    block = schema.ImageContentBlock(data=b64encode(value.data).decode(), mime_type=value.media_type)
                elif isinstance(value, BinaryContent) and value.is_audio and prompt_capabilities.audio:
                    block = schema.AudioContentBlock(data=b64encode(value.data).decode(), mime_type=value.media_type)
                else:
                    raise AcpError("acp_input_unsupported", "The external Agent cannot accept this attachment.")
                blocks.append(block)
                if item.metadata.display:
                    authored.append(value)
            # The external agent runs its own model loop. This RunContext only
            # binds Host capabilities and never invokes a native model.
            run_context = RunContext(deps=context, model=None, usage=RunUsage(), run_id=context.run_id)
            capabilities = self.capabilities
            toolsets = self.toolsets
            binding = self.bind_environment(context)
            if self.inputs is not None and not context.unattended:

                def require_active() -> None:
                    if runtime.client.binding is not binding:
                        raise HarnessUiError("The ACP prompt is unavailable.", code="mcp_input_stale")

                interactions = AcpInputs(
                    self.inputs,
                    context.thread_id,
                    self.resource.id,
                    require_active,
                    context.run_id,
                    timeout_seconds=self.interaction_timeout_seconds,
                )
                binding.permission = interactions.permission
                binding.elicitation = interactions.elicitation
                if self.ask_user_enabled:
                    questions_toolset: FunctionToolset[AgentContext] = FunctionToolset()

                    @questions_toolset.tool
                    async def ask_user_question(
                        ctx: RunContext[AgentContext], questions: tuple[UserQuestion, ...]
                    ) -> dict[str, Any]:
                        """Ask the user one to four structured questions while this prompt is active."""
                        request = AskUserQuestionRequest(questions=questions)
                        anchor = ctx.tool_call_id if ctx.tool_call_id != context.run_id else None
                        result = await interactions.request(request, anchor)
                        return result.content if result.action == "accept" else {"action": result.action}

                    toolsets = (*toolsets, questions_toolset)
            if self.bot:
                bot_tools: FunctionToolset[AgentContext] = FunctionToolset()

                @bot_tools.tool_plain
                def finish_silently() -> None:
                    """Finish without sending a direct Bot reply."""
                    transcript.reset_reply()

                toolsets = (*toolsets, bot_tools)
            async with bridge.prepare(run_context, capabilities=capabilities, toolsets=toolsets) as scope:
                if runtime.session_id is None:
                    await runtime.open_session(
                        AcpSessionSetup(
                            cwd=self.setup.cwd,
                            instructions=(*self.setup.instructions, *(await scope.instructions())),
                            mcp_servers=self.setup.mcp_servers,
                            additional_directories=self.setup.additional_directories,
                        ),
                        saved_session_id=saved_id,
                        discovery=scope,
                    )
                session_id = runtime.session_id
                assert session_id is not None
                await context.state.write(
                    _NAMESPACE, _SessionState(digest=self.digest, session_id=session_id), version="1"
                )
                binding.host_tools = scope
                compacted = False

                async def observe(update: AcpUpdate) -> None:
                    nonlocal compacted
                    compacted |= isinstance(update, schema.SessionUpdateCompactionUpdate)
                    transcript.update(update)
                    await publish()

                binding.on_update = observe
                for event in input_events(exchange.input.value, source="user", input_id=context.run_id):
                    await context.events.emit(event)
                transcript.start()
                self.runtime = runtime

                async def prompt_until_settled() -> schema.PromptResponse:
                    nonlocal compacted
                    prompt = blocks
                    while True:
                        compacted = False
                        guidance = self.goal.get_instructions() if self.goal is not None else None
                        hidden = await scope.prompt_context()
                        if guidance is not None:
                            hidden = (*hidden, guidance)
                        response = await runtime.prompt(prompt, binding=binding, context=hidden)
                        if response.stop_reason == "cancelled" or self.goal is None:
                            return response
                        if compacted:
                            await self.goal.observe_restore(run_context, "acp_compaction")
                        continuation: list[str] = []
                        await self.goal.after_reply(run_context, transcript.last_text, continuation.append)
                        if not continuation:
                            return response
                        transcript.reset_reply()
                        prompt = [schema.TextContentBlock(text=continuation[0])]

                try:
                    response = (
                        await prompt_until_settled()
                        if self.goal is None
                        else await self.goal.wrap_run(run_context, handler=prompt_until_settled)
                    )
                except BaseException:
                    transcript.finish(error="External ACP turn did not complete.")
                    await publish()
                    raise
                finally:
                    self.runtime = None
                transcript.finish()
                await publish()
                cancelled = response.stop_reason == "cancelled"
                output = transcript.last_text or None
                history = previous.message_history
                messages = (*history, ModelRequest(parts=[UserPromptPart(content=authored)], run_id=context.run_id))
                if output:
                    messages = (*messages, ModelResponse(parts=[TextPart(content=output)], run_id=context.run_id))
                return HarnessRunResult(
                    thread_id=context.thread_id,
                    run_id=context.run_id,
                    status="cancelled" if cancelled else "completed",
                    output=None if cancelled else output,
                    state=await context.export_state(messages),
                    usage=context.usage_attribution.summary(),
                    _messages=messages,
                    _new_message_index=len(history),
                )
