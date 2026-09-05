"""WebUI-only root Thread collaboration tools over detached Agent UI commands and queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tools import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from a13n_ui.errors import AgentUiError
from a13n_ui.root_run import RootRunCoordinator
from a13n_ui.surfaces import NewThreadDefaults, ThreadSummary
from a13n_ui.thread_projection import ThreadProjectionService

_THREAD_CAPABILITY_ID = "a13n.agent-ui.thread-collaboration"
_MAX_OUTPUT_CHARS = 64 * 1024


class ThreadCreator(Protocol):
    async def __call__(
        self,
        *,
        defaults: NewThreadDefaults,
        title: str | None = None,
    ) -> ThreadSummary: ...


class ThreadToolController:
    """Narrow detached command/query boundary consumed by the root Capability."""

    def __init__(
        self,
        *,
        projections: ThreadProjectionService,
        root_runs: RootRunCoordinator,
        create_thread: ThreadCreator,
    ) -> None:
        self._projections = projections
        self._root_runs = root_runs
        self._create_thread = create_thread

    async def list_threads(
        self,
        *,
        query: str | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        page = await self._projections.list_threads(query=query, cursor=cursor, limit=limit)
        return page.model_dump(mode="json")

    async def get_thread(
        self,
        *,
        thread_id: str,
        history_cursor: str | None,
        history_limit: int,
    ) -> dict[str, Any]:
        detail = await self._projections.detail(thread_id)
        transcript = await self._projections.transcript(
            thread_id=thread_id,
            cursor=history_cursor,
            limit=history_limit,
        )
        return {
            "thread": detail.model_dump(mode="json"),
            "transcript": transcript.model_dump(mode="json"),
        }

    async def run_thread(self, *, thread_id: str, prompt: str) -> dict[str, Any]:
        receipt = await self._root_runs.submit_prompt(thread_id=thread_id, prompt=prompt)
        return receipt.model_dump(mode="json")

    async def create_thread(
        self,
        *,
        source_thread_id: str,
        prompt: str,
        title: str | None,
        agent_id: str | None,
    ) -> dict[str, Any]:
        source = await self._projections.detail(source_thread_id)
        configuration = source.thread.configuration
        created = await self._create_thread(
            defaults=NewThreadDefaults(
                project_id=configuration.project_id,
                agent_id=agent_id or configuration.agent_source.id,
                environment_profile_id=configuration.environment_profile_id,
                harness_plugin_ids=configuration.harness_plugin_ids,
                environment_run_extension_ids=configuration.environment_run_extension_ids,
                mcp_server_ids=configuration.mcp_server_ids,
            ),
            title=title,
        )
        try:
            receipt = await self.run_thread(thread_id=created.thread_id, prompt=prompt)
        except AgentUiError as exc:
            # Creation and run admission are separate durable effects. Never hide the created identity.
            return {**_failure(exc, "thread_run_failed"), "thread_id": created.thread_id}
        return {"ok": True, "thread_id": created.thread_id, "receipt": receipt}

    async def steer_thread(self, *, thread_id: str, message: str) -> dict[str, Any]:
        operation = await self._root_runs.active(thread_id)
        if operation is None:
            return {"accepted": False, "receipt_id": None, "enqueue_id": None}
        result = await self._root_runs.steer(
            receipt_id=operation.receipt.receipt_id,
            message=message,
        )
        return result.model_dump(mode="json")


@dataclass(kw_only=True, slots=True)
class ThreadCollaborationCapability(AbstractCapability[AgentContext]):
    """Expose bounded cross-Thread operations only to one root invocation."""

    controller: ThreadToolController
    source_thread_id: str
    id: str | None = _THREAD_CAPABILITY_ID

    def __post_init__(self) -> None:
        if self.id != _THREAD_CAPABILITY_ID:
            raise ValueError(f"ThreadCollaborationCapability.id must be {_THREAD_CAPABILITY_ID!r}")
        if not self.source_thread_id:
            raise ValueError("source_thread_id must not be blank")

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                _tool(self.list_threads, name="list_threads", effects={"read"}),
                _tool(self.get_thread, name="get_thread", effects={"read"}),
                _tool(
                    self.create_thread,
                    name="create_thread",
                    effects={"read", "write", "external_communication"},
                    idempotency="none",
                ),
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
            return {
                "ok": True,
                **await self.controller.list_threads(query=query, cursor=cursor, limit=limit),
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
            return {
                "ok": True,
                **await self.controller.get_thread(
                    thread_id=thread_id,
                    history_cursor=history_cursor,
                    history_limit=history_limit,
                ),
            }
        except (AgentUiError, ValueError) as exc:
            return _failure(exc, "thread_get_failed")

    async def run_thread(
        self,
        ctx: RunContext[AgentContext],
        thread_id: str,
        prompt: str,
    ) -> dict[str, Any]:
        """Start another idle Thread and return its admission receipt without waiting for completion."""
        self._require_context(ctx)
        if thread_id == self.source_thread_id:
            return _failure_code(
                "thread_recursive_run",
                "A Thread cannot recursively run itself from its active root invocation.",
            )
        try:
            receipt = await self.controller.run_thread(thread_id=thread_id, prompt=prompt)
            return {"ok": True, "receipt": receipt}
        except AgentUiError as exc:
            return _failure(exc, "thread_run_failed")

    async def create_thread(
        self,
        ctx: RunContext[AgentContext],
        prompt: str,
        title: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Create work in this Thread's Project and return immediately after run admission.

        The returned receipt is not completion. Use get_thread to inspect progress.
        If admission fails after creation, the returned thread_id remains valid; do not create a duplicate.
        """
        self._require_context(ctx)
        if not prompt.strip():
            return _failure_code("thread_prompt_empty", "A non-empty initial prompt is required.")
        try:
            return await self.controller.create_thread(
                source_thread_id=self.source_thread_id,
                prompt=prompt,
                title=title,
                agent_id=agent_id,
            )
        except AgentUiError as exc:
            return _failure(exc, "thread_create_failed")

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
            result = await self.controller.steer_thread(thread_id=thread_id, message=message)
            return {"ok": result["accepted"], **result}
        except AgentUiError as exc:
            return _failure(exc, "thread_steer_failed")

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        if ctx.deps.thread_id != self.source_thread_id:
            raise DefinitionError(
                "Agent UI Thread tools cannot cross root Run scope.",
                code="capability_scope_invalid",
            )


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


def _bounded(value: str) -> str:
    if len(value) <= _MAX_OUTPUT_CHARS:
        return value
    return value[: _MAX_OUTPUT_CHARS - 23] + "\n...[output truncated]"


def _failure(exc: Exception, fallback_code: str) -> dict[str, Any]:
    if isinstance(exc, AgentUiError):
        return _failure_code(exc.code, str(exc))
    return _failure_code(fallback_code, str(exc))


def _failure_code(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": _bounded(message)}}


__all__ = ["ThreadCollaborationCapability", "ThreadToolController"]
