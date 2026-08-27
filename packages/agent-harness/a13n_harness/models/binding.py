"""Run-scoped logical model resolution."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Protocol

from pydantic_ai.models import Model, ModelResolutionContext

from a13n_harness.errors import ModelResolutionError

if TYPE_CHECKING:
    from a13n_harness.context import AgentContext


class RunModelResolver(Protocol):
    """Async callable that resolves a logical model ID for one run."""

    def __call__(
        self,
        context: ModelResolutionContext[AgentContext],
        model_id: str,
    ) -> Awaitable[Model]:
        """Resolve one logical ID to a native Pydantic AI Model."""
        ...


async def resolve_run_model(
    context: ModelResolutionContext[AgentContext],
    model_id: str,
) -> Model | None:
    """Resolve through the fresh callable, or delegate to native inference."""
    resolver = context.deps.model_resolver
    if resolver is None:
        return None
    try:
        model = await resolver(context, model_id)
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


__all__ = ["RunModelResolver"]
