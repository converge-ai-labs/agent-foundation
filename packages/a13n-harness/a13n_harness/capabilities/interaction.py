"""Capability ownership for structured native deferred user questions."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from pydantic_ai import RunContext, ToolDefinition, ToolFailed
from pydantic_ai.capabilities import AbstractCapability, ValidatedToolArgs
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_harness.context import AgentContext
from a13n_harness.toolsets.interaction import (
    ASK_USER_QUESTION_KIND,
    AskUserQuestionRequest,
    UserInteractionToolset,
    UserQuestion,
    UserQuestionAnswers,
    UserQuestionOption,
)

USER_INTERACTION_CAPABILITY_ID = "a13n.user-interaction"


@dataclass(kw_only=True)
class UserInteractionCapability(AbstractCapability[AgentContext]):
    """Expose structured questions only to an independent root Agent."""

    id: str | None = USER_INTERACTION_CAPABILITY_ID

    def __post_init__(self) -> None:
        if self.id != USER_INTERACTION_CAPABILITY_ID:
            raise ValueError(f"UserInteractionCapability.id must be {USER_INTERACTION_CAPABILITY_ID!r}")

    async def after_tool_validate(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
    ) -> ValidatedToolArgs:
        if (tool_def.metadata or {}).get("kind") != ASK_USER_QUESTION_KIND:
            return args
        try:
            request = AskUserQuestionRequest.model_validate(args)
        except ValidationError as exc:
            errors = "; ".join(error["msg"][:512] for error in exc.errors(include_input=False, include_url=False)[:4])
            raise ToolFailed(f"Invalid structured question: {errors}") from exc
        return request.model_dump(mode="json", by_alias=True)

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id="a13n-user-interaction")

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext] | None:
        if ctx.deps.instance.parent_agent_instance_id is not None:
            return None
        return UserInteractionToolset().get_toolset()


__all__ = [
    "AskUserQuestionRequest",
    "UserInteractionCapability",
    "UserQuestion",
    "UserQuestionAnswers",
    "UserQuestionOption",
]
