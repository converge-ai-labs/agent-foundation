"""Root-only Agent UI Thread tools over the shared application service."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tools import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from a13n_ui.errors import AgentUiError
from a13n_ui.storage import Thread

if TYPE_CHECKING:
    from a13n_ui.thread_service import ThreadService

_THREAD_CAPABILITY_ID = "a13n.agent-ui.threads"
_MAX_OUTPUT_CHARS = 64 * 1024


@dataclass(kw_only=True, slots=True)
class AgentUiThreadCapability(AbstractCapability[AgentContext]):
    """Expose bounded cross-Thread operations only to one root invocation."""

    service: ThreadService
    source_thread_id: str
    id: str | None = _THREAD_CAPABILITY_ID

    def __post_init__(self) -> None:
        if self.id != _THREAD_CAPABILITY_ID:
            raise ValueError(f"AgentUiThreadCapability.id must be {_THREAD_CAPABILITY_ID!r}")
        if not self.source_thread_id:
            raise ValueError("source_thread_id must not be blank")

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                _tool(self.list_threads, name="list_threads", effects={"read"}),
                _tool(self.get_thread, name="get_thread", effects={"read"}),
                _tool(
                    self.run_thread,
                    name="run_thread",
                    effects={"read", "write", "external_communication"},
                    idempotency="none",
                ),
                _tool(
                    self.steer_thread,
                    name="steer_thread",
                    effects={"write"},
                    idempotency="none",
                ),
            ],
            id="a13n-agent-ui-thread-tools",
        )

    async def list_threads(
        self,
        ctx: RunContext[AgentContext],
        query: str | None = None,
        cursor: str | None = None,
        limit: int = Field(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        self._require_context(ctx)
        try:
            offset = _decode_cursor(cursor)
            threads, total = await self.service.list(query=query, offset=offset, limit=limit)
            next_offset = offset + len(threads)
            return {
                "ok": True,
                "threads": [_thread_projection(item) for item in threads],
                "total": total,
                "next_cursor": _encode_cursor(next_offset) if next_offset < total else None,
            }
        except (AgentUiError, ValueError) as exc:
            return _failure(exc, "thread_list_failed")

    async def get_thread(
        self,
        ctx: RunContext[AgentContext],
        thread_id: str,
        history_cursor: str | None = None,
        history_limit: int = Field(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        self._require_context(ctx)
        try:
            offset = _decode_cursor(history_cursor)
            thread, history, total = await self.service.inspect(
                thread_id=thread_id,
                history_offset=offset,
                history_limit=history_limit,
            )
            next_offset = offset + len(history)
            return {
                "ok": True,
                "thread": _thread_projection(thread),
                "history": history,
                "history_total": total,
                "next_history_cursor": _encode_cursor(next_offset) if next_offset < total else None,
            }
        except (AgentUiError, ValueError) as exc:
            return _failure(exc, "thread_get_failed")

    async def run_thread(
        self,
        ctx: RunContext[AgentContext],
        thread_id: str,
        prompt: str,
    ) -> dict[str, Any]:
        self._require_context(ctx)
        if thread_id == self.source_thread_id:
            return _failure_code(
                "thread_recursive_run",
                "A Thread cannot recursively run itself from its active root invocation.",
            )
        try:
            outcome = await self.service.run(thread_id=thread_id, prompt=prompt)
            failure = outcome.result.failure
            failure_projection = (
                None
                if failure is None
                else {
                    "code": failure.code,
                    "message": _bounded(failure.message),
                    "retry_hint": failure.retry_hint,
                }
            )
            return {
                "ok": (outcome.result.status == "completed" and outcome.continuation.status == "selected"),
                "thread_id": thread_id,
                "status": outcome.result.status,
                "output": _bounded(outcome.result.output if isinstance(outcome.result.output, str) else None),
                "failure": failure_projection,
                "continuation_status": outcome.continuation.status,
            }
        except AgentUiError as exc:
            return _failure(exc, "thread_run_failed")

    async def steer_thread(
        self,
        ctx: RunContext[AgentContext],
        thread_id: str,
        message: str,
    ) -> dict[str, Any]:
        self._require_context(ctx)
        if thread_id == self.source_thread_id:
            return _failure_code(
                "thread_recursive_steer",
                "A Thread cannot steer itself from its active root invocation.",
            )
        try:
            result = await self.service.steer(thread_id=thread_id, message=message)
            return {
                "ok": result.accepted,
                "thread_id": result.thread_id,
                "accepted": result.accepted,
                "enqueue_id": result.enqueue_id,
            }
        except AgentUiError as exc:
            return _failure(exc, "thread_steer_failed")

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        if ctx.deps.thread_id != self.source_thread_id:
            raise DefinitionError(
                "Agent UI Thread tools cannot cross root Run scope.",
                code="capability_scope_invalid",
            )


def _thread_projection(thread: Thread) -> dict[str, Any]:
    configuration = thread.configuration
    return {
        "thread_id": thread.thread_id,
        "title": thread.title,
        "created_at": thread.created_at.isoformat(),
        "updated_at": thread.updated_at.isoformat(),
        "archived": thread.archived,
        "configuration": {
            "version": configuration.version,
            "project_id": configuration.project_id,
            "agent_source": configuration.agent_source.model_dump(mode="json"),
            "environment_profile_id": configuration.environment_profile_id,
            "harness_plugin_ids": list(configuration.harness_plugin_ids),
            "environment_run_extension_ids": list(configuration.environment_run_extension_ids),
            "mcp_server_ids": list(configuration.mcp_server_ids),
        },
    }


def _tool(
    function: Any,
    *,
    name: str,
    effects: set[str],
    idempotency: Literal["read_only", "none"] = "read_only",
) -> HarnessTool:
    return HarnessTool(
        function,
        harness_metadata=HarnessToolMetadata(
            tool_id=f"agent-ui.thread.{name}",
            effects=cast(Any, frozenset(effects)),
            credential_audiences=(),
            idempotency=idempotency,
            output_policy=ToolOutputPolicy(
                max_inline_bytes=_MAX_OUTPUT_CHARS,
                max_output_bytes=_MAX_OUTPUT_CHARS,
                overflow="truncate",
                redact=True,
            ),
        ),
        name=name,
    )


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"v1:{offset}".encode()).decode().rstrip("=")


def _decode_cursor(value: str | None) -> int:
    if value is None:
        return 0
    if len(value) > 128:
        raise ValueError("Thread cursor is invalid")
    try:
        padded = value + "=" * (-len(value) % 4)
        version, raw_offset = base64.urlsafe_b64decode(padded).decode().split(":", 1)
        offset = int(raw_offset)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Thread cursor is invalid") from exc
    if version != "v1" or offset < 0:
        raise ValueError("Thread cursor is invalid")
    return offset


def _bounded(value: str | None) -> str | None:
    if value is None or len(value) <= _MAX_OUTPUT_CHARS:
        return value
    return value[: _MAX_OUTPUT_CHARS - 23] + "\n...[output truncated]"


def _failure(exc: Exception, fallback_code: str) -> dict[str, Any]:
    if isinstance(exc, AgentUiError):
        return _failure_code(exc.code, str(exc))
    return _failure_code(fallback_code, str(exc))


def _failure_code(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": _bounded(message)}}


__all__ = ["AgentUiThreadCapability"]
