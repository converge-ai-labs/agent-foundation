"""Shared native-first dispatch for detached file and URL media."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import ValidationError
from pydantic_ai import BinaryContent, RunContext, ToolReturn
from pydantic_ai.exceptions import UsageLimitExceeded

from a13n_harness.context import AgentContext
from a13n_harness.errors import HarnessError
from a13n_harness.metering import ModelUsageBinding
from a13n_harness.spec import HarnessModelCharacteristics, ModelCapability
from a13n_harness.usage import ProviderUsage

from ._results import ToolFailure, tool_failure
from .file_media import (
    AgentMediaUnderstandingProvider,
    MediaUnderstandingAvailability,
    MediaUnderstandingError,
    MediaUnderstandingProvider,
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
    NativeInputMediaKind,
    environment_media_kinds,
)


def supports_native_media(characteristics: HarnessModelCharacteristics | None, kind: NativeInputMediaKind) -> bool:
    return characteristics is not None and ModelCapability(f"{kind}_understanding") in characteristics.capabilities


def resolve_media_understanding(
    ctx: RunContext[AgentContext], kind: NativeInputMediaKind
) -> MediaUnderstandingProvider | None:
    provider = ctx.deps.file_media_understanding
    return provider if provider is not None else AgentMediaUnderstandingProvider.from_environment(kind=kind)


def available_media_kinds(ctx: RunContext[AgentContext]) -> tuple[NativeInputMediaKind, ...]:
    provider = ctx.deps.file_media_understanding
    fallback = (
        tuple(kind for kind in ("image", "video", "audio") if provider.supports(kind))
        if isinstance(provider, MediaUnderstandingAvailability)
        else environment_media_kinds()
        if provider is None
        else ()
    )
    return tuple(
        kind
        for kind in ("image", "video", "audio")
        if supports_native_media(ctx.deps.model_characteristics, kind) or kind in fallback
    )


async def dispatch_media(
    ctx: RunContext[AgentContext],
    request: MediaUnderstandingRequest,
    *,
    resolve: Callable[[RunContext[AgentContext], NativeInputMediaKind], MediaUnderstandingProvider | None],
    tool_id: str,
    usage_source: str,
    metadata: Any = None,
) -> ToolReturn | ToolFailure:
    if supports_native_media(ctx.deps.model_characteristics, request.kind):
        message = f"The {request.media_type} source {request.source_name} is attached in this tool result."
        if request.instructions:
            message += f"\n\nAnalysis instructions: {request.instructions}"
        return ToolReturn(
            return_value=message,
            metadata=metadata,
            content=[BinaryContent(data=request.source_bytes, media_type=request.media_type)],
        )

    async def record(receipts: Sequence[ProviderUsage]) -> None:
        for receipt in receipts:
            await ctx.deps.record_provider_usage(
                receipt, source=usage_source, tool_id=tool_id, tool_call_id=ctx.tool_call_id
            )

    try:
        provider = resolve(ctx, request.kind)
        if provider is None:
            raise MediaUnderstandingError("media_understanding_unavailable")
        raw = await provider.understand(
            request,
            usage=ModelUsageBinding.for_context(
                ctx.deps, source=usage_source, tool_id=tool_id, tool_call_id=ctx.tool_call_id
            ),
        )
    except (HarnessError, UsageLimitExceeded):
        raise
    except MediaUnderstandingError as exc:
        await record(exc.usage)
        return media_understanding_error(exc.code)
    except Exception:
        return media_understanding_error("media_understanding_failed")
    try:
        result = MediaUnderstandingResult.model_validate(raw)
    except ValidationError:
        return media_understanding_error("media_understanding_response_invalid")
    await record(result.usage)
    return ToolReturn(return_value=result.text, metadata=metadata)


def media_understanding_error(code: str) -> ToolFailure:
    return tool_failure(
        code,
        "Media understanding could not complete. Check the configured media model and provider.",
        retry_hint="dependency_change",
    )
