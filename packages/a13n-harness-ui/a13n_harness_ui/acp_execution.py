"""Run an outbound ACP turn inside the regular Harness Run middleware lifecycle."""

from __future__ import annotations

import json
import os
from base64 import b64encode
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape, quoteattr

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
from a13n_harness.capabilities.context import restored_history_summary
from a13n_harness.capabilities.skills import BoundSkillCatalog, SkillManager
from a13n_harness.content import ContentItem, content_items, input_request, request_input_content
from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import ArgvCommand, CommandRequest
from a13n_harness.events import input_events
from a13n_harness.input import RunInputValue
from a13n_harness.model_context import ModelContextBlock, ModelContextHistory
from a13n_harness.plugins import AbstractHarnessPlugin, PluginRunExchange, PluginRunNext
from a13n_harness.result import HarnessRunResult
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity
from a13n_harness.tools.permission_gate import check_permission
from a13n_harness.tools.permissions import ToolPermissionsCapability
from a13n_harness.toolsets.interaction import AskUserQuestionRequest, UserQuestion
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord
from a13n_stream_protocol.acp import AcpRunStream
from acp import schema
from anyio import CancelScope, to_thread
from mcp.types import ElicitRequestFormParams
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_ai import BinaryContent, RunContext, TextContent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ApprovalRequired, ToolFailed
from pydantic_ai.messages import (
    CapabilityEvent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
)
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset
from pydantic_ai.usage import RunUsage

from a13n_harness_ui.acp_inputs import AcpInputs
from a13n_harness_ui.acp_sessions import (
    MODEL_OPTION_GRACE_SECONDS,
    AcpSessions,
    AcpSessionState,
    acp_commands,
    acp_options_scope,
)
from a13n_harness_ui.configuration.models import AcpAgentPreset, AcpAgentResource
from a13n_harness_ui.conversation import input_excerpt
from a13n_harness_ui.environment_paths import acp_skill_host_path
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.goal import GoalCapability
from a13n_harness_ui.mcp_runtime.inputs import Inputs
from a13n_harness_ui.skill_input import format_skill_selection

_NATIVE_AGENTS_MD_PRESETS = frozenset({"antigravity", "codex", "opencode", "github_copilot", "goose", "kimi_cli"})
_INJECT_AGENTS_MD_PRESETS = frozenset({"claude_code", "gemini_cli"})
_MAX_AGENTS_MD_BYTES = 32 * 1024
_MAX_EMBEDDED_ATTACHMENT_BYTES = 64 * 1024
_EXCLUDED_ACP_CONTEXT_SOURCES = frozenset({"a13n.environment", "a13n.environment-mounts"})
_INBOX_REMINDER_SOURCE = "a13n-harness-ui.inbox-reminder"
_INPUT_SURFACE_SOURCE = "a13n-harness-ui.input-surface"
_SKILLS_SELECTION_SOURCE = "a13n-harness-ui.skills"


def _is_readable_regular_file(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False


def _read_bounded_utf8(path: Path, max_bytes: int) -> str | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
    except OSError:
        return None
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="ignore").strip()
    if not text:
        return None
    if truncated:
        text += "\n\n[Truncated at 32 KiB; read the file on disk for the remainder.]"
    return text


def workspace_agents_instructions(
    preset: AcpAgentPreset, cwd: str, additional_directories: tuple[str, ...]
) -> tuple[str, ...]:
    """Resolve preset-aware workspace AGENTS.md instructions for an ACP session."""
    sections: list[str] = []
    cwd_agents = Path(cwd) / "AGENTS.md"
    if _is_readable_regular_file(cwd_agents):
        if preset in _INJECT_AGENTS_MD_PRESETS:
            content = _read_bounded_utf8(cwd_agents, _MAX_AGENTS_MD_BYTES)
            if content is not None:
                sections.append(
                    f"# AGENTS.md instructions ({cwd_agents})\n\n<INSTRUCTIONS>\n{content}\n</INSTRUCTIONS>"
                )
        elif preset == "custom":
            sections.append(
                f"Project instructions are available in {cwd_agents}; read it if your runtime has not already loaded it."
            )
    extra_agents = [
        str(candidate)
        for directory in additional_directories
        if _is_readable_regular_file(candidate := Path(directory) / "AGENTS.md")
    ]
    if extra_agents:
        sections.append("Additional project root AGENTS.md files: " + ", ".join(extra_agents) + ".")
    return tuple(sections)


