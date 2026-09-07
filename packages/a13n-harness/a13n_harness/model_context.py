"""Typed dynamic model-context projection and its single request commit boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from copy import copy
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import (
    BaseToolCallPart,
    BaseToolReturnPart,
    CapabilityEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextContent,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError

MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID = "a13n.model-context-coordinator"
_MODEL_CONTEXT_METADATA_KEY = "a13n.model-context-overlay"
_MODEL_CONTEXT_METADATA_VERSION = "1"
_MAX_BLOCKS = 128
_MAX_SOURCE_ID_LENGTH = 256
_MAX_BLOCK_BYTES = 2 * 1024 * 1024
_MAX_AGGREGATE_BYTES = 2 * 1024 * 1024


@dataclass(kw_only=True)
class ModelInputEvent(CapabilityEvent, namespace="a13n.context", name="model_input"):
    """Fresh native input content; transport adapters own presentation projection."""

    content: list[UserContent]


def user_prompt_content(part: UserPromptPart) -> list[UserContent]:
    """Observe native content, making plain text metadata-capable without editing history."""
    items = [part.content] if isinstance(part.content, str) else part.content
    return [TextContent(item) if isinstance(item, str) else item for item in items]


class ModelContextRequestKind(StrEnum):
    """Eligible semantic request boundaries."""

    INPUT = "input"
    TOOL_RESULTS = "tool_results"


class ModelContextInputOrigin(StrEnum):
    """Provenance of an input boundary when the producer makes it explicit."""

    USER = "user"
    ENQUEUE = "enqueue"


class ModelContextPlacement(StrEnum):
    """The two structurally safe locations for projected blocks."""

    INPUT_PREAMBLE = "input_preamble"
    REQUEST_EPILOGUE = "request_epilogue"


@dataclass(frozen=True, slots=True)
class ModelContextProjectionRequest:
    """Detached description of the current eligible model request."""

    kind: ModelContextRequestKind
    input_origin: ModelContextInputOrigin | None = None
    tool_call_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_call_ids", tuple(self.tool_call_ids))


@dataclass(frozen=True, slots=True)
class ModelContextBlock:
    """One source-owned context block awaiting structural placement."""

    source_id: str
    placement: ModelContextPlacement
    content: str


@dataclass(frozen=True, slots=True)
class ModelContextProjection:
    """An immutable ordered set of projected blocks."""

    blocks: tuple[ModelContextBlock, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", tuple(self.blocks))


ModelContextNext = Callable[[ModelContextProjectionRequest], Awaitable[ModelContextProjection]]


class ModelContextMiddleware(Protocol):
    """Fresh Host middleware around one run's default projection chain."""

    async def wrap_model_context(
        self,
        ctx: AgentContext,
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection: ...


class AbstractModelContextCapability(AbstractCapability[AgentContext]):
    """Capability subtype that transforms detached dynamic model context."""

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        return await handler(request)


@dataclass(init=False)
class ModelContextCoordinatorCapability(AbstractCapability[AgentContext]):
    """Classify, resolve, validate, and commit exactly one request overlay."""

    id = MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: Callable[[ModelRequestContext], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if _requires_exact_boundary(ctx, request_context.messages):
            return await handler(request_context)
        request = _classify_request(ctx, request_context.messages)
        if request is None:
            return await handler(request_context)

        async def terminal(current: ModelContextProjectionRequest) -> ModelContextProjection:
            return await ctx.deps.project_model_context(current)

        projection_handler: ModelContextNext = terminal
        capabilities = tuple(
            capability
            for capability in ctx.capabilities.values()
            if isinstance(capability, AbstractModelContextCapability)
        )
        for capability in reversed(capabilities):
            inner = projection_handler

            async def wrapped(
                current: ModelContextProjectionRequest,
                *,
                capability: AbstractModelContextCapability = capability,
                inner: ModelContextNext = inner,
            ) -> ModelContextProjection:
                return await capability.wrap_model_context(ctx, current, inner)

            projection_handler = wrapped

        host = ctx.deps.model_context
        if host is not None:
            inner = projection_handler

            async def host_wrapped(current: ModelContextProjectionRequest) -> ModelContextProjection:
                return await host.wrap_model_context(ctx.deps, current, inner)

            projection_handler = host_wrapped

        projection = await projection_handler(request)
        _validate_projection(projection, request)
        original_request = request_context.messages[-1]
        assert isinstance(original_request, ModelRequest)
        committed = _commit_projection(request_context.messages, request, projection)
        _persist_projection(ctx.messages, original_request, committed, request, projection)
        # Observe only freshly produced overlay content. Native user input and
        # delivered steering have their own event boundaries, never history replay.
        if projection.blocks:
            await ctx.emit(
                ModelInputEvent(
                    content=[
                        TextContent(block.content, metadata={"display": False, "source_id": block.source_id})
                        for block in projection.blocks
                    ]
                )
            )
        return await handler(_replace_messages(request_context, committed))


def _classify_request(
    ctx: RunContext[AgentContext],
    messages: list[ModelMessage],
) -> ModelContextProjectionRequest | None:
    if not messages or not isinstance(messages[-1], ModelRequest):
        return None
    final = messages[-1]
    if any(isinstance(part, RetryPromptPart) for part in final.parts):
        return None

    tool_results = tuple(part for part in final.parts if isinstance(part, BaseToolReturnPart))
    if tool_results:
        if len(tool_results) != len(final.parts):
            return None
        return ModelContextProjectionRequest(
            kind=ModelContextRequestKind.TOOL_RESULTS,
            tool_call_ids=tuple(part.tool_call_id for part in tool_results),
        )

    if any(isinstance(part, UserPromptPart) for part in final.parts):
        # Pydantic does not expose reliable enqueue provenance on ordinary text input.
        return ModelContextProjectionRequest(
            kind=ModelContextRequestKind.INPUT,
            input_origin=ModelContextInputOrigin.USER,
        )
    return None


def _validate_projection(
    projection: ModelContextProjection,
    request: ModelContextProjectionRequest,
) -> None:
    if not isinstance(projection, ModelContextProjection):
        raise DefinitionError(
            "Model context middleware must return ModelContextProjection.",
            code="model_context_projection_invalid",
        )
    if len(projection.blocks) > _MAX_BLOCKS:
        raise DefinitionError("Model context contains too many blocks.", code="model_context_projection_invalid")

    aggregate_bytes = 0
    for block in projection.blocks:
        if not isinstance(block, ModelContextBlock):
            raise DefinitionError(
                "Model context projection contains an invalid block.",
                code="model_context_projection_invalid",
            )
        if (
            not isinstance(block.source_id, str)
            or not block.source_id.strip()
            or block.source_id != block.source_id.strip()
            or len(block.source_id) > _MAX_SOURCE_ID_LENGTH
            or "\x00" in block.source_id
        ):
            raise DefinitionError(
                "Model context source_id must be a non-blank bounded string.",
                code="model_context_projection_invalid",
            )
        if not isinstance(block.placement, ModelContextPlacement) or not isinstance(block.content, str):
            raise DefinitionError(
                "Model context block placement or content is invalid.",
                code="model_context_projection_invalid",
            )
        size = len(block.content.encode("utf-8"))
        if size > _MAX_BLOCK_BYTES:
            raise DefinitionError(
                "Model context block exceeds its byte limit.", code="model_context_projection_invalid"
            )
        aggregate_bytes += size
        if (
            request.kind is ModelContextRequestKind.TOOL_RESULTS
            and block.placement is ModelContextPlacement.INPUT_PREAMBLE
        ):
            raise DefinitionError(
                "Tool-result context cannot use INPUT_PREAMBLE placement.",
                code="model_context_placement_invalid",
            )
    if aggregate_bytes > _MAX_AGGREGATE_BYTES:
        raise DefinitionError(
            "Model context exceeds its aggregate byte limit.", code="model_context_projection_invalid"
        )


def _commit_projection(
    messages: list[ModelMessage],
    request: ModelContextProjectionRequest,
    projection: ModelContextProjection,
) -> list[ModelMessage]:
    if not projection.blocks:
        return messages
    final = messages[-1]
    assert isinstance(final, ModelRequest)
    original_parts = list(final.parts)
    preamble = [block for block in projection.blocks if block.placement is ModelContextPlacement.INPUT_PREAMBLE]
    epilogue = [block for block in projection.blocks if block.placement is ModelContextPlacement.REQUEST_EPILOGUE]

    if request.kind is ModelContextRequestKind.INPUT and preamble:
        input_index = next(index for index, part in enumerate(original_parts) if isinstance(part, UserPromptPart))
    else:
        input_index = len(original_parts)

    inserted_parts = [
        UserPromptPart([TextContent(block.content, metadata={"display": False, "source_id": block.source_id})])
        for block in preamble
    ]
    parts = [*original_parts[:input_index], *inserted_parts, *original_parts[input_index:]]
    parts.extend(
        UserPromptPart([TextContent(block.content, metadata={"display": False, "source_id": block.source_id})])
        for block in epilogue
    )
    inserted_indexes = [*range(input_index, input_index + len(preamble))]
    inserted_indexes.extend(range(len(parts) - len(epilogue), len(parts)))
    inserted_blocks = [*preamble, *epilogue]

    metadata = dict(final.metadata or {})
    metadata[_MODEL_CONTEXT_METADATA_KEY] = {
        "version": _MODEL_CONTEXT_METADATA_VERSION,
        "parts": [
            {
                "index": index,
                "source_id": block.source_id,
                "sha256": sha256(block.content.encode("utf-8")).hexdigest(),
            }
            for index, block in zip(inserted_indexes, inserted_blocks, strict=True)
        ],
    }
    updated = list(messages)
    updated[-1] = replace(final, parts=tuple(parts), metadata=metadata)
    return updated


def _persist_projection(
    active_messages: list[ModelMessage],
    original_request: ModelRequest,
    committed_messages: list[ModelMessage],
    request: ModelContextProjectionRequest,
    projection: ModelContextProjection,
) -> None:
    """Record the exact outgoing overlay on its active-history request."""
    if not projection.blocks:
        return
    committed_request = committed_messages[-1]
    assert isinstance(committed_request, ModelRequest)
    for index in range(len(active_messages) - 1, -1, -1):
        if active_messages[index] is original_request:
            active_messages[index] = committed_request
            return
    active_messages[:] = _commit_projection(active_messages, request, projection)


def _remove_owned_overlays(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    updated = list(messages)
    for message_index, message in enumerate(updated):
        if not isinstance(message, ModelRequest) or not message.metadata:
            continue
        ownership = message.metadata.get(_MODEL_CONTEXT_METADATA_KEY)
        if ownership is None:
            continue
        if not isinstance(ownership, dict) or ownership.get("version") != _MODEL_CONTEXT_METADATA_VERSION:
            raise DefinitionError("Model context ownership metadata is invalid.", code="model_context_overlay_invalid")
        owned_parts = ownership.get("parts")
        if not isinstance(owned_parts, list):
            raise DefinitionError("Model context ownership metadata is invalid.", code="model_context_overlay_invalid")
        parts = list(message.parts)
        indexes: list[int] = []
        for owned in owned_parts:
            if not isinstance(owned, dict):
                raise DefinitionError(
                    "Model context ownership metadata is invalid.", code="model_context_overlay_invalid"
                )
            index = owned.get("index")
            digest = owned.get("sha256")
            if not isinstance(index, int) or not isinstance(digest, str) or index < 0 or index >= len(parts):
                raise DefinitionError(
                    "Model context ownership metadata is stale.", code="model_context_overlay_invalid"
                )
            part = parts[index]
            content = part.content if isinstance(part, UserPromptPart) else None
            if isinstance(content, (list, tuple)) and len(content) == 1 and isinstance(content[0], TextContent):
                content = content[0].content
            if not isinstance(content, str) or sha256(content.encode("utf-8")).hexdigest() != digest:
                raise DefinitionError(
                    "Model context ownership metadata is stale.", code="model_context_overlay_invalid"
                )
            indexes.append(index)
        if len(indexes) != len(set(indexes)):
            raise DefinitionError("Model context ownership metadata is invalid.", code="model_context_overlay_invalid")
        for index in sorted(indexes, reverse=True):
            parts.pop(index)
        metadata = dict(message.metadata)
        metadata.pop(_MODEL_CONTEXT_METADATA_KEY, None)
        updated[message_index] = replace(message, parts=tuple(parts), metadata=metadata or None)
    return updated


def _requires_exact_boundary(ctx: RunContext[AgentContext], messages: list[ModelMessage]) -> bool:
    return _requires_exact_history(messages) or _is_deferred_result_boundary(ctx, messages)


def _is_deferred_result_boundary(ctx: RunContext[AgentContext], messages: list[ModelMessage]) -> bool:
    resume = ctx.deps.deferred_resume
    if resume is None or not messages or not isinstance(messages[-1], ModelRequest):
        return False
    expected = {part.tool_call_id for part in (*resume.requests.calls, *resume.requests.approvals)}
    integrated = {
        part.tool_call_id for part in messages[-1].parts if isinstance(part, BaseToolReturnPart | RetryPromptPart)
    }
    return bool(expected) and expected <= integrated


def _requires_exact_history(messages: list[ModelMessage]) -> bool:
    if messages and isinstance(messages[-1], ModelResponse) and messages[-1].state == "suspended":
        return True
    pending: set[str] = set()
    for message in messages:
        if isinstance(message, ModelResponse):
            pending.update(part.tool_call_id for part in message.parts if isinstance(part, BaseToolCallPart))
        elif isinstance(message, ModelRequest):
            pending.difference_update(
                part.tool_call_id for part in message.parts if isinstance(part, BaseToolReturnPart | RetryPromptPart)
            )
    return bool(pending)


def _replace_messages(request_context: ModelRequestContext, messages: list[ModelMessage]) -> ModelRequestContext:
    updated = copy(request_context)
    updated.messages = messages
    return updated


__all__ = [
    "AbstractModelContextCapability",
    "ModelContextBlock",
    "ModelContextInputOrigin",
    "ModelContextMiddleware",
    "ModelContextNext",
    "ModelContextPlacement",
    "ModelContextProjection",
    "ModelContextProjectionRequest",
    "ModelContextRequestKind",
    "ModelInputEvent",
    "user_prompt_content",
]
