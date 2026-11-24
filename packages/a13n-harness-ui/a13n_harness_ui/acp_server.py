"""Agent Client Protocol v1 adapter over the Harness UI App."""

from __future__ import annotations

import asyncio
import os
import re
import select
import sys
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import IO, Any, SupportsIndex, cast

from a13n_harness.context import AgentContext
from a13n_harness.events import TaskChangedPayload, UsageReportPayload
from a13n_harness.models.codex import CodexRequestModel
from a13n_logging import get_logger
from acp import Agent, Client, RequestError
from acp.agent import AgentSideConnection
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    Implementation,
    InitializeResponse,
    McpServerStdio,
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
    SetSessionConfigOptionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
)
from pydantic_ai import DeferredToolRequests, DeferredToolResults, RunContext
from pydantic_ai.capabilities import AbstractCapability, HandleDeferredToolCalls
from pydantic_ai.messages import AgentStreamEvent, PartEndEvent, ThinkingPart
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.tools import ToolApproved, ToolDenied

from a13n_harness_ui.app import HarnessUiApp
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.live import LiveEvent, LiveSubscription
from a13n_harness_ui.model_thinking import describe_thinking
from a13n_harness_ui.root_inputs import StrictRootInputs
from a13n_harness_ui.session_mcp import SessionMcpServer
from a13n_harness_ui.surfaces import (
    NewThreadDefaults,
    RootOperationStatus,
    RootOperationView,
    RootRunReceipt,
    RunModelOverrides,
)

PROFILE = "dev.a13n.harness-ui"


class _PromptCancelled(Exception):
    """The ACP turn was cancelled before the App registered an operation."""


@dataclass(slots=True)
class _Turn:
    turn_id: str | None
    receipt: RootRunReceipt | None = None
    cancel_requested: bool = False
    stopping: asyncio.Task[None] | None = None
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    plan: dict[str, PlanEntry] = field(default_factory=dict)
    run_id: str | None = None
    tool_calls: dict[str, str] = field(default_factory=dict)

    def admitted(self, receipt: RootRunReceipt) -> None:
        self.receipt = receipt


