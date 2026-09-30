"""Capability installation of request-local model self-healing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from copy import copy
from dataclasses import dataclass

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.wrapper import WrapperModel

from a13n_harness.context import AgentContext
from a13n_harness.models.self_healing import (
    DEFAULT_MODEL_RECOVERY_RULES,
    ModelRecoveryRule,
    SelfHealingModel,
)

SELF_HEALING_MODEL_CAPABILITY_ID = "a13n.model.self-healing"


@dataclass(init=False)
class SelfHealingModelCapability(AbstractCapability[AgentContext]):
    """Install self-healing around the final effective request Model."""

    id = SELF_HEALING_MODEL_CAPABILITY_ID

    def __init__(self, rules: Sequence[ModelRecoveryRule] | None = None) -> None:
        self.rules = DEFAULT_MODEL_RECOVERY_RULES if rules is None else tuple(rules)

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: Callable[[ModelRequestContext], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        del ctx
        model = request_context.model
        while isinstance(model, WrapperModel):
            if isinstance(model, SelfHealingModel):
                return await handler(request_context)
            model = model.wrapped
        updated = copy(request_context)
        updated.model = SelfHealingModel(request_context.model, rules=self.rules)
        return await handler(updated)


__all__ = ["SelfHealingModelCapability"]
