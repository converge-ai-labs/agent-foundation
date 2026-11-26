"""Agent Client Protocol v1 adapter over the Harness UI App."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Any

from a13n_harness.context import AgentContext
from a13n_harness.events import TaskChangedPayload, UsageReportPayload
from a13n_harness.models.codex import CodexRequestModel
from a13n_stream_protocol import CustomEventAssembler
from acp import Client, RequestError
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    ClientCapabilities,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PlanEntry,
    PromptResponse,
    ResourceContentBlock,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionResumeCapabilities,
    SessionUpdateCompactionUpdate,
    SetSessionConfigOptionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
)
from pydantic_ai import DeferredToolRequests, DeferredToolResults, RunContext, ToolFailed
from pydantic_ai.capabilities import AbstractCapability, HandleDeferredToolCalls
from pydantic_ai.messages import AgentStreamEvent, PartEndEvent, ThinkingPart, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.tools import ToolApproved, ToolDenied

from a13n_harness_ui.app import HarnessUiApp
from a13n_harness_ui.errors import HarnessUiError, LivePresentationError
from a13n_harness_ui.live import LiveEvent, LiveSubscription
from a13n_harness_ui.model_thinking import describe_thinking
from a13n_harness_ui.root_inputs import StrictRootInputs
from a13n_harness_ui.session_mcp import PROFILE, SessionMcpServer, select_session_mcp
from a13n_harness_ui.surfaces import (
    NewThreadDefaults,
    RootOperationStatus,
    RootOperationView,
    RootRunReceipt,
    RunModelOverrides,
)


def _profile(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    profile = kwargs.get(PROFILE)
    if profile is not None and not isinstance(profile, dict):
        raise RequestError.invalid_params({"message": "Invalid a13n request metadata."})
    return profile


class _PromptCancelled(Exception):
    """The ACP turn was cancelled before the App registered an operation."""


@dataclass(slots=True)
class _Approval:
    call: ToolCallPart
    answer: asyncio.Future[ToolApproved | ToolDenied]


@dataclass(slots=True)
class _Turn:
    turn_id: str | None
    receipt: RootRunReceipt | None = None
    cancel_requested: bool = False
    stopping: asyncio.Task[None] | None = None
    notifications: asyncio.Queue[AgentThoughtChunk | str | _Approval] = field(default_factory=asyncio.Queue)
    plan: dict[str, PlanEntry] = field(default_factory=dict)
    compactions: dict[str, str] = field(default_factory=dict)
    custom_events: CustomEventAssembler = field(default_factory=CustomEventAssembler)
    run_id: str | None = None
    tool_calls: dict[str, str] = field(default_factory=dict)
    open_calls: set[str] = field(default_factory=set)
    approvals: set[asyncio.Future[ToolApproved | ToolDenied]] = field(default_factory=set)

    def cancel(self) -> None:
        self.cancel_requested = True
        for answer in self.approvals:
            if not answer.done():
                answer.set_result(ToolDenied())


def _client_supports_compaction(capabilities: object) -> bool:
    if isinstance(capabilities, ClientCapabilities):
        return capabilities.session is not None and capabilities.session.compaction is not None
    if isinstance(capabilities, dict):
        session = capabilities.get("session")
        return isinstance(session, dict) and session.get("compaction") is not None
    return False


class AcpAgent:
    def __init__(self, app: HarnessUiApp, *, unattended: bool = False) -> None:
        self.app = app
        self.unattended = unattended
        self.client: Client
        self.initialized = False
        self.client_supports_compaction = False
        self.disconnected = False
        self.resuming: set[str] = set()
        self.sessions: dict[str, tuple[SessionMcpServer, ...]] = {}
        self.turns: dict[str, _Turn] = {}
        self.model_overrides: dict[str, RunModelOverrides] = {}
        self.omitted_servers: dict[str, list[dict[str, Any]]] = {}
        self.tool_exclusions: dict[str, dict[str, str]] = {}

    def on_connect(self, conn: Client) -> None:
        self.client = conn

    def disconnect(self) -> None:
        self.disconnected = True
        # Only the App owns Runs. Stop waiting for their settlement in the
        # transport when no peer remains to receive the result.
        for turn in self.turns.values():
            turn.cancel()
            if turn.stopping is not None:
                turn.stopping.cancel()

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        self.initialized = True
        self.client_supports_compaction = _client_supports_compaction(client_capabilities)
        return InitializeResponse(
            field_meta={PROFILE: {"version": 1, "unattended": self.unattended}},
            protocol_version=1,
            agent_info=Implementation(name="a13n-harness-ui", title="Harness UI", version=version("a13n-harness-ui")),
            agent_capabilities=AgentCapabilities(
                session_capabilities=SessionCapabilities(resume=SessionResumeCapabilities())
            ),
        )

    def _require_initialized(self) -> None:
        if not self.initialized:
            raise RequestError.invalid_request({"message": "Call initialize first."})

    def _require_session(self, session_id: str) -> None:
        self._require_initialized()
        if session_id not in self.sessions:
            raise RequestError.invalid_params({"message": "Start or resume this session on the connection first."})

    async def new_session(
        self, cwd: str, mcp_servers: Sequence[object] | None = None, **kwargs: Any
    ) -> NewSessionResponse:
        self._require_initialized()
        directory = _cwd(cwd)
        servers, meta = select_session_mcp(directory, mcp_servers, _profile(kwargs), unattended=self.unattended)
        try:
            project_id = await self.app.ensure_cwd_project(directory)
            thread = await self.app.create_thread(defaults=NewThreadDefaults(project_id=project_id))
        except HarnessUiError as exc:
            raise _app_error(exc) from exc
        self.sessions[thread.thread_id] = servers
        self.omitted_servers[thread.thread_id] = meta[PROFILE]["omittedMcpServers"] if meta else []
        return NewSessionResponse(
            session_id=thread.thread_id, field_meta=meta, config_options=[*await self._options(thread.thread_id)]
        )

    async def resume_session(
        self, session_id: str, cwd: str, mcp_servers: Sequence[object] | None = None, **kwargs: Any
    ) -> ResumeSessionResponse:
        self._require_initialized()
        directory = _cwd(cwd)
        servers, meta = select_session_mcp(directory, mcp_servers, _profile(kwargs), unattended=self.unattended)
        if session_id in self.turns or session_id in self.resuming:
            raise RequestError.invalid_request({"message": "The session already has an open turn or resume request."})
        self.resuming.add(session_id)
        try:
            detail = await self.app.get_thread(session_id)
            thread = detail.thread
            if (
                thread.role != "ordinary"
                or thread.coordinator_thread_id is not None
                or thread.parent_thread_id is not None
                or thread.archived
                or thread.memory_scope is not None
                or thread.configuration.agent_source.kind != "agent"
            ):
                raise RequestError.invalid_params({"message": "Only active ordinary root Threads can be resumed."})
            if await self.app.active_root_operation(session_id) is not None:
                raise RequestError.invalid_request({"message": "The Thread already has an active run."})
            roots = thread.configuration.local_roots
            if not roots or Path(roots[0]).resolve() != directory:
                raise RequestError.invalid_params({"message": "Use the first saved local root as cwd when resuming."})
            if detail.deferred_requests:
                raise RequestError.invalid_request(
                    {"message": "Resolve the Thread's pending decisions before resuming."}
                )
            override = self.model_overrides.pop(session_id, None)
            try:
                options = await self._options(session_id)
            except BaseException:
                if override is not None:
                    self.model_overrides[session_id] = override
                raise
            self.sessions[session_id] = servers
            self.omitted_servers[session_id] = meta[PROFILE]["omittedMcpServers"] if meta else []
            self.tool_exclusions.pop(session_id, None)
            return ResumeSessionResponse(field_meta=meta, config_options=[*options])
        except HarnessUiError as exc:
            raise _app_error(exc) from exc
        finally:
            self.resuming.remove(session_id)

    async def prompt(self, session_id: str, prompt: Sequence[object], **kwargs: Any) -> PromptResponse:
        self._require_session(session_id)
        parts = _prompt_parts(prompt)
        profile = _profile(kwargs)
        turn_id = None if profile is None or "turnId" not in profile else _identifier(profile["turnId"])
        ids = None if profile is None else profile.get("inputIds")
        if ids is not None and (not isinstance(ids, list) or len(ids) != len(parts) or len(ids) > 256):
            raise RequestError.invalid_params({"message": "Supply one input ID per prompt content block."})
        input_ids = [] if ids is None else [_identifier(value) for value in ids]
        if len(set(input_ids)) != len(input_ids):
            raise RequestError.invalid_params({"message": "Input IDs must be unique within the turn."})
        if session_id in self.turns or session_id in self.resuming:
            raise RequestError.invalid_request({"message": "The session already has an open turn or resume request."})
        turn = self.turns[session_id] = _Turn(turn_id)
        servers = self.sessions[session_id]
        override = self.model_overrides.get(session_id) or RunModelOverrides(carry_forward=False)

        def check_admission() -> None:
            if turn.cancel_requested or self.disconnected:
                raise _PromptCancelled

        inputs = StrictRootInputs(turn.notifications.put_nowait) if turn_id is not None or input_ids else None
        message = inputs.prompt(tuple(zip(input_ids, parts, strict=True))) if inputs and input_ids else "\n".join(parts)
        result = None
        try:
            async with self.app.watch_thread(root_thread_id=session_id) as watch:
                check_admission()
                self.tool_exclusions.pop(session_id, None)
                capabilities: tuple[AbstractCapability[AgentContext], ...] = (
                    _TurnCapability(
                        turn.notifications,
                        snapshot=lambda hidden: self.tool_exclusions.__setitem__(session_id, hidden),
                    ),
                    *(server.capability() for server in servers),
                )
                if not self.unattended:
                    capabilities += (HandleDeferredToolCalls(self._permission_handler(turn)),)
                await self.app.submit_thread(
                    thread_id=session_id,
                    prompt=message,
                    strict_inputs=inputs,
                    run_capabilities=capabilities,
                    model_overrides=override,
                    on_admitted=lambda receipt: setattr(turn, "receipt", receipt),
                    before_admit=check_admission,
                )
                if turn.cancel_requested and not self.disconnected:
                    self._request_stop(turn)
                result = await self._stream(session_id, turn, watch.events)
            for call_id in turn.open_calls:
                await self.client.session_update(session_id, ToolCallProgress(tool_call_id=call_id, status="failed"))
            if result.status is RootOperationStatus.cancelled:
                return PromptResponse(stop_reason="cancelled")
            if result.status is not RootOperationStatus.completed or result.outcome is None:
                failure = result.failure or (result.outcome and result.outcome.execution.failure)
                raise RequestError.internal_error({"code": failure.code if failure else result.status.value})
            run = result.outcome.execution
            final: dict[str, Any] = {"finalOutput": run.output, "finalOutputOmitted": run.output_omitted}
            continuation = result.outcome.continuation
            if continuation.status != "selected" or continuation.continuation_id is None:
                failure = continuation.failure
                raise RequestError.internal_error({"code": failure.code if failure else "continuation_not_selected"})
            page = await self.app.get_thread_transcript(
                thread_id=session_id, expected_continuation_id=continuation.continuation_id, limit=1
            )
            if page.turns and page.entries and page.turns[-1].final_position == page.entries[-1].position:
                final["finalMessage"] = {
                    "continuationId": continuation.continuation_id,
                    "position": page.entries[-1].position,
                }
            return PromptResponse(stop_reason="end_turn", field_meta={PROFILE: final})
        except _PromptCancelled:
            return PromptResponse(stop_reason="cancelled")
        except LivePresentationError as exc:
            raise RequestError.internal_error({"code": exc.code}) from exc
        except HarnessUiError as exc:
            raise _app_error(exc) from exc
        finally:
            try:
                if not self.disconnected and result is None and turn.receipt is not None:
                    self._request_stop(turn)
                if not self.disconnected and turn.stopping is not None:
                    await self._join_stop(turn.stopping)
            finally:
                del self.turns[session_id]

    def _permission_handler(self, turn: _Turn):
        async def handle(ctx: RunContext[AgentContext], requests: DeferredToolRequests) -> DeferredToolResults:
            approvals = {}
            for call in requests.approvals:
                if self.disconnected or turn.cancel_requested:
                    break
                answer: asyncio.Future[ToolApproved | ToolDenied] = asyncio.get_running_loop().create_future()
                turn.approvals.add(answer)
                turn.notifications.put_nowait(_Approval(call, answer))
                try:
                    decision = await answer
                except asyncio.CancelledError:
                    if not turn.cancel_requested:
                        raise
                    decision = ToolDenied()
                finally:
                    turn.approvals.discard(answer)
                approvals[call.tool_call_id] = decision
            # Pydantic dispatches only after it receives the whole batch.
            if self.disconnected or turn.cancel_requested:
                approvals.update((call.tool_call_id, ToolDenied()) for call in requests.approvals)
            return DeferredToolResults(
                approvals=approvals,
                calls={
                    call.tool_call_id: ToolFailed("Client cannot answer this tool input request.")
                    for call in requests.calls
                },
            )

        return handle

    async def _ask_approval(self, session_id: str, turn: _Turn, request: _Approval) -> None:
        if request.answer.done() or turn.cancel_requested:
            return
        call = request.call
        answer: ToolApproved | ToolDenied = ToolDenied()

        async def ask() -> ToolApproved | ToolDenied:
            if turn.cancel_requested:
                return ToolDenied()
            raw_input = call.args
            with suppress(ValueError, TypeError, AssertionError):
                raw_input = call.args_as_dict()
            response = await self.client.request_permission(
                session_id,
                ToolCallUpdate(tool_call_id=call.tool_call_id, title=call.tool_name, raw_input=raw_input),
                [
                    PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
                    PermissionOption(option_id="reject-once", name="Reject once", kind="reject_once"),
                ],
            )
            outcome = response.outcome
            if not turn.cancel_requested and outcome.outcome == "selected" and outcome.option_id == "allow-once":
                return ToolApproved()
            return ToolDenied()

        try:
            # Publish the start before the approval, even if the live event is still queued.
            await self._start_tool_call(session_id, turn, call.tool_call_id, call.tool_name)
            if turn.cancel_requested:
                return
            pending = asyncio.create_task(ask())
            try:
                ready, _ = await asyncio.wait((pending, request.answer), return_when=asyncio.FIRST_COMPLETED)
                if pending in ready:
                    answer = pending.result()
            finally:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        except (ConnectionError, RequestError):
            pass
        finally:
            if not request.answer.done():
                request.answer.set_result(answer)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        turn = self.turns.get(session_id)
        if turn is None or self.disconnected:
            return
        if turn.receipt is None:
            turn.cancel()
        else:
            self._request_stop(turn)

    def _request_stop(self, turn: _Turn) -> asyncio.Task[None]:
        assert turn.receipt is not None
        turn.cancel()
        if turn.stopping is None:
            turn.stopping = asyncio.create_task(self._stop_run(turn.receipt.receipt_id))
        return turn.stopping

    async def _stop_run(self, receipt_id: str) -> None:
        try:
            await self.app.cancel_root_operation(receipt_id)
        finally:
            if not self.disconnected:
                await self.app.wait_root_operation(receipt_id)

    async def _join_stop(self, stopping: asyncio.Task[None]) -> None:
        # Keep the reservation until the stop joins while the session is live,
        # even when JSON-RPC cancellation arrives repeatedly.
        cancelled = False
        while not stopping.done() and not self.disconnected:
            try:
                await asyncio.shield(stopping)
            except asyncio.CancelledError:
                cancelled = True
        if self.disconnected:
            return
        stopping.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _inventory(self, session_id: str) -> dict[str, Any]:
        source = await self.app.current_configuration()
        inspection = await self.app.inspect_thread_configuration(session_id)
        config = inspection.next_run.configuration
        models = []
        if source is not None:
            for model_id, model in source.models.items():
                thinking = describe_thinking(model.route, model.settings)
                models.append(
                    {
                        "id": model_id,
                        "name": model.name,
                        "defaultEffort": thinking.default_summary,
                        "efforts": [
                            {"id": option.command, "name": option.label}
                            for option in thinking.options
                            if option.disabled_reason is None
                        ],
                    }
                )
        skills = await self.app.skill_catalog(thread_id=session_id) if inspection.next_model_id is not None else None
        return {
            "defaultModel": inspection.next_model_id,
            "models": models,
            "skills": [{"name": item.name, "description": item.description} for item in skills.items] if skills else [],
            "mcpServers": [{"name": name, "source": "configuration"} for name in config.mcp_server_ids]
            + [{"name": entry.name, "source": "session"} for entry in self.sessions[session_id]],
            "omittedMcpServers": self.omitted_servers[session_id],
            **(
                {"omittedTools": [{"name": name, "reason": reason} for name, reason in sorted(hidden.items())]}
                if (hidden := self.tool_exclusions.get(session_id)) is not None
                else {}
            ),
        }

    async def _options(self, session_id: str) -> list[SessionConfigOptionSelect]:
        source = await self.app.current_configuration()
        if source is None or not source.models:
            return []
        inspection = await self.app.inspect_thread_configuration(session_id)
        override = self.model_overrides.get(session_id)
        current = override.model_id if override and override.model_id else inspection.next_model_id
        if current not in source.models:
            current = None
        choices = [SessionConfigSelectOption(value="default", name="Thread default")]
        choices.extend(SessionConfigSelectOption(value=key, name=model.name) for key, model in source.models.items())
        result = [
            SessionConfigOptionSelect(
                id="model", category="model", name="Model", current_value=current or "default", options=choices
            )
        ]
        if current is not None and (model := source.models.get(current)) is not None:
            thinking = describe_thinking(model.route, model.settings)
            if thinking.status == "supported":
                selected = override.thinking if override else None
                if selected is None:
                    selected = thinking.default_value
                selected_name = ("on" if selected else "off") if isinstance(selected, bool) else selected
                result.append(
                    SessionConfigOptionSelect(
                        id="thought_level",
                        category="thought_level",
                        name="Reasoning effort",
                        current_value=selected_name or "default",
                        options=[
                            SessionConfigSelectOption(value=option.command, name=option.label)
                            for option in thinking.options
                            if option.disabled_reason is None
                        ],
                    )
                )
        return result

    async def set_config_option(
        self,
        session_id: str,
        config_id: str,
        value: str,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse:
        self._require_session(session_id)
        options = {option.id: option for option in await self._options(session_id)}
        if session_id in self.turns or session_id in self.resuming:
            raise RequestError.invalid_request({"message": "Set configuration between turns and outside resume."})
        if config_id not in options or value not in {item.value for item in options[config_id].options}:
            raise RequestError.invalid_params({"message": "Unsupported configuration option or value."})
        override = self.model_overrides.get(session_id) or RunModelOverrides(carry_forward=False)
        if config_id == "model":
            override = RunModelOverrides(model_id=None if value == "default" else value, carry_forward=False)
        else:
            thinking = {"default": None, "on": True, "off": False}.get(value, value)
            override = RunModelOverrides(model_id=override.model_id, thinking=thinking, carry_forward=False)
        self.model_overrides[session_id] = override
        self.tool_exclusions.pop(session_id, None)
        return SetSessionConfigOptionResponse(config_options=[*await self._options(session_id)])

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "a13n/inventory":
            session_id = _identifier(params.get("sessionId"))
            self._require_session(session_id)
            return await self._inventory(session_id)
        if method != "a13n/steer":
            raise RequestError.method_not_found(f"_{method}")
        session_id = _identifier(params.get("sessionId"))
        self._require_session(session_id)
        turn_id = _identifier(params.get("turnId"))
        input_id = _identifier(params.get("inputId"))
        message = params.get("text")
        if not isinstance(message, str) or not message.strip():
            raise RequestError.invalid_params({"message": "Steering text must not be blank."})
        turn = self.turns.get(session_id)
        if turn is None or turn.turn_id != turn_id or turn.cancel_requested or turn.receipt is None:
            return {"delivery": "not_delivered"}
        try:
            delivery = await self.app.steer_root_operation_strict(
                turn.receipt.receipt_id, input_id=input_id, message=message
            )
        except HarnessUiError:
            return {"delivery": "unknown"}
        return {"delivery": delivery}

    async def _stream(self, session_id: str, turn: _Turn, events: LiveSubscription) -> RootOperationView:
        assert turn.receipt is not None
        done = asyncio.create_task(self.app.wait_root_operation(turn.receipt.receipt_id))
        receive = asyncio.create_task(events.receive())
        notify = asyncio.create_task(turn.notifications.get())
        try:
            while True:
                ready, _ = await asyncio.wait((done, receive, notify), return_when=asyncio.FIRST_COMPLETED)
                if done in ready:
                    # Fence pending readers before the awaits below so they
                    # cannot consume any entries from the terminal drain.
                    for reader in (receive, notify):
                        if not reader.done():
                            reader.cancel()
                if receive in ready:
                    await self._update(session_id, receive.result())
                if notify in ready:
                    await self._notify(session_id, turn, notify.result())
                if done in ready:
                    break
                if receive in ready:
                    receive = asyncio.create_task(events.receive())
                if notify in ready:
                    notify = asyncio.create_task(turn.notifications.get())
            for event in events.drain_pending():
                await self._update(session_id, event)
            while not turn.notifications.empty():
                await self._notify(session_id, turn, turn.notifications.get_nowait())
            return done.result()
        finally:
            # These are observer tasks. The prompt still owns stopping the Run.
            for task in (done, receive, notify):
                if not task.done():
                    task.cancel()
            await asyncio.gather(done, receive, notify, return_exceptions=True)

    async def _notify(self, session_id: str, turn: _Turn, update: AgentThoughtChunk | str | _Approval) -> None:
        if isinstance(update, _Approval):
            await self._ask_approval(session_id, turn, update)
        elif isinstance(update, str):
            await self.client.ext_notification(
                "a13n/input_accepted", {"sessionId": session_id, "turnId": turn.turn_id, "inputId": update}
            )
        else:
            await self.client.session_update(session_id, update)

    async def _start_tool_call(self, session_id: str, turn: _Turn, call_id: str, name: str) -> None:
        if call_id not in turn.open_calls:
            await self.client.session_update(
                session_id, ToolCallStart(tool_call_id=call_id, title=name, status="in_progress")
            )
            turn.open_calls.add(call_id)

    async def _update(self, session_id: str, event: LiveEvent) -> None:
        if event.run_kind != "root":
            return
        turn = self.turns[session_id]
        assert turn.receipt is not None
        if turn.run_id is None:
            turn.run_id = (await self.app.get_root_operation(turn.receipt.receipt_id)).run_id
        if event.run_id != turn.run_id:
            return
        if event.payload_omitted:
            raise RequestError.internal_error({"code": "live_payload_omitted"})
        payload = event.payload or {}
        item = event.item
        if item is not None and item.kind == "tool_call":
            if event.event_type == "TOOL_CALL_START":
                call_id, name = payload.get("toolCallId"), payload.get("toolCallName")
                if isinstance(call_id, str) and isinstance(name, str) and item.id not in turn.tool_calls:
                    turn.tool_calls[item.id] = call_id
                    await self._start_tool_call(session_id, turn, call_id, name)
            elif item.state in {"completed", "failed", "interrupted"}:
                if (call_id := turn.tool_calls.pop(item.id, None)) is not None:
                    turn.open_calls.discard(call_id)
                    status = "completed" if item.state == "completed" else "failed"
                    await self.client.session_update(session_id, ToolCallProgress(tool_call_id=call_id, status=status))
        if event.event_type == "CUSTOM":
            await self._custom_update(session_id, turn, payload)
        elif event.event_type == "TEXT_MESSAGE_CONTENT" and isinstance(delta := payload.get("delta"), str):
            await self.client.session_update(
                session_id,
                AgentMessageChunk(
                    content=TextContentBlock(type="text", text=delta),
                    field_meta={PROFILE: {"messageId": payload.get("messageId")}},
                ),
            )

    async def _custom_update(self, session_id: str, turn: _Turn, payload: dict[str, Any]) -> None:
        assembled = turn.custom_events.accept(payload)
        if assembled is None:
            return
        name = assembled.get("name")
        value = assembled.get("value")
        event = value.get("event") if isinstance(value, dict) else None
        if name == "a13n.context.compaction_summary" and isinstance(event, dict):
            if not self.client_supports_compaction:
                return
            operation_id = event.get("operation_id")
            summary = event.get("summary")
            if isinstance(operation_id, str) and operation_id and isinstance(summary, str):
                status = turn.compactions.get(operation_id, "in_progress")
                await self.client.session_update(
                    session_id,
                    SessionUpdateCompactionUpdate(
                        compaction_id=operation_id,
                        status=status,
                        summary=[TextContentBlock(type="text", text=summary)],
                    ),
                )
            return
        native = event.get("payload") if isinstance(event, dict) else None
        if not isinstance(native, dict):
            return
        if name == "a13n.harness.context":
            if not self.client_supports_compaction:
                return
            event_type = native.get("type")
            operation_id = native.get("operation_id")
            if isinstance(operation_id, str) and operation_id:
                if event_type == "compaction_started":
                    turn.compactions[operation_id] = "in_progress"
                    await self.client.session_update(
                        session_id,
                        SessionUpdateCompactionUpdate(compaction_id=operation_id, status="in_progress"),
                    )
                elif event_type == "compaction_completed":
                    turn.compactions[operation_id] = "completed"
                    await self.client.session_update(
                        session_id,
                        SessionUpdateCompactionUpdate(compaction_id=operation_id, status="completed"),
                    )
                elif event_type == "compaction_failed":
                    turn.compactions[operation_id] = "failed"
                    parts = [
                        part
                        for part in (native.get("error_code"), native.get("reason"))
                        if isinstance(part, str) and part
                    ]
                    await self.client.session_update(
                        session_id,
                        SessionUpdateCompactionUpdate(
                            compaction_id=operation_id,
                            status="failed",
                            error=": ".join(parts) if parts else None,
                        ),
                    )
            return
        if native.get("type") == "task_changed" and name == "a13n.harness.state":
            task = TaskChangedPayload.model_validate(native).task
            turn.plan[task.id] = PlanEntry(
                content=task.subject, priority="medium", status=task.status, field_meta={PROFILE: {"taskId": task.id}}
            )
            await self.client.session_update(session_id, AgentPlanUpdate(entries=list(turn.plan.values())))
        elif native.get("type") == "usage_report" and name == "a13n.harness.usage":
            usage = UsageReportPayload.model_validate(native)
            context = await self.app.context_usage(session_id)
            report = usage.model_dump(mode="json")
            if context.context_window is not None and context.latest_request_tokens is not None:
                await self.client.session_update(
                    session_id,
                    UsageUpdate(
                        used=context.latest_request_tokens,
                        size=context.context_window,
                        field_meta={PROFILE: {"usageReport": report}},
                    ),
                )
            else:
                await self.client.ext_notification(
                    "a13n/usage_report", {"sessionId": session_id, "version": 1, "usageReport": report}
                )


class _TurnCapability(AbstractCapability[AgentContext]):
    """Expose only Responses summary text from the audited adapters.

    The pinned Responses parser keeps raw `reasoning_text` in
    `provider_details["raw_content"]`, and ciphertext in `signature`. Both
    fields are intentionally ignored, including on parts carrying a summary.
    """

    def __init__(
        self,
        notifications: asyncio.Queue[AgentThoughtChunk | str | _Approval],
        *,
        snapshot: Callable[[dict[str, str]], None],
    ) -> None:
        self.notifications = notifications
        self.snapshot = snapshot

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        self.snapshot(dict(ctx.deps.unattended_tool_exclusions))
        return request_context

    async def on_event(self, ctx: RunContext[AgentContext], *, event: AgentStreamEvent) -> None:
        # The SDK requires an async hook. This producer never awaits I/O.
        if not isinstance(event, PartEndEvent) or not isinstance(event.part, ThinkingPart) or not event.part.content:
            return
        model = ctx.model
        while isinstance(model, WrapperModel):
            model = model.wrapped
        if isinstance(model, (CodexRequestModel, OpenAIResponsesModel)):
            self.notifications.put_nowait(
                AgentThoughtChunk(content=TextContentBlock(type="text", text=event.part.content))
            )


def _cwd(value: str) -> Path:
    directory = Path(value)
    if not directory.is_absolute() or not directory.is_dir():
        raise RequestError.invalid_params({"message": "cwd must name an existing absolute directory."})
    return directory.resolve()


def _app_error(error: HarnessUiError) -> RequestError:
    return RequestError.invalid_request({"code": error.code, "message": str(error)})


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.strip():
        raise RequestError.invalid_params({"message": "Invalid session, turn or input ID."})
    return value


def _prompt_parts(prompt: Sequence[object]) -> list[str]:
    parts: list[str] = []
    for block in prompt:
        if isinstance(block, TextContentBlock):
            parts.append(block.text)
        elif isinstance(block, ResourceContentBlock):
            parts.append(f"{block.name}: {block.uri}")
        else:
            raise RequestError.invalid_params({"message": "This agent accepts text and resource links."})
    result = "\n".join(parts)
    if not result.strip():
        raise RequestError.invalid_params({"message": "The prompt must not be blank."})
    return parts