class AcpAgent:
    def __init__(self, app: HarnessUiApp, *, unattended: bool = False) -> None:
        self.app = app
        self.unattended = unattended
        self.client: Client
        self.initialized = False
        self.disconnected = False
        self.profile_usage = False
        self.resuming: set[str] = set()
        self.sessions: dict[str, tuple[SessionMcpServer, ...]] = {}
        self.turns: dict[str, _Turn] = {}
        self.model_overrides: dict[str, RunModelOverrides] = {}
        self.turn_ids: dict[str, set[str]] = {}

    def on_connect(self, conn: Client) -> None:
        self.client = conn

    def disconnect(self) -> None:
        self.disconnected = True
        # Only the App owns Runs. Stop waiting for their settlement in the
        # transport when no peer remains to receive the result.
        for turn in self.turns.values():
            if turn.stopping is not None:
                turn.stopping.cancel()

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        self.initialized = True
        profile = kwargs.get(PROFILE)
        self.profile_usage = (
            isinstance(profile, dict) and type(profile.get("version")) is int and profile["version"] == 1
        )
        return InitializeResponse(
            field_meta={
                PROFILE: {
                    "version": 1,
                    "unattended": self.unattended,
                    "steer": True,
                    "inputAcceptance": True,
                    "inventory": True,
                    "usageReport": True,
                    "unattendedSafeMcpServers": True,
                }
            },
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
        servers, meta = _mcp_selection(directory, mcp_servers, kwargs.get(PROFILE), unattended=self.unattended)
        try:
            project_id = await self.app.ensure_cwd_project(directory)
            thread = await self.app.create_thread(defaults=NewThreadDefaults(project_id=project_id))
        except HarnessUiError as exc:
            raise _app_error(exc) from exc
        self.sessions[thread.thread_id] = servers
        return NewSessionResponse(
            session_id=thread.thread_id, field_meta=meta, config_options=[*await self._options(thread.thread_id)]
        )

    async def resume_session(
        self, session_id: str, cwd: str, mcp_servers: Sequence[object] | None = None, **kwargs: Any
    ) -> ResumeSessionResponse:
        self._require_initialized()
        directory = _cwd(cwd)
        servers, meta = _mcp_selection(directory, mcp_servers, kwargs.get(PROFILE), unattended=self.unattended)
        if session_id in self.turns or session_id in self.resuming:
            raise RequestError.invalid_request({"message": "The session already has an open turn or resume request."})
        self.resuming.add(session_id)
        try:
            detail = await self.app.get_thread(session_id)
            thread = detail.thread
            if thread.parent_thread_id is not None or thread.archived or thread.memory_scope is not None:
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
            options = await self._options(session_id, include_override=False)
            self.sessions[session_id] = servers
            self.model_overrides.pop(session_id, None)
            return ResumeSessionResponse(field_meta=meta, config_options=[*options])
        except HarnessUiError as exc:
            raise _app_error(exc) from exc
        finally:
            self.resuming.remove(session_id)

    async def prompt(self, session_id: str, prompt: Sequence[object], **kwargs: Any) -> PromptResponse:
        self._require_session(session_id)
        parts = _prompt_parts(prompt)
        profile = kwargs.get(PROFILE)
        if profile is not None and not isinstance(profile, dict):
            raise RequestError.invalid_params({"message": "Invalid a13n prompt metadata."})
        turn_id = None if profile is None or "turnId" not in profile else _identifier(profile["turnId"])
        ids = None if profile is None else profile.get("inputIds")
        if ids is not None and (not isinstance(ids, list) or len(ids) != len(parts) or len(ids) > 256):
            raise RequestError.invalid_params({"message": "Supply one input ID per prompt content block."})
        input_ids = [] if ids is None else [_identifier(value) for value in ids]
        if len(set(input_ids)) != len(input_ids):
            raise RequestError.invalid_params({"message": "Input IDs must be unique within the turn."})
        if session_id in self.turns or session_id in self.resuming:
            raise RequestError.invalid_request({"message": "The session already has an open turn or resume request."})
        if turn_id is not None:
            seen = self.turn_ids.setdefault(session_id, set())
            if turn_id in seen or len(seen) >= 4096:
                raise RequestError.invalid_params(
                    {"message": "Use a fresh turn ID (at most 4096 per connection session)."}
                )
            seen.add(turn_id)
        turn = self.turns[session_id] = _Turn(turn_id)
        servers = self.sessions[session_id]
        override = self.model_overrides.get(session_id) or RunModelOverrides(carry_forward=False)

        async def accepted(input_id: str) -> None:
            await self.client.ext_notification(
                "a13n/input_accepted", {"sessionId": session_id, "turnId": turn_id, "inputId": input_id}
            )

        def check_admission() -> None:
            if turn.cancel_requested or self.disconnected:
                raise _PromptCancelled

        inputs = StrictRootInputs(accepted)
        message = inputs.prompt(tuple(zip(input_ids, parts, strict=True))) if input_ids else "\n".join(parts)
        result = None
        try:
            async with self.app.watch_thread(root_thread_id=session_id) as watch:
                check_admission()
                capabilities: tuple[AbstractCapability[AgentContext], ...] = (
                    _SummaryCapability(self.client, session_id),
                    *(server.capability() for server in servers),
                )
                if not self.unattended:
                    capabilities += (HandleDeferredToolCalls(self._permission_handler(session_id)),)
                await self.app.submit_thread(
                    thread_id=session_id,
                    prompt=message,
                    strict_inputs=inputs,
                    run_capabilities=capabilities,
                    model_overrides=override,
                    on_admitted=turn.admitted,
                    before_admit=check_admission,
                )
                if turn.cancel_requested and not self.disconnected:
                    self._request_stop(turn)
                result = await self._observe(session_id, turn, watch.events)
            for call_id in turn.tool_calls.values():
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
                turn.settled.set()

    def _permission_handler(self, session_id: str):
        async def handle(ctx: RunContext[AgentContext], requests: DeferredToolRequests) -> DeferredToolResults:
            approvals = {}
            for call in requests.approvals:
                response = await self.client.request_permission(
                    session_id,
                    ToolCallUpdate(tool_call_id=call.tool_call_id, title=call.tool_name, raw_input=call.args),
                    [
                        PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
                        PermissionOption(option_id="reject-once", name="Reject once", kind="reject_once"),
                    ],
                )
                outcome = response.outcome
                approvals[call.tool_call_id] = (
                    ToolApproved()
                    if outcome.outcome == "selected" and outcome.option_id == "allow-once"
                    else ToolDenied()
                )
            return DeferredToolResults(
                approvals=approvals,
                calls={
                    call.tool_call_id: ToolDenied("Client cannot answer this tool input request.")
                    for call in requests.calls
                },
            )

        return handle

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        turn = self.turns.get(session_id)
        if turn is None or self.disconnected:
            return
        turn.cancel_requested = True
        if turn.receipt is not None:
            self._request_stop(turn)
        await turn.settled.wait()

    def _request_stop(self, turn: _Turn) -> asyncio.Task[None]:
        assert turn.receipt is not None
        turn.cancel_requested = True
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
        }

    async def _options(self, session_id: str, *, include_override: bool = True) -> list[SessionConfigOptionSelect]:
        source = await self.app.current_configuration()
        if source is None or not source.models:
            return []
        inspection = await self.app.inspect_thread_configuration(session_id)
        override = self.model_overrides.get(session_id) if include_override else None
        current = override.model_id if override and override.model_id else inspection.next_model_id
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
        if session_id in self.turns or session_id in self.resuming:
            raise RequestError.invalid_request({"message": "Set configuration between turns and outside resume."})
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

    async def _observe(self, session_id: str, turn: _Turn, events: LiveSubscription) -> RootOperationView:
        assert turn.receipt is not None
        receipt_id = turn.receipt.receipt_id
        done = asyncio.create_task(self.app.wait_root_operation(receipt_id))
        try:
            await self._stream(session_id, events, done)
            return done.result()
        finally:
            # Only the observer is owned here; prompt owns stopping the Run.
            if not done.done():
                done.cancel()
            await asyncio.gather(done, return_exceptions=True)

    async def _stream(self, session_id: str, events: LiveSubscription, done: asyncio.Task[RootOperationView]) -> None:
        while not done.done():
            receive = asyncio.create_task(events.receive())
            try:
                await asyncio.wait((receive, done), return_when=asyncio.FIRST_COMPLETED)
                if receive.done():
                    await self._update(session_id, receive.result())
            finally:
                if not receive.done():
                    receive.cancel()
                    await asyncio.gather(receive, return_exceptions=True)
        for event in events.drain_pending():
            await self._update(session_id, event)

    async def _update(self, session_id: str, event: LiveEvent) -> None:
        if event.run_kind != "root":
            return
        turn = self.turns[session_id]
        assert turn.receipt is not None
        if turn.run_id is None:
            turn.run_id = (await self.app.get_root_operation(turn.receipt.receipt_id)).run_id
        if event.run_id != turn.run_id:
            return
        payload = event.payload or {}
        item = event.item
        if item is not None and item.kind == "tool_call":
            if event.event_type == "TOOL_CALL_START":
                call_id, name = payload.get("toolCallId"), payload.get("toolCallName")
                if isinstance(call_id, str) and isinstance(name, str) and item.id not in turn.tool_calls:
                    turn.tool_calls[item.id] = call_id
                    await self.client.session_update(
                        session_id, ToolCallStart(tool_call_id=call_id, title=name, status="in_progress")
                    )
            elif item.state in {"completed", "failed", "interrupted"}:
                if (call_id := turn.tool_calls.pop(item.id, None)) is not None:
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
        value = payload.get("value")
        event = value.get("event") if isinstance(value, dict) else None
        native = event.get("payload") if isinstance(event, dict) else None
        if not isinstance(native, dict):
            return
        if native.get("type") == "task_changed" and payload.get("name") == "a13n.harness.state":
            task = TaskChangedPayload.model_validate(native).task
            turn.plan[task.id] = PlanEntry(
                content=task.subject, priority="medium", status=task.status, field_meta={PROFILE: {"taskId": task.id}}
            )
            await self.client.session_update(session_id, AgentPlanUpdate(entries=list(turn.plan.values())))
        elif native.get("type") == "usage_report" and payload.get("name") == "a13n.harness.usage":
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
            elif self.profile_usage:
                await self.client.ext_notification(
                    "a13n/usage_report", {"sessionId": session_id, "version": 1, "usageReport": report}
                )


