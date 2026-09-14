"""Optional, replaceable review of tool calls before business logic runs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from html import escape
from importlib.resources import files
from typing import Literal, Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from pydantic_ai import Agent, RunContext, ToolOutput
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from a13n_harness._review_context import ReviewEvidence, render_review_input
from a13n_harness.capabilities._review import drain_review_events as _drain_review_events
from a13n_harness.capabilities._review import provider_usage_receipts as _provider_usage_receipts
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.models.structured_output import StructuredOutputAutoToolChoiceModel
from a13n_harness.observation import _auxiliary_agent_capabilities
from a13n_harness.tools.permissions import match_selector, validate_selector
from a13n_harness.tools.policy import InvocationDecisionKind
from a13n_harness.usage import ProviderUsage

TOOL_REVIEW_CAPABILITY_ID = "a13n.tool-review"


class ToolReviewRequest(BaseModel):
    """Bounded facts; task input and tool declarations are data, not instructions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_id: str
    tool_call_id: str
    tool_name: str
    description: str | None = None
    parameters_schema: dict[str, JsonValue]
    arguments: dict[str, JsonValue]
    task: str | None = None
    context: dict[str, JsonValue] = Field(default_factory=dict)
    omitted: tuple[str, ...] = ()
    profile: Literal["shell", "general"] = "general"
    approved_sources: tuple[Literal["permission", "reviewer", "tool"], ...] = ()
    previous_reviews: tuple[ReviewEvidence, ...] = ()
    recent_actions: tuple[ReviewEvidence, ...] = ()

    def to_prompt(self) -> str:
        return render_review_input(
            tool_id=self.tool_id,
            tool_call_id=self.tool_call_id,
            tool_name=self.tool_name,
            arguments=self.arguments,
            parameters_schema=self.parameters_schema,
            task=self.task,
            description=self.description,
            context=self.context,
            approved_sources=self.approved_sources,
            previous_reviews=self.previous_reviews,
            recent_actions=self.recent_actions,
            omitted=self.omitted,
        )


class ToolRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTRA_HIGH = "extra_high"


class ToolReviewRule(BaseModel):
    """A matching rule overrides the supplied fields of the global policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_threshold: ToolRiskLevel | None = None
    on_flagged: Literal["deny", "approval_required"] | None = None


class ToolReviewPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_threshold: ToolRiskLevel = ToolRiskLevel.EXTRA_HIGH
    on_flagged: Literal["deny", "approval_required"] = "deny"
    rules: dict[str, ToolReviewRule] = Field(default_factory=dict, max_length=1024)

    @field_validator("rules")
    @classmethod
    def _validate_rules(cls, rules: dict[str, ToolReviewRule]) -> dict[str, ToolReviewRule]:
        for selector in rules:
            validate_selector(selector)
        return rules

    def decision_for(self, tool_id: str, risk: ToolRiskLevel) -> InvocationDecisionKind:
        rule = match_selector(self.rules, tool_id)
        threshold = rule.risk_threshold if rule is not None and rule.risk_threshold is not None else self.risk_threshold
        action = rule.on_flagged if rule is not None and rule.on_flagged is not None else self.on_flagged
        levels = list(ToolRiskLevel)
        return action if levels.index(risk) >= levels.index(threshold) else "allow"


class ToolReviewAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    risk: ToolRiskLevel
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("review reason must be nonblank text without NUL")
        return value


class ToolReviewResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    assessment: ToolReviewAssessment
    usage: tuple[ProviderUsage, ...] = Field(default=(), max_length=16)


class ToolReviewResultPayload(BaseModel):
    """Public result observation, never a request or execution authorization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["tool_review_result"] = "tool_review_result"
    tool_id: str
    tool_call_id: str
    status: Literal["completed", "error"]
    result: ToolReviewResult | None
    error_code: str | None = None
    decision: InvocationDecisionKind | None = None


@runtime_checkable
class ToolReviewer(Protocol):
    async def review(self, request: ToolReviewRequest, *, context: AgentContext) -> ToolReviewResult: ...


