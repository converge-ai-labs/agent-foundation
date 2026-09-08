"""Optional model-backed review for marked shell command launches."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from functools import cache
from importlib.resources import files
from typing import Protocol, cast, runtime_checkable
from uuid import uuid4

from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import Agent, PromptedOutput, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import AgentStreamEvent
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.usage import ProviderUsage, UsageMeasure

SHELL_REVIEW_CAPABILITY_ID = "a13n.shell-review"
SHELL_EXEC_TOOL_ID = "environment.shell_exec"
MAX_SHELL_REVIEW_REASON_CHARS = 2_000
_LOGGER = get_logger(__name__)


class ShellRiskLevel(StrEnum):
    """Ordered risk classification returned by a shell command reviewer."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTRA_HIGH = "extra_high"


class ShellReviewAction(StrEnum):
    """Review response; skipping adds no restriction to the invocation policy."""

    APPROVAL_REQUIRED = "approval_required"
    DENY = "deny"
    SKIP = "skip"


_RISK_ORDER = {
    ShellRiskLevel.LOW: 0,
    ShellRiskLevel.MEDIUM: 1,
    ShellRiskLevel.HIGH: 2,
    ShellRiskLevel.EXTRA_HIGH: 3,
}


class ShellReviewRequest(BaseModel):
    """Bounded command facts projected from normalized managed arguments."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)

    tool_id: str = Field(min_length=1, max_length=256)
    tool_call_id: str = Field(min_length=1, max_length=512)
    command: str = Field(min_length=1, max_length=1024 * 1024)
    cwd: str | None = Field(default=None, max_length=16 * 1024)
    environment_keys: tuple[str, ...] = Field(default=(), max_length=4_096)
    yield_time_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    execution_timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    alias: str | None = Field(default=None, max_length=2_048)

    @field_validator("command", "cwd", "alias")
    @classmethod
    def _reject_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("shell review text must not contain NUL")
        return value

    @field_validator("environment_keys")
    @classmethod
    def _validate_environment_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > 4_096 or "\x00" in item for item in value):
            raise ValueError("environment keys must be bounded non-blank strings")
        if tuple(sorted(set(value))) != value:
            raise ValueError("environment keys must be unique and sorted")
        return value


class ShellReviewAssessment(BaseModel):
    """One bounded, non-authoritative shell risk assessment."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    risk: ShellRiskLevel
    reason: str = Field(min_length=1, max_length=MAX_SHELL_REVIEW_REASON_CHARS)

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        reason = value.strip()
        if not reason or "\x00" in reason:
            raise ValueError("shell review reason must be non-blank and contain no NUL")
        return reason