class _SummaryCapability(AbstractCapability[AgentContext]):
    """Expose only Responses summary text from the audited adapters.

    The pinned Responses parser keeps raw `reasoning_text` in
    `provider_details["raw_content"]`, and ciphertext in `signature`. Both
    fields are intentionally ignored, including on parts carrying a summary.
    """

    def __init__(self, client: Client, session_id: str) -> None:
        self.client = client
        self.session_id = session_id

    async def on_event(self, ctx: RunContext[AgentContext], *, event: AgentStreamEvent) -> None:
        if (
            isinstance(event, PartEndEvent)
            and isinstance(event.part, ThinkingPart)
            and event.part.content
            and isinstance(ctx.model, (CodexRequestModel, OpenAIResponsesModel))
        ):
            await self.client.session_update(
                self.session_id, AgentThoughtChunk(content=TextContentBlock(type="text", text=event.part.content))
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


def _mcp_selection(
    directory: Path,
    entries: Sequence[object] | None,
    profile: Any,
    *,
    unattended: bool,
) -> tuple[tuple[SessionMcpServer, ...], dict[str, Any] | None]:
    if profile is not None and not isinstance(profile, dict):
        raise RequestError.invalid_params({"message": "Invalid a13n session metadata."})
    opted = [] if profile is None else profile.get("unattendedSafeMcpServers", [])
    if not isinstance(opted, list) or any(not isinstance(name, str) for name in opted) or len(set(opted)) != len(opted):
        raise RequestError.invalid_params({"message": "List unique names in unattendedSafeMcpServers."})
    if profile is not None and opted and (type(profile.get("version")) is not int or profile["version"] != 1):
        raise RequestError.invalid_params({"message": "MCP opt-in requires a13n profile version 1."})
    servers = []
    omitted = []
    names = set()
    for entry in entries or ():
        if not isinstance(entry, McpServerStdio):
            raise RequestError.invalid_params({"message": "Only stdio MCP servers are supported."})
        name = entry.name
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name) or name in names:
            raise RequestError.invalid_params({"message": "MCP server names must be unique identifiers."})
        names.add(name)
        if not entry.command.strip() or "\x00" in entry.command or any("\x00" in arg for arg in entry.args):
            raise RequestError.invalid_params({"message": "Use a nonblank MCP command and valid arguments."})
        env = tuple((pair.name, pair.value) for pair in entry.env)
        if len({key for key, _ in env}) != len(env) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in value for key, value in env
        ):
            raise RequestError.invalid_params({"message": "Use unique, valid MCP environment entries."})
        if unattended and name not in opted:
            omitted.append({"name": name, "reason": "unattended_safety_not_declared"})
            continue
        servers.append(SessionMcpServer(name, entry.command, tuple(entry.args), directory, env, name in opted))
    if set(opted) - names:
        raise RequestError.invalid_params({"message": "Every opted-in MCP name must appear in this request."})
    for item in omitted:
        get_logger(__name__).warning("Omitted ACP MCP server %s: %s", item["name"], item["reason"])
    return tuple(servers), {PROFILE: {"omittedMcpServers": omitted}} if omitted else None