def _format_acp_skills_index(catalog: BoundSkillCatalog | None) -> str | None:
    if catalog is None or not catalog.items:
        return None
    lines = [
        '<available-skills source="harness-ui">',
        "The following Harness UI Skills are available for this session. When a skill directly applies, "
        "read its SKILL.md in full from the listed Host path before following its workflow.",
    ]
    for item in catalog.items:
        host_path = acp_skill_host_path(item.path).rstrip("/") + "/SKILL.md"
        summary = " ".join(item.description.split())
        lines.append(
            f"<skill name={quoteattr(item.name)} source={quoteattr(item.source_id)} "
            f"path={quoteattr(host_path)}>{escape(summary)}</skill>"
        )
    lines.append("</available-skills>")
    return "\n".join(lines)


def _host_tool_hint(available_tools: frozenset[str]) -> str | None:
    has_question = "ask_user_question" in available_tools
    has_inbox = "post_to_inbox" in available_tools
    if has_question and has_inbox:
        return (
            "Use harness-host ask_user_question to ask for confirmation inline "
            "without ending the turn, and post_to_inbox to report results or blockers."
        )
    if has_question:
        return "Use harness-host ask_user_question to ask for confirmation inline without ending the turn."
    if has_inbox:
        return "Use harness-host post_to_inbox to report results or blockers."
    return None


def _merge_delivered_context(
    existing: tuple[tuple[str, str | None, str], ...],
    blocks: Sequence[ModelContextBlock],
) -> tuple[tuple[str, str | None, str], ...]:
    if not blocks:
        return existing
    entries: dict[tuple[str, str | None], str] = {(source_id, key): digest for source_id, key, digest in existing}
    for block in blocks:
        entries[(block.source_id, block.key)] = block.content_hash
    return tuple((source_id, key, digest) for (source_id, key), digest in entries.items())


def _read_small_utf8_file(path: Path, max_bytes: int) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
    except OSError:
        return None
    if len(raw) > max_bytes or b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _message_key(message: ModelMessage) -> str:
    return sha256(ModelMessagesTypeAdapter.dump_json([message])).hexdigest()


def _prior_conversation(history: Sequence[ModelMessage], session: AcpSessionState) -> tuple[str, ...]:
    synced = min(session.synced_messages, len(history))
    if synced and _message_key(history[synced - 1]) != session.synced_message_key:
        # Compaction can relocate or replace the saved prefix boundary.
        synced = next(
            (
                index + 1
                for index in range(len(history) - 1, -1, -1)
                if _message_key(history[index]) == session.synced_message_key
            ),
            0,
        )
    lines: list[str] = []
    remaining = 8000
    summary = restored_history_summary(history[synced:])
    for message in reversed(history[synced:][-64:]):
        if isinstance(message, ModelRequest):
            if (message.metadata or {}).get("a13n.context") in {"handoff", "compaction"}:
                continue
            text = input_excerpt(request_input_content(message))
            tools = [part.tool_name for part in message.parts if isinstance(part, ToolReturnPart)]
            line = f"User: {text}" if text else ""
            if tools:
                # Tool payloads can include credentials or non-display data. Keep names, not raw results.
                line += ("\n" if line else "") + "Tools returned: " + ", ".join(dict.fromkeys(tools))
        else:
            text = "\n".join(part.content for part in message.parts if isinstance(part, TextPart))
            line = f"Assistant: {text}" if text else ""
        if line:
            line = escape(line[: min(1800, remaining)])[:remaining]
            lines.append(line)
            remaining -= len(line) + 1
            if remaining <= 0:
                break
    if summary and remaining > 0:
        lines.append("Earlier conversation: " + escape(summary[: min(1800, remaining)])[:remaining])
    if not lines:
        return ()
    body = "\n".join(reversed(lines))
    return (f"<prior-thread-conversation>\n{body}\n</prior-thread-conversation>",)


