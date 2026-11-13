"""Typed dynamic model-context projection and its single request commit boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from copy import copy, deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import (
    BaseToolCallPart,
    BaseToolReturnPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryFeedbackPart,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    ToolAvailabilityDeltaPart,
    ToolCallPart,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext

from a13n_harness.content import (
    ContentItem,
    ContentMetadata,
    replace_request_parts,
    request_input_content,
    request_parts,
)
from a13n_harness.context import AgentContext
from a13n_harness.context_breakdown import ContextGroupName
from a13n_harness.errors import DefinitionError
from a13n_harness.events import input_events

MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID = "a13n.model-context-coordinator"
_MAX_BLOCKS = 128
_MAX_SOURCE_ID_LENGTH = 256
_MAX_BLOCK_BYTES = 2 * 1024 * 1024
_MAX_AGGREGATE_BYTES = 2 * 1024 * 1024


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
class ModelContextHistory:
    """Detached, read-only facts about context still retained at this boundary.

    Presence and latest-hash comparisons are observations, never emission policies.
    Querying does not reserve a block or establish a delivery watermark.
    """

    _entries: tuple[tuple[str, str | None, str], ...] = ()
    _unordered: frozenset[tuple[str, str | None]] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "_entries", tuple(self._entries))
        object.__setattr__(self, "_unordered", frozenset(self._unordered))

    @classmethod
    def from_messages(cls, messages: list[ModelMessage]) -> ModelContextHistory:
        entries: list[tuple[str, str | None, str]] = []
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for item in request_input_content(message):
                source = item.metadata.source_id
                marker = (item.metadata.model_extra or {}).get("model_context")
                if source is None or not isinstance(marker, dict):
                    continue
                key = marker.get("key")
                if key is not None and not isinstance(key, str):
                    continue
                value = item.value.content if isinstance(item.value, TextContent) else item.value
                if isinstance(value, str):
                    digest = sha256(value.encode("utf-8")).hexdigest()
                    if marker.get("sha256") == digest:
                        entries.append((source, key, digest))
        return cls(tuple(entries))

    @classmethod
    def from_blocks(
        cls,
        blocks: tuple[ModelContextBlock, ...],
        *,
        unordered: frozenset[tuple[str, str | None]] = frozenset(),
    ) -> ModelContextHistory:
        """Observe confirmed content; unsettled delivery can leave latest order unknown."""
        return cls(tuple((block.source_id, block.key, block.content_hash) for block in blocks), unordered)

    def contains(self, block: ModelContextBlock) -> bool:
        """Whether this source/key/content hash occurs anywhere in retained context."""
        return (block.source_id, block.key, block.content_hash) in self._entries

    def matches_latest(self, block: ModelContextBlock) -> bool:
        """Compare with the most recent same-identity hash; absent or unknown is false."""
        identity = (block.source_id, block.key)
        if identity in self._unordered:
            return False
        latest_hash = next(
            (digest for source, key, digest in reversed(self._entries) if (source, key) == identity),
            None,
        )
        return latest_hash == block.content_hash


@dataclass
class _NoticeBoundary:
    """Transient evidence for the current append. Never stored in messages or portable state."""

    results: tuple[ToolReturnPart, ...] = ()
    completion_ids: frozenset[str] = frozenset()
    result_call_ids: tuple[str, ...] = ()
    confirmations: list[tuple[ModelContextBlock | None, Callable[[], None]]] = field(default_factory=list)

    @classmethod
    def from_request(cls, message: ModelRequest) -> _NoticeBoundary:
        completion_ids = set()
        for item in request_input_content(message):
            extra = item.metadata.model_extra or {}
            if extra.get("notice_kind", item.metadata.source_id) == "tool_execution.completion":
                ids = extra.get("execution_ids")
                if isinstance(ids, list):
                    completion_ids.update(value for value in ids if isinstance(value, str))
        return cls.from_results((p for p in message.parts if isinstance(p, ToolReturnPart)), completion_ids)

    @classmethod
    def from_results(cls, parts: Iterable[ToolReturnPart], completion_ids: Iterable[str] = ()) -> _NoticeBoundary:
        # Keep middleware detached from canonical messages. Other tool results can
        # contain large media or arbitrary Python values; only their call IDs matter.
        results = []
        calls = []
        for part in parts:
            if part.outcome != "success":
                continue
            calls.append(part.tool_call_id)
            value = part.content
            if isinstance(value, dict) and (
                part.tool_name in ("note_write", "note_delete", "task_create", "task_update")
                or value.get("type") == "tool_execution_receipt"
            ):
                results.append(ToolReturnPart(part.tool_name, deepcopy(value), part.tool_call_id))
        return cls(tuple(results), frozenset(completion_ids), tuple(calls))


class _RunNoticeState[T]:
    """One source's last confirmed observation in this logical Run, across native attempts."""

    def __init__(self) -> None:
        self.previous: T | None = None
        self._prepared = self._confirmed = 0

    def observe(self, request: ModelContextProjectionRequest, current: T, block: ModelContextBlock | None) -> None:
        self._prepared += 1
        revision = self._prepared

        def confirm() -> None:
            # Concurrent Live tool sends can settle in a different order.
            if revision > self._confirmed:
                self._confirmed = revision
                self.previous = current

        request._notices.confirmations.append((block, confirm))