class _StdinReader(asyncio.StreamReader):
    def __init__(self) -> None:
        # StreamReader pauses above twice this pending-input limit. The SDK
        # can still consume larger individual frames in chunks.
        super().__init__(limit=1024 * 1024)
        self._read_changed = asyncio.Event()
        self._eof_received = False
        self._pipe: asyncio.ReadTransport | None = None

    def set_transport(self, transport: asyncio.BaseTransport) -> None:
        super().set_transport(transport)
        self._pipe = cast(asyncio.ReadTransport, transport)

    def feed_data(self, data: Iterable[SupportsIndex]) -> None:
        super().feed_data(data)
        if self._pipe is not None and not self._pipe.is_reading():
            self._read_changed.set()

    def feed_eof(self) -> None:
        super().feed_eof()
        self._eof_received = True
        self._read_changed.set()

    async def wait_disconnected(self, stdin_fd: int) -> None:
        # Poll only while asyncio has paused reads. POLLHUP reports a closed
        # writer even with unread bytes in the pipe, without consuming them.
        hangup = select.poll()
        # Linux can report a socket peer's write-side shutdown as RDHUP
        # without HUP if that peer is still reading.
        hangup.register(stdin_fd, select.POLLHUP | getattr(select, "POLLRDHUP", 0))
        while not self._eof_received:
            if self._pipe is not None and not self._pipe.is_reading():
                try:
                    if hangup.poll(0):
                        return
                except OSError:
                    return
                await asyncio.sleep(0.05)
            else:
                await self._read_changed.wait()
                self._read_changed.clear()