_NAMESPACE = "a13n.acp"
_SHELL = ToolDefinition(
    name="shell_exec",
    description="Execute a command requested by an external ACP Agent.",
    metadata={TOOL_IDENTITY_KEY: ToolIdentity("environment.shell_exec", "review")},
)


class _ExecutePermissionInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    command: str
    args: list[str] = []
    cwd: str | None = None
    env: dict[str, str] = {}


def _bind_shell_review(
    binding: AcpRunBinding, context: RunContext[AgentContext], interactions: AcpInputs | None
) -> None:
    async def review(
        tool_call_id: str, command: ArgvCommand, cwd: str | None, environment: dict[str, str]
    ) -> bool | None:
        arguments = {"command": [command.executable, *command.arguments], "cwd": cwd, "environment": environment}
        try:
            await check_permission(
                replace(context, tool_call_id=tool_call_id, tool_name=_SHELL.name), _SHELL, arguments
            )
        except ApprovalRequired:
            return None
        except ToolFailed:
            return False
        return True

    def summary(command: ArgvCommand, cwd: str | None, environment: dict[str, str]) -> str:
        return (
            f"Allow this command?\n\n{[command.executable, *command.arguments]!r}"
            f"\n\nWorking directory: {cwd or 'default'}"
            f"\nEnvironment overrides: {', '.join(sorted(environment)) or 'none'}"
        )

    async def terminal(request: CommandRequest) -> bool:
        assert isinstance(request.command, ArgvCommand)
        environment = dict(request.environment.set)
        decision = await review(f"call-{uuid4().hex}", request.command, request.cwd, environment)
        if decision is not None:
            return decision
        message = summary(request.command, request.cwd, environment)
        if interactions is None or len(message) > 8192:
            return False
        result = await interactions.request(
            ElicitRequestFormParams(
                message=message,
                requested_schema={"type": "object", "properties": {}},
            ),
            None,
        )
        return result.action == "accept"

    async def permission(call: schema.ToolCallUpdate, options: tuple[schema.PermissionOption, ...]) -> str | None:
        try:
            raw = _ExecutePermissionInput.model_validate(call.raw_input)
            command = ArgvCommand(executable=raw.command, arguments=tuple(raw.args))
        except ValidationError:
            return None
        decision = await review(call.tool_call_id, command, raw.cwd, raw.env)
        message = summary(command, raw.cwd, raw.env)
        if decision is False or interactions is None or len(message) > 8192:
            return None
        # A reviewed execute request can authorize this invocation only.
        options = tuple(
            option.model_copy(update={"name": "Allow once" if option.kind == "allow_once" else "Reject"})
            for option in options
            if option.kind in {"allow_once", "reject_once"}
        )
        return await interactions.permission(call.model_copy(update={"title": message}), options)

    binding.review_command = terminal
    binding.review_permission_command = permission


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
    thread_files: Path | None = None
    shell_review: Callable[[str], Awaitable[ToolPermissionsCapability]] | None = None
    channel: AcpChannel | None = None
    bind_environment: Callable[[AgentContext], AcpRunBinding] = lambda _: AcpRunBinding()
    goal: GoalCapability | None = None
    bot: bool = False
    skill_manager: SkillManager | None = None
    initial_skill_catalog: BoundSkillCatalog | None = None
    on_skill_catalog: Callable[[BoundSkillCatalog], None] | None = None
    runtime: AcpSessionRuntime | None = None
    saved: AcpSessionState | None = None
    persist: Callable[[AcpSessionState], Awaitable[None]] | None = None
    update_title: Callable[[str], Awaitable[None]] | None = None
    prewarm: bool = False
    _stop_requested: bool = False

    @property
    def plugin_id(self) -> str:
        return _NAMESPACE

    def _retained_host_path(self, metadata: dict[str, Any] | None) -> Path | None:
        if (
            self.thread_files is None
            or not isinstance(metadata, dict)
            or metadata.get("mount") != "thread-files"
            or not isinstance(path := metadata.get("path"), str)
        ):
            return None
        return self.thread_files / path

    def _skill_selection_text(self, item: ContentItem, fallback: str) -> str:
        namespace = (item.metadata.model_extra or {}).get("harness_ui")
        selected = namespace.get("selected_skills") if isinstance(namespace, dict) else None
        if not isinstance(selected, list) or not selected:
            return fallback
        entries: list[tuple[str, str, str]] = []
        for entry in selected:
            if (
                isinstance(entry, dict)
                and isinstance(name := entry.get("name"), str)
                and isinstance(source_id := entry.get("source_id"), str)
                and isinstance(logical_path := entry.get("logical_path"), str)
            ):
                entries.append((name, source_id, logical_path))
        return format_skill_selection(entries, host_paths=True) if entries else fallback

    def _attachment_blocks(
        self,
        item: ContentItem,
        *,
        prompt_capabilities: schema.PromptCapabilities,
        text_only: bool,
    ) -> list[PromptBlock]:
        value = item.value
        assert isinstance(value, TextContent)
        namespace = (item.metadata.model_extra or {}).get("harness_ui")
        if not isinstance(namespace, dict) or namespace.get("mount") != "thread-files":
            return [schema.TextContentBlock(text=value.content)]
        if namespace.get("captured_source_text"):
            return [schema.TextContentBlock(text=value.content)]
        path = namespace.get("path")
        if not isinstance(path, str):
            return [schema.TextContentBlock(text=value.content)]
        host_path = self._retained_host_path(namespace)
        resolved_path = str(host_path) if host_path is not None else path
        attachment = namespace.get("attachment")
        attachment_dict = attachment if isinstance(attachment, dict) else {}
        raw_name = attachment_dict.get("name")
        att_name = raw_name if isinstance(raw_name, str) else Path(path).name
        raw_media_type = attachment_dict.get("media_type")
        media_type = raw_media_type if isinstance(raw_media_type, str) else "application/octet-stream"
        raw_size = attachment_dict.get("size")
        size = raw_size if isinstance(raw_size, int) else None
        source = attachment_dict.get("source")
        if namespace.get("long_text"):
            characters = namespace.get("characters")
            char_label = f" ({characters} characters)" if isinstance(characters, int) else ""
            text = (
                f"The user's submitted text{char_label} is saved in a retained input file.\n"
                f"File: {resolved_path}\n"
                "Read this file to understand the user's request before answering. "
                "Read further sections as needed; no part of the original text is included inline."
            )
            blocks: list[PromptBlock] = [schema.TextContentBlock(text=text)]
            if not text_only and host_path is not None:
                blocks.append(
                    schema.ResourceContentBlock(uri=host_path.as_uri(), name=att_name, mime_type="text/plain")
                )
            return blocks
        composer = namespace.get("composer")
        label = composer.get("label") if isinstance(composer, dict) and isinstance(composer.get("label"), str) else None
        display_name = f"{label} ({att_name!r})" if label else repr(att_name)
        source_description = f" Selected Host source: {json.dumps(source)}." if source is not None else ""
        header = f"Attachment {display_name} ({media_type}): {resolved_path}.{source_description}"
        blocks = [schema.TextContentBlock(text=header)]
        if text_only or host_path is None or media_type.startswith("image/") or media_type.startswith("audio/"):
            return blocks
        if (
            prompt_capabilities.embedded_context
            and source is None
            and (size is None or size <= _MAX_EMBEDDED_ATTACHMENT_BYTES)
        ):
            embedded = _read_small_utf8_file(host_path, _MAX_EMBEDDED_ATTACHMENT_BYTES)
            if embedded is not None:
                blocks.append(
                    schema.EmbeddedResourceContentBlock(
                        resource=schema.TextResourceContents(
                            uri=host_path.as_uri(), mime_type=media_type, text=embedded
                        )
                    )
                )
                return blocks
        if host_path.is_file():
            blocks.append(schema.ResourceContentBlock(uri=host_path.as_uri(), name=att_name, mime_type=media_type))
        return blocks

    def build_prompt_blocks(
        self,
        value: RunInputValue | None,
        *,
        prompt_capabilities: schema.PromptCapabilities,
        text_only: bool = False,
    ) -> tuple[list[PromptBlock], list[ContentItem]]:
        blocks: list[PromptBlock] = []
        retained: list[ContentItem] = []
        for item in content_items(value) if value is not None else ():
            if item.metadata.display or item.metadata.source_id == _INPUT_SURFACE_SOURCE:
                retained.append(item)
            if item.metadata.source_id == _INPUT_SURFACE_SOURCE:
                continue
            part = item.value
            if isinstance(part, str):
                blocks.append(schema.TextContentBlock(text=part))
            elif isinstance(part, TextContent):
                if item.metadata.source_id == _SKILLS_SELECTION_SOURCE:
                    blocks.append(schema.TextContentBlock(text=self._skill_selection_text(item, part.content)))
                else:
                    blocks.extend(
                        self._attachment_blocks(item, prompt_capabilities=prompt_capabilities, text_only=text_only)
                    )
            elif isinstance(part, BinaryContent):
                namespace = (item.metadata.model_extra or {}).get("harness_ui")
                host_path = self._retained_host_path(namespace if isinstance(namespace, dict) else None)
                if not text_only and part.is_image and prompt_capabilities.image:
                    blocks.append(
                        schema.ImageContentBlock(data=b64encode(part.data).decode("ascii"), mime_type=part.media_type)
                    )
                elif not text_only and part.is_audio and prompt_capabilities.audio:
                    blocks.append(
                        schema.AudioContentBlock(data=b64encode(part.data).decode("ascii"), mime_type=part.media_type)
                    )
                elif host_path is not None:
                    attachment = namespace.get("attachment") if isinstance(namespace, dict) else None
                    raw_name = attachment.get("name") if isinstance(attachment, dict) else None
                    att_name = raw_name if isinstance(raw_name, str) else host_path.name
                    kind = "Image" if part.is_image else "Audio" if part.is_audio else "Binary"
                    blocks.append(
                        schema.TextContentBlock(
                            text=(
                                f"{kind} attachment {att_name!r} ({part.media_type}) is saved on disk at {host_path}. "
                                "Inspect it from disk because this session does not accept inline media blocks."
                            )
                        )
                    )
                    if not text_only:
                        blocks.append(
                            schema.ResourceContentBlock(
                                uri=host_path.as_uri(), name=att_name, mime_type=part.media_type
                            )
                        )
                else:
                    raise AcpError("acp_input_unsupported", "The external Agent cannot accept this attachment.")
            else:
                raise AcpError("acp_input_unsupported", "The external Agent cannot accept this attachment.")
        return blocks, retained

    def build_steer_blocks(self, value: RunInputValue) -> list[PromptBlock]:
        if self.runtime is None:
            return []
        prompt_capabilities = self.runtime.capabilities.prompt_capabilities or schema.PromptCapabilities()
        blocks, _ = self.build_prompt_blocks(
            value,
            prompt_capabilities=prompt_capabilities,
            text_only=not self.runtime.supports_multimodal_steering,
        )
        return blocks

    async def steer(self, content: Sequence[PromptBlock] | str) -> SteeringOutcome:
        if self.runtime is None:
            return "not_delivered"
        return await self.runtime.steer(content)

    @property
    def supports_steering(self) -> bool:
        return self.runtime is not None and self.runtime.supports_steering and not self._stop_requested

    async def stop(self) -> bool:
        self._stop_requested = True
        if self.runtime is None:
            return False
        await self.runtime.cancel()
        return True

    async def _record_usage(
        self, context: AgentContext, runtime: AcpSessionRuntime, response: schema.PromptResponse
    ) -> None:
        usage = response.usage
        assert usage is not None
        ledger = context.usage_attribution
        model = next(
            (item.current_value for item in runtime.config_options if item.category == "model" or item.id == "model"),
            None,
        )
        thought = usage.thought_tokens or 0
        output_tokens = usage.output_tokens
        if usage.input_tokens + output_tokens + thought <= usage.total_tokens:
            output_tokens += thought
        input_tokens = max(usage.input_tokens, usage.total_tokens - output_tokens)
        record_id = f"model-{uuid4().hex}"
        record = ModelUsageRecord(
            record_id=record_id,
            run_id=ledger.run_id,
            response_ordinal=ledger.next_model_ordinal(),
            call_id=record_id,
            agent_instance_id=ledger.instance.agent_instance_id,
            parent_agent_instance_id=ledger.instance.parent_agent_instance_id,
            delegation_id=ledger.instance.delegation_id,
            source="acp",
            model_name=model if isinstance(model, str) else None,
            provider_name=self.resource.name,
            response_state="incomplete" if response.stop_reason == "cancelled" else "complete",
            outcome="cancelled" if response.stop_reason == "cancelled" else "completed",
            response_timestamp=datetime.now(UTC),
            request_usage=BoundedRequestUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                details={"thoughts_tokens": thought} if thought else {},
                cache_read_tokens=usage.cached_read_tokens or 0,
                cache_write_tokens=usage.cached_write_tokens or 0,
            ),
        )
        refusal = ledger.finish(record_id, record)
        await ledger._flush(reason="model_request", trigger_record_id=record_id)
        if refusal is not None:
            raise refusal

    async def wrap_run(
        self, exchange: PluginRunExchange, call_next: PluginRunNext[Any]
    ) -> HarnessRunResult[str | None]:
        context = exchange.context
        if exchange.input.value is None and not self.prewarm:
            raise AcpError("acp_input_required", "An external ACP turn requires a prompt.")
        previous = await exchange.export_current_state()
        legacy = await context.state.read(_NAMESPACE, AcpSessionState, version="1")
        saved = self.saved if self.saved is not None and self.saved.digest == self.digest else None
        if self.saved is not None and self.saved.session_id is None:
            if self.saved.digest == acp_options_scope(self.resource, self.setup.cwd):
                saved = self.saved
            elif saved is None and not self.prewarm:
                raise AcpError("acp_option_invalid", "The ACP Agent or workspace changed. Select its options again.")
        if (
            saved is None
            and legacy is not None
            and legacy.digest == self.digest
            and legacy.thread_id in {None, context.thread_id}
        ):
            saved = legacy
        saved_id = saved.session_id if saved is not None else None
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
        ) as resident:
            runtime, bridge = resident.runtime, resident.host_tools
            prompt_capabilities = runtime.capabilities.prompt_capabilities or schema.PromptCapabilities()
            blocks, retained_user_items = self.build_prompt_blocks(
                exchange.input.value, prompt_capabilities=prompt_capabilities
            )
            user_message = replace(input_request(retained_user_items), run_id=context.run_id)
            skill_catalog = self.initial_skill_catalog
            if self.skill_manager is not None and not self.prewarm:
                skill_catalog = await self.skill_manager.scan_environment(environment=context.environment)
            if self.on_skill_catalog is not None and not self.prewarm:
                self.on_skill_catalog(skill_catalog or BoundSkillCatalog(items=()))
            # The external agent runs its own model loop. This RunContext only
            # binds Host capabilities and never invokes a native model.
            run_context = RunContext(
                deps=context,
                model=None,  # pyright: ignore[reportArgumentType]
                usage=RunUsage(),
                run_id=context.run_id,
                messages=[*previous.message_history, user_message],
            )
            capabilities = self.capabilities
            if self.shell_review is not None and not self.prewarm:
                capabilities = (*capabilities, await self.shell_review(context.thread_id))
            toolsets = self.toolsets
            binding = self.bind_environment(context)
            interactions = None
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
                        if result.action == "accept" and result.content is not None:
                            return result.content
                        return {"action": result.action}

                    toolsets = (*toolsets, questions_toolset)
            if self.bot:
                bot_tools: FunctionToolset[AgentContext] = FunctionToolset()

                @bot_tools.tool_plain
                def finish_silently() -> None:
                    """Finish without sending a direct Bot reply."""
                    transcript.reset_reply()

                toolsets = (*toolsets, bot_tools)
            async with bridge.prepare(run_context, capabilities=capabilities, toolsets=toolsets) as scope:
                if self.shell_review is not None and not self.prewarm:
                    _bind_shell_review(binding, scope.context, interactions)
                opened_fresh_session = False
                if runtime.session_id is None:
                    recovering = saved.session_id if saved is not None and saved.prompted else None
                    opened_fresh_session = recovering is None
                    workspace_guidance = await to_thread.run_sync(
                        workspace_agents_instructions,
                        self.resource.preset,
                        self.setup.cwd,
                        self.setup.additional_directories,
                    )
                    skills_index = _format_acp_skills_index(skill_catalog)
                    tool_hint = _host_tool_hint(await scope.tool_names())
                    session_instructions = (
                        *self.setup.instructions,
                        *workspace_guidance,
                        *(await scope.instructions()),
                        *((skills_index,) if skills_index is not None else ()),
                        *((tool_hint,) if tool_hint is not None else ()),
                    )
                    await runtime.open_session(
                        AcpSessionSetup(
                            cwd=self.setup.cwd,
                            instructions=session_instructions,
                            mcp_servers=self.setup.mcp_servers,
                            additional_directories=self.setup.additional_directories,
                        ),
                        saved_session_id=recovering,
                        discovery=scope,
                    )
                    if self.prewarm or (saved is not None and saved.options):
                        await runtime.wait_for_model_option(MODEL_OPTION_GRACE_SECONDS)
                    if saved is not None:
                        await runtime.restore_options(
                            saved.options, strict=saved.explicit_options if not self.prewarm else frozenset()
                        )
                elif not self.prewarm and saved is not None:
                    # Replay choices that prewarm could not apply, plus explicit first-turn selections.
                    keys = resident.unrestored_options | saved.explicit_options
                    if keys:
                        await runtime.restore_options(
                            {key: value for key, value in saved.options.items() if key in keys},
                            strict=saved.explicit_options,
                        )
                    resident.unrestored_options = frozenset()
                session_id = runtime.session_id
                assert session_id is not None
                commands = acp_commands(runtime)
                options = {option.id: option.current_value for option in runtime.config_options}
                if self.prewarm and saved is not None:
                    resident.unrestored_options = frozenset(saved.options.keys() - options.keys())
                    options = saved.options | options
                initial_context_window = (
                    runtime.client.context_window
                    if runtime.client.context_window_version > 0 or runtime.client.context_window is not None
                    else (saved.context_window if saved else None)
                )
                active = AcpSessionState(
                    digest=self.digest,
                    thread_id=context.thread_id,
                    session_id=session_id,
                    prompted=saved is not None and saved.prompted,
                    commands=commands if commands is not None else (saved.commands if saved else ()),
                    explicit_options=frozenset(
                        key
                        for key in saved.explicit_options
                        if key in saved.options and options.get(key) == saved.options[key]
                    )
                    if saved is not None
                    else frozenset(),
                    synced_messages=saved.synced_messages if saved and saved.prompted else 0,
                    synced_message_key=saved.synced_message_key if saved and saved.prompted else None,
                    delivered_context=(
                        saved.delivered_context
                        if saved is not None and saved.prompted and not opened_fresh_session
                        else ()
                    ),
                    context_window=initial_context_window,
                    options=options,
                )
                if self.persist is not None and active != saved:
                    await self.persist(active)
                persisted_context_window = active.context_window
                if self.prewarm:
                    return HarnessRunResult(
                        thread_id=context.thread_id,
                        run_id=context.run_id,
                        status="completed",
                        output=None,
                        state=previous,
                        usage=context.usage_attribution.summary(),
                        _messages=previous.message_history,
                        _new_message_index=len(previous.message_history),
                    )
                assert exchange.input.value is not None
                binding.host_tools = scope
                compacted = False

                async def observe(update: AcpUpdate) -> None:
                    nonlocal compacted, active, persisted_context_window
                    if isinstance(update, schema.SessionUpdateCompactionUpdate) and update.status == "completed":
                        compacted = True
                        runtime.client.clear_context_window()
                        active = active.model_copy(update={"context_window": None})
                    elif isinstance(update, schema.UsageUpdate) and update.size > 0:
                        active = active.model_copy(update={"context_window": (update.used, update.size)})
                    transcript.update(update)
                    if (
                        isinstance(update, schema.SessionInfoUpdate)
                        and runtime.client.session_title
                        and self.update_title
                    ):
                        await self.update_title(runtime.client.session_title)
                    if isinstance(update, schema.AvailableCommandsUpdate):
                        active = active.model_copy(update={"commands": acp_commands(runtime)})
                        if self.persist is not None:
                            await self.persist(active)
                            persisted_context_window = active.context_window
                    await publish()

                binding.on_update = observe
                for event in input_events(exchange.input.value, source="user", input_id=context.run_id):
                    await context.events.emit(event)
                transcript.start()
                self.runtime = runtime

                prior_conversation = _prior_conversation(previous.message_history, active)
                prompted_this_turn = False

                async def prompt_until_settled() -> schema.PromptResponse:
                    nonlocal active, compacted, persisted_context_window, prior_conversation, prompted_this_turn
                    prompt = blocks
                    while True:
                        compacted = False
                        guidance = self.goal.get_instructions() if self.goal is not None else None
                        history = ModelContextHistory(active.delivered_context)
                        raw_blocks = await scope.prompt_context_blocks(
                            history=history,
                            exclude_sources=_EXCLUDED_ACP_CONTEXT_SOURCES,
                        )
                        context_blocks = tuple(
                            block
                            for block in raw_blocks
                            if not (block.source_id == _INBOX_REMINDER_SOURCE and history.contains(block))
                        )
                        hidden = (*(block.content for block in context_blocks), *prior_conversation)
                        if guidance is not None:
                            hidden = (*hidden, guidance)
                        if self._stop_requested:
                            return schema.PromptResponse(stop_reason="cancelled")
                        first = not active.prompted
                        explicit = active.explicit_options
                        if first:
                            # Persist before dispatch so an interruption with unknown outcome still recovers.
                            active = active.model_copy(update={"prompted": True, "explicit_options": frozenset()})
                            if self.persist is not None:
                                await self.persist(active)
                                persisted_context_window = active.context_window
                        try:
                            response = await runtime.prompt(prompt, binding=binding, context=hidden)
                        except Exception:
                            if first and runtime.prompt_rejected_before_progress:
                                active = active.model_copy(update={"prompted": False, "explicit_options": explicit})
                                if self.persist is not None:
                                    await self.persist(active)
                                    persisted_context_window = active.context_window
                            raise
                        prompted_this_turn = True
                        prior_conversation = ()
                        active = active.model_copy(
                            update={
                                "delivered_context": ()
                                if compacted
                                else _merge_delivered_context(active.delivered_context, context_blocks)
                            }
                        )
                        if response.usage is not None:
                            await self._record_usage(context, runtime, response)
                        if self._stop_requested:
                            return schema.PromptResponse(stop_reason="cancelled")
                        if response.stop_reason == "cancelled" or self.goal is None:
                            return response
                        if compacted:
                            await self.goal.observe_restore(run_context, "acp_compaction")
                        continuation: list[str] = []
                        await self.goal.after_reply(run_context, transcript.last_text, continuation.append)
                        if self._stop_requested:
                            return schema.PromptResponse(stop_reason="cancelled")
                        if not continuation:
                            return response
                        transcript.reset_reply()
                        prompt = [schema.TextContentBlock(text=continuation[0])]

                persisted_final = False
                try:
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
                    commands = acp_commands(runtime)
                    active = active.model_copy(
                        update={
                            "options": {option.id: option.current_value for option in runtime.config_options},
                            "commands": commands if commands is not None else active.commands,
                        }
                    )
                    transcript.finish()
                    await publish()
                    cancelled = response.stop_reason == "cancelled"
                    output = transcript.last_text or None
                    history = previous.message_history
                    messages = (*history, user_message)
                    if output:
                        messages = (*messages, ModelResponse(parts=[TextPart(content=output)], run_id=context.run_id))
                    if prompted_this_turn:
                        active = active.model_copy(
                            update={
                                "synced_messages": len(messages),
                                "synced_message_key": _message_key(messages[-1]),
                            }
                        )
                    if self.persist is not None:
                        await self.persist(active)
                        persisted_context_window = active.context_window
                    persisted_final = True
                    await context.state.write(_NAMESPACE, active, version="1")
                finally:
                    if (
                        not persisted_final
                        and self.persist is not None
                        and active.context_window != persisted_context_window
                    ):
                        try:
                            with CancelScope(shield=True):
                                await self.persist(active)
                                persisted_context_window = active.context_window
                        except Exception:
                            pass
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