@dataclass(frozen=True, slots=True)
class ModelContextProjectionRequest:
    """Detached description of the current eligible model request."""

    kind: ModelContextRequestKind
    input_origin: ModelContextInputOrigin | None = None
    tool_call_ids: tuple[str, ...] = ()
    history: ModelContextHistory = field(default_factory=ModelContextHistory)
    _notices: _NoticeBoundary = field(default_factory=_NoticeBoundary, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_call_ids", tuple(self.tool_call_ids))


@dataclass(frozen=True, slots=True)
class ModelContextBlock:
    """One source-owned context block awaiting structural placement."""

    source_id: str
    placement: ModelContextPlacement
    content: str
    memory_mount: str | None = None
    context_group: ContextGroupName | None = None
    key: str | None = None

    @property
    def content_hash(self) -> str:
        """SHA-256 of the complete rendered UTF-8 text, shared by comparison and metadata."""
        return sha256(self.content.encode("utf-8")).hexdigest()

    def content_item(self) -> ContentItem:
        metadata = ContentMetadata.model_validate(
            {
                "display": False,
                "source_id": self.source_id,
                "model_context": {
                    "key": self.key,
                    "placement": self.placement.value,
                    "sha256": self.content_hash,
                },
            }
        )
        if self.memory_mount is not None:
            metadata = metadata.model_copy(update={"memory_mount": self.memory_mount})
        if self.context_group is not None:
            metadata = metadata.model_copy(update={"context_group": self.context_group})
        return ContentItem(self.content, metadata)


@dataclass(frozen=True, slots=True)
class ModelContextProjection:
    """An immutable ordered set of projected blocks."""

    blocks: tuple[ModelContextBlock, ...] = ()

    _confirmations: tuple[tuple[ModelContextBlock | None, Callable[[], None]], ...] = field(
        default=(), repr=False, compare=False
    )

    def _confirm(self, delivered: tuple[ModelContextBlock, ...]) -> None:
        for block, confirm in self._confirmations:
            if block is None or block in delivered:
                confirm()

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
        return CapabilityOrdering(position="innermost")

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        from a13n_harness.capabilities.context import (
            CompactionCapability,
            _project_compaction_media,
            _restore_compacted_state,
        )
        from a13n_harness.compaction import COMPACTION_CAPABILITY_ID

        compaction = ctx.capabilities.get(COMPACTION_CAPABILITY_ID)
        if ctx.deps._compacting or (isinstance(compaction, CompactionCapability) and compaction.generating_summary):
            return request_context
        if _requires_exact_boundary(ctx, request_context.messages) or _requires_exact_boundary(ctx, ctx.messages):
            return request_context

        restored = await _restore_compacted_state(ctx, request_context)
        restoring_compaction = restored is not request_context
        request_context = restored
        request = _classify_request(ctx, request_context.messages)
        prepared = request_context
        if request is not None:
            original_request = request_context.messages[-1]
            assert isinstance(original_request, ModelRequest)
            request = replace(
                request,
                history=ModelContextHistory.from_messages(request_context.messages),
                _notices=_NoticeBoundary.from_request(original_request),
            )
            projection = await _project_model_context(ctx, request)
            committed = _commit_projection(request_context.messages, request, projection)
            _persist_projection(ctx.messages, original_request, committed, request, projection)
            projection._confirm(projection.blocks)
            # Observe only freshly produced overlays, never history replay.
            for event in input_events(
                [block.content_item() for block in projection.blocks],
                source="context",
                input_id=f"context-{ctx.run_id}-{ctx.run_step}",
            ):
                await ctx.emit(event)
            prepared = _replace_messages(request_context, committed)
        if restoring_compaction:
            prepared = await _project_compaction_media(ctx, prepared)
        return prepared


async def _project_model_context(
    ctx: RunContext[AgentContext], request: ModelContextProjectionRequest
) -> ModelContextProjection:
    """Resolve the same validated projection for request and duplex boundaries."""

    async def terminal(current: ModelContextProjectionRequest) -> ModelContextProjection:
        return await ctx.deps.project_model_context(current)

    projection_handler: ModelContextNext = terminal
    capabilities = tuple(
        capability for capability in ctx.capabilities.values() if isinstance(capability, AbstractModelContextCapability)
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
    return replace(projection, _confirmations=tuple(request._notices.confirmations))


def _classify_request(
    ctx: RunContext[AgentContext],
    messages: list[ModelMessage],
) -> ModelContextProjectionRequest | None:
    if not messages or not isinstance(messages[-1], ModelRequest):
        return None
    final = messages[-1]
    if any(
        isinstance(part, RetryFeedbackPart | RetryPromptPart)
        or (isinstance(part, ToolReturnPart) and part.outcome == "retried")
        for part in final.parts
    ):
        return None

    tool_results = tuple(part for part in final.parts if isinstance(part, BaseToolReturnPart))
    if tool_results:
        if len(tool_results) == len(final.parts):
            return ModelContextProjectionRequest(
                kind=ModelContextRequestKind.TOOL_RESULTS,
                tool_call_ids=tuple(part.tool_call_id for part in tool_results),
            )
        # Native enqueue appends input after the complete result batch.
        # Project at that input without splitting sibling tool results.
        if tuple(final.parts[: len(tool_results)]) != tool_results or not all(
            isinstance(part, UserPromptPart) for part in final.parts[len(tool_results) :]
        ):
            return None

    if any(isinstance(part, UserPromptPart) for part in final.parts):
        # Pydantic does not expose reliable enqueue provenance on ordinary text input.
        return ModelContextProjectionRequest(
            kind=ModelContextRequestKind.INPUT,
            input_origin=ModelContextInputOrigin.USER,
            tool_call_ids=tuple(part.tool_call_id for part in tool_results),
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
    identities: set[tuple[str, str | None]] = set()
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
        if block.key is not None and (not isinstance(block.key, str) or not block.key or len(block.key) > 4096):
            raise DefinitionError("Model context identity is invalid.", code="model_context_projection_invalid")
        identity = (block.source_id, block.key)
        if identity in identities:
            raise DefinitionError(
                "Model context block identities must be unique.", code="model_context_projection_invalid"
            )
        identities.add(identity)
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
    original_parts = request_parts(final)
    preamble = [block for block in projection.blocks if block.placement is ModelContextPlacement.INPUT_PREAMBLE]
    epilogue = [block for block in projection.blocks if block.placement is ModelContextPlacement.REQUEST_EPILOGUE]

    if request.kind is ModelContextRequestKind.INPUT and preamble:
        # Native provider preparation can render typed parts as user input only
        # in the outgoing request (for example ToolAvailabilityDeltaPart).
        # Preserve the same preamble position before the canonical native part.
        input_index = next(
            (
                index
                for index, part in enumerate(original_parts)
                if isinstance(part[0], UserPromptPart | ToolAvailabilityDeltaPart)
            ),
            None,
        )
        if input_index is None:
            # A system-only enqueue renders as user text on fallback providers.
            input_index = next(
                index for index, part in enumerate(original_parts) if isinstance(part[0], SystemPromptPart)
            )
    else:
        input_index = len(original_parts)

    inserted_parts = [
        (
            UserPromptPart(block.content),
            [block.content_item()],
        )
        for block in preamble
    ]
    parts = [*original_parts[:input_index], *inserted_parts, *original_parts[input_index:]]
    parts.extend(
        (
            UserPromptPart(block.content),
            [block.content_item()],
        )
        for block in epilogue
    )
    updated = list(messages)
    updated[-1] = replace_request_parts(final, parts)
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


def _requires_exact_boundary(ctx: RunContext[AgentContext], messages: list[ModelMessage]) -> bool:
    return _requires_exact_history(messages) or _is_deferred_result_boundary(ctx, messages)


def _is_deferred_result_boundary(ctx: RunContext[AgentContext], messages: list[ModelMessage]) -> bool:
    resume = ctx.deps.deferred_resume
    if resume is None or not messages or not isinstance(messages[-1], ModelRequest):
        return False
    expected = {part.model_call.tool_call_id for part in (*resume.requests.calls, *resume.requests.approvals)}
    integrated = {
        part.tool_call_id for part in messages[-1].parts if isinstance(part, BaseToolReturnPart | RetryPromptPart)
    }
    return bool(expected) and expected <= integrated


def _requires_exact_history(messages: list[ModelMessage]) -> bool:
    if messages and isinstance(messages[-1], ModelResponse) and messages[-1].state == "suspended":
        return True
    pending: set[str] = set()
    previous_response_calls: set[str] = set()
    for message in messages:
        if isinstance(message, ModelResponse):
            pending.update(part.tool_call_id for part in message.parts if isinstance(part, BaseToolCallPart))
            previous_response_calls = {part.tool_call_id for part in message.parts if isinstance(part, ToolCallPart)}
        else:
            if any(
                isinstance(part, RetryFeedbackPart) or (isinstance(part, RetryPromptPart) and part.tool_name is None)
                for part in message.parts
            ):
                # A model-level retry rejects the preceding response before function-tool dispatch.
                # Its generated ID does not match those calls. Older and provider-native calls
                # still need their own results; a retry before a response retires no calls.
                pending.difference_update(previous_response_calls)
            previous_response_calls.clear()
        # Provider-executed native tools return inside ModelResponse, not ModelRequest.
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
    "ModelContextHistory",
    "ModelContextInputOrigin",
    "ModelContextMiddleware",
    "ModelContextNext",
    "ModelContextPlacement",
    "ModelContextProjection",
    "ModelContextProjectionRequest",
    "ModelContextRequestKind",
    "user_prompt_content",
]