class ShellReviewResult(BaseModel):
    """Assessment plus provider usage proven by its implementation."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    assessment: ShellReviewAssessment
    usage: tuple[ProviderUsage, ...] = Field(default=(), max_length=16)


class ShellReviewError(Exception):
    """Review failure carrying any provider usage proven before failure."""

    def __init__(self, code: str, *, usage: Sequence[ProviderUsage] = ()) -> None:
        if not isinstance(code, str) or not code.strip() or len(code) > 128:
            raise ValueError("shell review error code must be a short non-blank string")
        self.code = code
        self.usage = tuple(ProviderUsage.model_validate(item) for item in usage)
        super().__init__(code)


@runtime_checkable
class ShellCommandReviewer(Protocol):
    """Assess a command without granting invocation authority."""

    async def review(
        self,
        request: ShellReviewRequest,
        *,
        context: AgentContext,
    ) -> ShellReviewResult: ...


class AgentShellCommandReviewer:
    """Tool-free Pydantic AI implementation of shell command review."""

    def __init__(
        self,
        model: Model,
        *,
        model_settings: ModelSettings | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not isinstance(model, Model):
            raise TypeError("model must be a Pydantic AI Model")
        if not 0 < timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 0 and 120")
        self._model = model
        self._timeout_seconds = float(timeout_seconds)
        self._agent: Agent[None, ShellReviewAssessment] = Agent(
            model,
            output_type=PromptedOutput(ShellReviewAssessment),
            name="shell-command-review",
            system_prompt=_system_prompt(),
            model_settings=cast(ModelSettings, dict(model_settings or {})),
            retries=0,
        )

    async def review(
        self,
        request: ShellReviewRequest,
        *,
        context: AgentContext,
    ) -> ShellReviewResult:
        del context
        request = ShellReviewRequest.model_validate(request)
        usage = RunUsage()
        prompt = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._agent.run(
                    prompt,
                    usage=usage,
                    usage_limits=UsageLimits(request_limit=1),
                    event_stream_handler=_drain_review_events,
                )
        except TimeoutError as exc:
            raise ShellReviewError(
                "shell_review_timeout",
                usage=_provider_usage_receipts(self._model, usage),
            ) from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Provider error messages/bodies can echo credentials or command input.
            # Keep diagnostics useful without publishing the protected exception chain.
            _LOGGER.warning(
                "shell_review_failed",
                extra={
                    "tool_call_id": request.tool_call_id,
                    "error_type": type(exc).__name__,
                    "status_code": exc.status_code if isinstance(exc, ModelHTTPError) else None,
                },
            )
            raise ShellReviewError(
                "shell_review_failed",
                usage=_provider_usage_receipts(self._model, usage),
            ) from exc
        return ShellReviewResult(
            assessment=ShellReviewAssessment.model_validate(result.output),
            usage=_provider_usage_receipts(self._model, result.usage),
        )


@dataclass(init=False)
class ShellReviewCapability(AbstractCapability[AgentContext]):
    """Definition-selected shell review policy and logical model binding."""

    id = SHELL_REVIEW_CAPABILITY_ID

    def __init__(
        self,
        model: str,
        *,
        model_settings: ModelSettings | None = None,
        risk_threshold: ShellRiskLevel = ShellRiskLevel.HIGH,
        on_flagged: ShellReviewAction = ShellReviewAction.APPROVAL_REQUIRED,
        on_error: ShellReviewAction = ShellReviewAction.APPROVAL_REQUIRED,
        timeout_seconds: float = 120.0,
        reviewer: ShellCommandReviewer | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip() or len(model.strip()) > 1_024:
            raise ValueError("model must be a bounded non-blank string")
        if not 0 < timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 0 and 120")
        if reviewer is not None and not isinstance(reviewer, ShellCommandReviewer):
            raise TypeError("reviewer must implement ShellCommandReviewer")
        self.model = model.strip()
        self.model_settings = cast(ModelSettings, dict(model_settings or {}))
        self.risk_threshold = ShellRiskLevel(risk_threshold)
        self.on_flagged = ShellReviewAction(on_flagged)
        self.on_error = ShellReviewAction(on_error)
        self.timeout_seconds = float(timeout_seconds)
        self._configured_reviewer = reviewer
        self._reviewer: ShellCommandReviewer | None = None
        self._context: AgentContext | None = None

    @classmethod
    def from_spec(
        cls,
        model: str,
        *,
        model_settings: ModelSettings | None = None,
        risk_threshold: ShellRiskLevel = ShellRiskLevel.HIGH,
        on_flagged: ShellReviewAction = ShellReviewAction.APPROVAL_REQUIRED,
        on_error: ShellReviewAction = ShellReviewAction.APPROVAL_REQUIRED,
        timeout_seconds: float = 120.0,
    ) -> ShellReviewCapability:
        """Construct the serializable default-reviewer form."""
        return cls(
            model,
            model_settings=model_settings,
            risk_threshold=risk_threshold,
            on_flagged=on_flagged,
            on_error=on_error,
            timeout_seconds=timeout_seconds,
        )

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(SHELL_REVIEW_CAPABILITY_ID)
        if existing is not None:
            if type(existing) is not ShellReviewCapability:
                raise DefinitionError(
                    "Shell review has an incompatible run replacement.", code="capability_type_mismatch"
                )
            existing._require_context(ctx.deps)
            return existing
        if SHELL_REVIEW_CAPABILITY_ID not in ctx.deps._capability_provenance.definition_ids:
            raise DefinitionError(
                "ShellReviewCapability must originate from the Agent definition.",
                code="capability_scope_invalid",
            )
        replacement = ShellReviewCapability(
            self.model,
            model_settings=self.model_settings,
            risk_threshold=self.risk_threshold,
            on_flagged=self.on_flagged,
            on_error=self.on_error,
            timeout_seconds=self.timeout_seconds,
            reviewer=self._configured_reviewer,
        )
        replacement._context = ctx.deps
        if self._configured_reviewer is not None:
            replacement._reviewer = self._configured_reviewer
        else:
            if ctx.agent is None:
                raise DefinitionError("Shell review model resolution is unavailable.", code="model_resolution_failed")
            resolution_context = ModelResolutionContext(agent=ctx.agent, deps=ctx.deps)
            model = await ctx.deps._model_inference(resolution_context, self.model)
            replacement._reviewer = AgentShellCommandReviewer(
                model,
                model_settings=self.model_settings,
                timeout_seconds=self.timeout_seconds,
            )
        ctx.deps._record_run_capability(SHELL_REVIEW_CAPABILITY_ID, replacement)
        return replacement

    async def review(self, request: ShellReviewRequest, *, context: AgentContext) -> ShellReviewResult:
        """Invoke the run-bound reviewer and validate its detached result."""
        self._require_context(context)
        if self._reviewer is None:
            raise DefinitionError("Shell review is not bound to the current run.", code="capability_scope_invalid")
        if self._configured_reviewer is None:
            # The built-in reviewer owns its deadline so it can retain usage
            # proven before cancellation. Do not race it with a second timer.
            result = await self._reviewer.review(request, context=context)
        else:
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    result = await self._reviewer.review(request, context=context)
            except TimeoutError as exc:
                raise ShellReviewError("shell_review_timeout") from exc
        return ShellReviewResult.model_validate(result)

    def action_for(self, assessment: ShellReviewAssessment) -> ShellReviewAction | None:
        """Return the configured restriction when the assessment reaches the threshold."""
        assessment = ShellReviewAssessment.model_validate(assessment)
        if _RISK_ORDER[assessment.risk] < _RISK_ORDER[self.risk_threshold]:
            return None
        return self.on_flagged

    def _require_context(self, context: AgentContext) -> None:
        if self._context is not context:
            raise DefinitionError("Shell review cannot cross logical runs.", code="capability_scope_invalid")


async def _drain_review_events(
    ctx: RunContext[None],
    events: AsyncIterable[AgentStreamEvent],
) -> None:
    """Force the single review request through the provider streaming path."""
    del ctx
    async for _ in events:
        pass


@cache
def _system_prompt() -> str:
    return files("a13n_harness.toolsets.prompts").joinpath("shell_review.md").read_text(encoding="utf-8").strip()


def _provider_usage_receipts(model: Model, usage: RunUsage) -> tuple[ProviderUsage, ...]:
    measures = tuple(
        UsageMeasure(unit=unit, quantity=Decimal(value))
        for unit, value in (
            ("requests", usage.requests),
            ("tool_calls", usage.tool_calls),
            ("input_tokens", usage.input_tokens),
            ("cache_write_tokens", usage.cache_write_tokens),
            ("cache_read_tokens", usage.cache_read_tokens),
            ("output_tokens", usage.output_tokens),
        )
        if value
    )
    if not measures:
        return ()
    return (
        ProviderUsage(
            usage_id=f"shell-review-{uuid4()}",
            provider=model.system,
            product=model.model_name,
            timestamp=datetime.now(UTC),
            measures=measures,
        ),
    )


__all__ = [
    "AgentShellCommandReviewer",
    "ShellCommandReviewer",
    "ShellReviewAction",
    "ShellReviewAssessment",
    "ShellReviewCapability",
    "ShellReviewError",
    "ShellReviewRequest",
    "ShellReviewResult",
    "ShellRiskLevel",
]
