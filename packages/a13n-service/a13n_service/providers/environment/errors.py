from __future__ import annotations

import asyncio

from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState


class EnvironmentManagementError(EnvironmentProviderError):
    """A management failure with authoritative state observed before it failed."""

    def __init__(
        self, error: EnvironmentProviderError, state: EnvironmentState | None, operation_id: str, environment_id: str
    ) -> None:
        super().__init__(
            error.description,
            code=error.code,
            category=error.category,
            certainty=error.certainty,
            recovery_hint=error.recovery_hint,
            details=error.details,
            context=error.context.model_copy(
                update={"operation_id": operation_id, "resource_correlation": environment_id}
            ),
        )
        self.state = state.model_copy(deep=True) if state is not None else None


class EnvironmentManagementCancelled(asyncio.CancelledError):
    """Cancellation retaining state already observed by a management operation."""

    def __init__(self, state: EnvironmentState | None, operation_id: str) -> None:
        super().__init__("Environment management operation cancelled")
        self.state = state
        self.operation_id = operation_id


def observed_environment_state(error: BaseException, fallback: EnvironmentState | None) -> EnvironmentState | None:
    """Recover observed state through a timeout's cancellation cause."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, (EnvironmentManagementError, EnvironmentManagementCancelled)):
            return current.state
        current = current.__cause__ or current.__context__
    return fallback
