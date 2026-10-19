"""Mandatory model-request boundary observations for one logical Harness run."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.agent import CallToolsNode, ModelRequestNode
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.exceptions import ModelHTTPError

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError, HarnessError
from a13n_harness.events import (
    MAX_FAILURE_DETAIL_CHARS,
    ModelRequestCompletedPayload,
    ModelRequestFailedPayload,
    ModelRequestStartedPayload,
    emit_harness_event,
)

LIFECYCLE_EVENT_CAPABILITY_ID = "a13n.lifecycle-events"


@dataclass(init=False)
class LifecycleEventCapability(AbstractCapability[AgentContext]):
    """Code-owned observer for public Pydantic model-request node boundaries."""

    id = LIFECYCLE_EVENT_CAPABILITY_ID

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(LIFECYCLE_EVENT_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _LifecycleEventActiveCapability):
                raise DefinitionError(
                    "Lifecycle events have an incompatible run replacement.",
                    code="capability_type_mismatch",
                )
            return existing
        replacement = _LifecycleEventActiveCapability(context=ctx.deps)
        ctx.deps._record_run_capability(LIFECYCLE_EVENT_CAPABILITY_ID, replacement)
        return replacement

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")


@dataclass(init=False)
class _LifecycleEventActiveCapability(LifecycleEventCapability):
    def __init__(self, *, context: AgentContext) -> None:
        self._context = context
        self._next_request_index = 0
        self._active_request_index: int | None = None

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self._context:
            raise DefinitionError(
                "Lifecycle event observation cannot cross logical runs.",
                code="capability_scope_invalid",
            )
        return self

    @property
    def active_request_index(self) -> int | None:
        return self._active_request_index

    async def wrap_node_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        node: Any,
        handler: Any,
    ) -> Any:
        self._require_context(ctx)
        if not isinstance(node, ModelRequestNode):
            return await handler(node)

        request_index = self._next_request_index
        self._next_request_index += 1
        request_id = f"model-request-{request_index + 1}"
        self._active_request_index = request_index
        await emit_harness_event(
            ctx.deps.events,
            kind="lifecycle",
            payload=ModelRequestStartedPayload(
                request_id=request_id,
                request_index=request_index,
                message_count=len(ctx.messages) + 1,
            ),
        )
        recovery = ctx.deps._model_recovery
        primary_request = recovery.attempt_id is not None and ctx.run_id == recovery.attempt_id
        if primary_request:
            recovery.request_error = None
        try:
            result = await handler(node)
        except BaseException as exc:
            if primary_request:
                recovery.request_error = exc
            try:
                await emit_harness_event(
                    ctx.deps.events,
                    kind="lifecycle",
                    payload=_failed_payload(exc, request_id=request_id, request_index=request_index),
                )
            except BaseException:
                pass
            raise
        else:
            # A retry node, partial stream, or auxiliary Agent response is not
            # accepted progress in the primary execution being recovered.
            if primary_request and isinstance(result, CallToolsNode) and result.model_response.state == "complete":
                recovery.consecutive_failures = 0
            await emit_harness_event(
                ctx.deps.events,
                kind="lifecycle",
                payload=ModelRequestCompletedPayload(
                    request_id=request_id,
                    request_index=request_index,
                ),
            )
            return result
        finally:
            self._active_request_index = None

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        if ctx.deps is not self._context:
            raise DefinitionError(
                "Lifecycle event observation cannot cross logical runs.",
                code="capability_scope_invalid",
            )
        owner = ctx.capabilities.get(LIFECYCLE_EVENT_CAPABILITY_ID)
        if type(owner) is not _LifecycleEventActiveCapability or owner is not self:
            raise DefinitionError(
                "The finalized lifecycle event observer has an incompatible identity.",
                code="capability_scope_invalid",
            )


def active_model_request_index(ctx: RunContext[AgentContext]) -> int:
    """Return the request index allocated by the mandatory lifecycle observer."""
    owner = ctx.capabilities.get(LIFECYCLE_EVENT_CAPABILITY_ID)
    if not isinstance(owner, _LifecycleEventActiveCapability) or owner.active_request_index is None:
        raise DefinitionError(
            "A context snapshot requires an active model-request boundary.",
            code="model_request_boundary_missing",
        )
    return owner.active_request_index


def _failed_payload(exc: BaseException, *, request_id: str, request_index: int) -> ModelRequestFailedPayload:
    """Name why the request failed: the provider's own message for an HTTP error, else the exception type.

    A Harness error is already named by its code. Other exception messages stay out: they can carry anything the
    failing code held.
    """
    error_code = _safe_error_code(exc)
    status_code: int | None = None
    detail: str | None = None
    if isinstance(exc, ModelHTTPError):
        status_code = exc.status_code
        detail = _provider_message(exc.body)
    elif not isinstance(exc, HarnessError | asyncio.CancelledError):
        detail = type(exc).__name__
    return ModelRequestFailedPayload(
        request_id=request_id,
        request_index=request_index,
        error_code=error_code,
        status_code=status_code,
        detail=detail,
    )


def _provider_message(body: object) -> str | None:
    """The message of a provider error body: its `error.message`, a top-level `message`, or the whole body."""
    if isinstance(body, dict):
        for container in (body.get("error"), body):
            if isinstance(container, dict) and isinstance(container.get("message"), str):
                return _bounded(container["message"])
    if body is None:
        return None
    return _bounded(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str))


def _bounded(text: str) -> str | None:
    text = text.strip()
    if len(text) > MAX_FAILURE_DETAIL_CHARS:
        text = text[: MAX_FAILURE_DETAIL_CHARS - 1] + "…"
    return text or None


def _safe_error_code(exc: BaseException) -> str:
    if isinstance(exc, HarnessError) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", exc.code):
        return exc.code
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "model_request_failed"