@asynccontextmanager
async def _stdio(protocol: IO[str]) -> AsyncIterator[tuple[_StdinReader, asyncio.StreamWriter]]:
    loop = asyncio.get_running_loop()
    reader = _StdinReader()
    input_pipe, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    try:
        # asyncio's pipe flow control wakes drain() when abort() closes the
        # connection. The SDK's stdout protocol only wakes on resume_writing().
        unused_reader = asyncio.StreamReader()
        output_protocol = asyncio.StreamReaderProtocol(unused_reader)
        output_pipe, _ = await loop.connect_write_pipe(lambda: output_protocol, protocol)
        try:
            yield reader, asyncio.StreamWriter(output_pipe, output_protocol, None, loop)
        finally:
            if not output_pipe.is_closing():
                output_pipe.abort()
    finally:
        input_pipe.close()


async def _listen(connection: AgentSideConnection, reader: _StdinReader) -> None:
    # A receive can be waiting to send a parse-error response. Observe
    # stdin closing even if the SDK listener cannot read more bytes.
    listener = asyncio.create_task(connection.listen())
    disconnect = asyncio.create_task(reader.wait_disconnected(sys.stdin.fileno()))
    try:
        await asyncio.wait((listener, disconnect), return_when=asyncio.FIRST_COMPLETED)
        if listener.done():
            listener.result()
        if disconnect.done():
            disconnect.result()
    finally:
        for task in (listener, disconnect):
            task.cancel()
        await asyncio.gather(listener, disconnect, return_exceptions=True)


async def serve(app_factory: AbstractAsyncContextManager[HarnessUiApp], *, unattended: bool = False) -> None:
    """Keep even subprocess and extension diagnostics away from the protocol descriptor."""
    sys.stdout.flush()
    with os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8") as original_stdout:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        try:
            with os.fdopen(os.dup(original_stdout.fileno()), "w", encoding="utf-8") as protocol:
                async with _stdio(protocol) as (reader, writer):
                    closing = None
                    try:
                        async with app_factory as app:
                            agent = AcpAgent(app, unattended=unattended)
                            # Own the listen/close boundary without changing SDK routing.
                            connection = AgentSideConnection(cast(Agent, agent), writer, reader, listening=False)
                            try:
                                await _listen(connection, reader)
                            finally:
                                agent.disconnect()
                                if not writer.is_closing():
                                    writer.transport.abort()
                                # Exit the App context without waiting for SDK handlers.
                                closing = asyncio.create_task(connection.close())
                    finally:
                        if closing is not None:
                            # An aborted writer can report connection loss when
                            # SDK close joins its sender. The client is gone.
                            with suppress(ConnectionError):
                                await closing
        finally:
            sys.stdout.flush()
            os.dup2(original_stdout.fileno(), sys.stdout.fileno())
