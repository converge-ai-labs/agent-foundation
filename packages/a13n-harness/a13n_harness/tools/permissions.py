"""Declarative tool permissions shared by embedded and hosted Agents."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import ModelRequestContext, ModelResolutionContext
from pydantic_ai.native_tools import WebSearchTool

from a13n_harness._tool_selectors import match_selector as match_selector
from a13n_harness._tool_selectors import validate_selector
from a13n_harness.capabilities.tool_review import (
    AgentToolReviewer,
    ToolReviewAssessment,
    ToolReviewConfig,
    ToolReviewer,
    ToolReviewError,
    ToolReviewPolicy,
    ToolReviewRequest,
    ToolReviewResult,
)
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.identity import ToolIdentity, ToolPermissionMode
from a13n_harness.tools.policy import InvocationDecisionKind

TOOL_PERMISSIONS_CAPABILITY_ID = "a13n.tool-permissions"
type ToolPermissionSetting = ToolPermissionMode | Literal["inherit"]


class ToolPermissions(BaseModel):
    """Portable configuration. Inherit resolves a tool default, never an execution decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default: ToolPermissionSetting = "inherit"
    rules: dict[str, ToolPermissionSetting] = Field(default_factory=dict, max_length=1024)

    @field_validator("rules")
    @classmethod
    def _validate_rules(cls, rules: dict[str, ToolPermissionSetting]) -> dict[str, ToolPermissionSetting]:
        for selector in rules:
            validate_selector(selector)
        return rules

    def resolve(self, identity: ToolIdentity) -> ToolPermissionMode:
        setting = match_selector(self.rules, identity.tool_id) or self.default
        return identity.default_mode if setting == "inherit" else setting


@dataclass(init=False)
class ToolPermissionsCapability(AbstractCapability[AgentContext]):
    """Own invocation permissions and optional review without replacing Host authorization."""

    id = TOOL_PERMISSIONS_CAPABILITY_ID

    def __init__(
        self,
        permissions: ToolPermissions | None = None,
        *,
        review: ToolReviewConfig | None = None,
        reviewer: ToolReviewer | None = None,
        reviewers: Mapping[str, ToolReviewer] | None = None,
        policy: ToolReviewPolicy | None = None,
    ) -> None:
        self.permissions = (permissions or ToolPermissions()).model_copy(deep=True)
        self.config = review.model_copy(deep=True) if review is not None else None
        if review is not None and policy is not None:
            raise ValueError("Configure review policy through review or policy, not both")
        self.policy = (policy or review or ToolReviewPolicy()).model_copy(deep=True)
        self._reviewer = reviewer
        self._reviewers = dict(reviewers or {})
        for selector, implementation in self._reviewers.items():
            validate_selector(selector)
            if not isinstance(implementation, ToolReviewer):
                raise TypeError("reviewers must implement ToolReviewer")
        if reviewer is not None and not isinstance(reviewer, ToolReviewer):
            raise TypeError("reviewer must implement ToolReviewer")
        self._context: AgentContext | None = None

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if self.permissions.resolve(ToolIdentity("web.search", "allow")) != "allow" and any(
            isinstance(tool, WebSearchTool) for tool in request_context.model_request_parameters.native_tools
        ):
            raise DefinitionError(
                "web.search permissions require Host search, not provider-native search.",
                code="tool_permission_unsupported",
            )
        return request_context

    @classmethod
    def from_spec(
        cls,
        *,
        default: ToolPermissionSetting = "inherit",
        rules: dict[str, ToolPermissionSetting] | None = None,
        review: ToolReviewConfig | None = None,
    ) -> ToolPermissionsCapability:
        return cls(
            ToolPermissions(default=default, rules=rules or {}),
            review=ToolReviewConfig.model_validate(review) if review is not None else None,
        )

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(TOOL_PERMISSIONS_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, ToolPermissionsCapability):
                raise DefinitionError("Incompatible tool reviewer.", code="capability_type_mismatch")
            return existing
        replacement = ToolPermissionsCapability(
            self.permissions,
            review=self.config,
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
        ctx.deps._record_run_capability(TOOL_PERMISSIONS_CAPABILITY_ID, replacement)
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
