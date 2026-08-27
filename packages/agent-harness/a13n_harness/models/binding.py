"""Run-scoped logical model resolution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic_ai.models import Model, ModelResolutionContext

from a13n_harness.errors import ModelResolutionError

if TYPE_CHECKING:
    from a13n_harness.context import AgentContext


class ModelRunBinding(ABC):
    """Fresh run authority for resolving a logical model ID."""

    @abstractmethod
    async def resolve_model(
        self,
        context: ModelResolutionContext[AgentContext],
        model_id: str,
    ) -> Model:
        """Resolve one logical ID to a native Pydantic AI Model."""
        raise NotImplementedError


async def resolve_run_model(
    context: ModelResolutionContext[AgentContext],
    model_id: str,
) -> Model | None:
    """Resolve through the fresh binding, or delegate to native inference."""
    binding = context.deps.model_binding
    if binding is None:
        return None
    try:
        model = await binding.resolve_model(context, model_id)
    except ModelResolutionError:
        raise
    except Exception as error:
        raise ModelResolutionError(
            "Run model resolution failed.",
            code="model_resolution_failed",
            details={"model_id": model_id},
        ) from error
    if not isinstance(model, Model):
        raise ModelResolutionError(
            "Run model resolution returned an invalid value.",
            code="model_resolution_invalid",
            details={"model_id": model_id},
        )
    return model


__all__ = ["ModelRunBinding"]
