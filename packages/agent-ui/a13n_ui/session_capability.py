"""Root-only Agent UI Session tools over the shared application service."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from a13n_harness.context import AgentContext
from a13n_harness.tools import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from a13n_ui.environment_runtime import WorkspaceBinding
from a13n_ui.errors import AgentUiError

if TYPE_CHECKING:
    from a13n_ui.session_service import SessionService

_SESSION_CAPABILITY_ID = "a13n.agent-ui.sessions"
_MAX_OUTPUT_CHARS = 64 * 1024


@dataclass(kw_only=True, slots=True)
class AgentUiSessionCapability(AbstractCapability[AgentContext]):
    """Expose bounded cross-Session operations only to one root invocation."""

    service: SessionService
    source_session_id: str
    binding: WorkspaceBinding
    id: str | None = _SESSION_CAPABILITY_ID

    def __post_init__(self) -> None:
        if self.id != _SESSION_CAPABILITY_ID:
            raise ValueError(f"AgentUiSessionCapability.id must be {_SESSION_CAPABILITY_ID!r}")
        if not self.source_session_id:
            raise ValueError("source_session_id must not be blank")
        if not isinstance(self.binding, WorkspaceBinding):
            raise TypeError("binding must be a WorkspaceBinding")

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                _tool(self.list_sessions, name="list_sessions", effects={"read"}),
                _tool(self.get_session, name="get_session", effects={"read"}),
                _tool(
                    self.run_session,
                    name="run_session",
                    effects={"read", "write", "external_communication"},
                    idempotency="none",
                ),
                _tool(
                    self.steer_session,
                    name="steer_session",
                    effects={"write"},
                    idempotency="none",
                ),
            ],
            id="a13n-agent-ui-session-tools",
        )

    async def list_sessions(
        self,
        ctx: RunContext[AgentContext],
        query: str | None = None,
        cursor: str | None = None,
        limit: int = Field(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        del ctx
        try:
            offset = _decode_cursor(cursor)
            sessions, total = await self.service.list(query=query, offset=offset, limit=limit)
            next_offset = offset + len(sessions)
            return {
                "ok": True,
                "sessions": [
                    {
                        "session_id": item.session_id,
                        "title": item.title,
                        "created_at": item.created_at.isoformat(),
                        "updated_at": item.updated_at.isoformat(),
                    }
                    for item in sessions
                ],
                "total": total,
                "next_cursor": _encode_cursor(next_offset) if next_offset < total else None,
            }
        except (AgentUiError, ValueError) as exc:
            return _failure(exc, "session_list_failed")

    async def get_session(
        self,
        ctx: RunContext[AgentContext],
        session_id: str,
        history_cursor: str | None = None,
        history_limit: int = Field(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        del ctx
        try:
            offset = _decode_cursor(history_cursor)
            session, history, total = await self.service.inspect(
                session_id=session_id,
                history_offset=offset,
                history_limit=history_limit,
            )
            next_offset = offset + len(history)
            return {
                "ok": True,
                "session": {
                    "session_id": session.session_id,
                    "title": session.title,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                },
                "history": history,
                "history_total": total,
                "next_history_cursor": _encode_cursor(next_offset) if next_offset < total else None,
            }
        except (AgentUiError, ValueError) as exc:
            return _failure(exc, "session_get_failed")

    async def run_session(
        self,
        ctx: RunContext[AgentContext],
        session_id: str,
        prompt: str,
    ) -> dict[str, Any]:
        del ctx
        if session_id == self.source_session_id:
            return _failure_code(
                "session_recursive_run",
                "A Session cannot recursively run itself from its active root invocation.",
            )
        try:
            outcome = await self.service.run(
                session_id=session_id,
                prompt=prompt,
                folders=self.binding.folders,
            )
            failure = outcome.result.failure
            return {
                "ok": outcome.result.status == "completed" and outcome.continuation.status == "selected",
                "session_id": session_id,
                "status": outcome.result.status,
                "output": _bounded(outcome.result.output if isinstance(outcome.result.output, str) else None),
                "failure": (
                    None
                    if failure is None
                    else {
                        "code": failure.code,
                        "message": _bounded(failure.message),
                        "retry_hint": failure.retry_hint,
                    }
                ),
                "continuation_status": outcome.continuation.status,
            }
        except AgentUiError as exc:
            return _failure(exc, "session_run_failed")

    async def steer_session(
        self,
        ctx: RunContext[AgentContext],
        session_id: str,
        message: str,
    ) -> dict[str, Any]:
        del ctx
        if session_id == self.source_session_id:
            return _failure_code(
                "session_recursive_steer",
                "A Session cannot steer itself from its active root invocation.",
            )
        try:
            result = await self.service.steer(session_id=session_id, message=message)
            return {
                "ok": result.accepted,
                "session_id": result.session_id,
                "accepted": result.accepted,
                "enqueue_id": result.enqueue_id,
            }
        except AgentUiError as exc:
            return _failure(exc, "session_steer_failed")


def _tool(
    function: Any,
    *,
    name: str,
    effects: set[str],
    idempotency: str = "read_only",
) -> HarnessTool:
    return HarnessTool(
        function,
        harness_metadata=HarnessToolMetadata(
            tool_id=f"agent-ui.session.{name}",
            effects=cast(Any, frozenset(effects)),
            credential_audiences=(),
            idempotency=cast(Any, idempotency),
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
        raise ValueError("Session cursor is invalid")
    try:
        padded = value + "=" * (-len(value) % 4)
        version, raw_offset = base64.urlsafe_b64decode(padded).decode().split(":", 1)
        offset = int(raw_offset)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Session cursor is invalid") from exc
    if version != "v1" or offset < 0:
        raise ValueError("Session cursor is invalid")
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


__all__ = ["AgentUiSessionCapability"]