class ToolReviewError(Exception):
    def __init__(self, code: str, *, usage: tuple[ProviderUsage, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.usage = usage


class ToolReviewConfig(ToolReviewPolicy):
    """Host-portable configuration for the default model-backed reviewer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(min_length=1, max_length=1024)
    instruction: str | None = Field(default=None, max_length=32768)
    shell_instruction: str | None = Field(default=None, max_length=32768)
    model_settings: dict[str, JsonValue] | None = None
    timeout_seconds: float = Field(default=120, gt=0, le=120)
    on_error: Literal["deny", "approval_required", "allow"] = "approval_required"


def render_review_instruction(instruction: str | None) -> str | None:
    """Render text, not executable templates, into the separate instructions field."""
    if instruction is None or not instruction.strip():
        return None
    return f"<custom-instruction>\n{escape(instruction, quote=False)}\n</custom-instruction>"


@cache
def _review_prompt() -> str:
    return files("a13n_harness.toolsets.prompts").joinpath("tool_review.md").read_text(encoding="utf-8")


class AgentToolReviewer:
    """One bounded model request, no business tools and no inherited Agent prompt."""

    def __init__(self, model: Model, config: ToolReviewConfig) -> None:
        self._model = model
        self._config = config.model_copy(deep=True)
        self._agent: Agent[ToolReviewRequest, ToolReviewAssessment] = Agent(
            StructuredOutputAutoToolChoiceModel(model),
            deps_type=ToolReviewRequest,
            output_type=ToolOutput(
                ToolReviewAssessment,
                name="submit_tool_review",
                description=(
                    "Call this tool exactly once with risk and a brief reason. "
                    "This does not execute or authorize the command or tool call. "
                    "Plain text or JSON text is not a valid submission."
                ),
            ),
            system_prompt=_review_prompt(),
            instructions=self._instructions,
            model_settings=cast(ModelSettings, config.model_settings),
            retries=0,
            name="tool-review",
        )
        self._agent.instrument = False

    def _instructions(self, ctx: RunContext[ToolReviewRequest]) -> str:
        instruction = (
            self._config.shell_instruction
            if ctx.deps.profile == "shell" and self._config.shell_instruction is not None
            else self._config.instruction
        )
        return render_review_instruction(instruction) or ""

    async def review(self, request: ToolReviewRequest, *, context: AgentContext) -> ToolReviewResult:
        del context
        usage = RunUsage()
        try:
            async with asyncio.timeout(self._config.timeout_seconds):
                result = await self._agent.run(
                    request.to_prompt(),
                    deps=request,
                    usage=usage,
                    usage_limits=UsageLimits(request_limit=1),
                    capabilities=_auxiliary_agent_capabilities(),
                    event_stream_handler=_drain_review_events,
                )
        except asyncio.CancelledError:
            raise
        except ToolReviewError:
            raise
        except TimeoutError as exc:
            raise ToolReviewError("tool_review_timeout", usage=_provider_usage_receipts(self._model, usage)) from exc
        except Exception as exc:
            raise ToolReviewError("tool_review_failed", usage=_provider_usage_receipts(self._model, usage)) from exc
        return ToolReviewResult(assessment=result.output, usage=_provider_usage_receipts(self._model, result.usage))


@dataclass(init=False)
class ToolReviewCapability(AbstractCapability[AgentContext]):
    """Definition-selected reviewer with optional per-selector implementation overrides."""

    id = TOOL_REVIEW_CAPABILITY_ID

    def __init__(
        self,
        config: ToolReviewConfig | None = None,
        *,
        reviewer: ToolReviewer | None = None,
        reviewers: Mapping[str, ToolReviewer] | None = None,
        policy: ToolReviewPolicy | None = None,
    ) -> None:
        self.config = config.model_copy(deep=True) if config is not None else None
        if config is not None and policy is not None:
            raise ValueError("Configure review policy through config or policy, not both")
        self.policy = (policy or config or ToolReviewPolicy()).model_copy(deep=True)
        self._reviewer = reviewer
        self._reviewers = dict(reviewers or {})
        for selector, implementation in self._reviewers.items():
            validate_selector(selector)
            if not isinstance(implementation, ToolReviewer):
                raise TypeError("reviewers must implement ToolReviewer")
        if reviewer is not None and not isinstance(reviewer, ToolReviewer):
            raise TypeError("reviewer must implement ToolReviewer")
        self._context: AgentContext | None = None

    @classmethod
    def from_spec(
        cls,
        model: str,
        *,
        instruction: str | None = None,
        shell_instruction: str | None = None,
        model_settings: dict[str, JsonValue] | None = None,
        timeout_seconds: float = 120,
        on_error: Literal["deny", "approval_required", "allow"] = "approval_required",
        risk_threshold: ToolRiskLevel = ToolRiskLevel.EXTRA_HIGH,
        on_flagged: Literal["deny", "approval_required"] = "deny",
        rules: dict[str, ToolReviewRule] | None = None,
    ) -> ToolReviewCapability:
        return cls(
            ToolReviewConfig(
                model=model,
                instruction=instruction,
                shell_instruction=shell_instruction,
                model_settings=model_settings,
                timeout_seconds=timeout_seconds,
                on_error=on_error,
                risk_threshold=risk_threshold,
                on_flagged=on_flagged,
                rules=rules or {},
            )
        )

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(TOOL_REVIEW_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, ToolReviewCapability):
                raise DefinitionError("Incompatible tool reviewer.", code="capability_type_mismatch")
            return existing
        replacement = ToolReviewCapability(
            self.config,
            reviewer=self._reviewer,
            reviewers=self._reviewers,
            policy=self.policy if self.config is None else None,
        )
        replacement._context = ctx.deps
        if replacement._reviewer is None and self.config is not None:
            if ctx.agent is None:
                raise DefinitionError("Review model resolution is unavailable.", code="model_resolution_failed")
            model = await ctx.deps._model_inference(
                ModelResolutionContext(agent=ctx.agent, deps=ctx.deps), self.config.model
            )
            replacement._reviewer = AgentToolReviewer(model, self.config)
        ctx.deps._record_run_capability(TOOL_REVIEW_CAPABILITY_ID, replacement)
        return replacement

    def decision_for(self, tool_id: str, assessment: ToolReviewAssessment) -> InvocationDecisionKind:
        return self.policy.decision_for(tool_id, assessment.risk)

    def has_reviewer(self, tool_id: str) -> bool:
        return match_selector(self._reviewers, tool_id) is not None or self._reviewer is not None

    async def review(self, request: ToolReviewRequest, *, context: AgentContext) -> ToolReviewResult | None:
        if self._context is not context:
            raise DefinitionError("Tool review is not bound to this run.", code="capability_scope_invalid")
        reviewer = match_selector(self._reviewers, request.tool_id) or self._reviewer
        if reviewer is None:
            return None
        if isinstance(reviewer, AgentToolReviewer):
            return ToolReviewResult.model_validate(await reviewer.review(request, context=context))
        try:
            async with asyncio.timeout(self.config.timeout_seconds if self.config is not None else 120):
                return ToolReviewResult.model_validate(await reviewer.review(request, context=context))
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise ToolReviewError("tool_review_timeout") from exc
        except ToolReviewError:
            raise
        except Exception as exc:
            raise ToolReviewError("tool_review_failed") from exc
