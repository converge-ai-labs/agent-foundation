"""Typed errors exposed by Agent Foundation Harness."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import JsonValue

if TYPE_CHECKING:
    from a13n_harness.result import HarnessRunResult

RetryHint = Literal["none", "new_run", "dependency_change"]


class HarnessError(Exception):
    """Base class for safe, process-local Harness failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Mapping[str, JsonValue] | None = None,
        retry_hint: RetryHint = "none",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})
        self.retry_hint = retry_hint


class DefinitionError(HarnessError):
    """Agent definition or build validation failed."""


class PluginError(HarnessError):
    """Plugin construction, ordering, binding, or middleware failed."""


class IdentityError(HarnessError):
    """Trusted Agent identity input is invalid."""


class InputError(HarnessError):
    """Run input is invalid."""


class ModelResolutionError(HarnessError):
    """A logical model could not be resolved for the current run."""


class StateError(HarnessError):
    """Harness continuation state is invalid."""


class RunError(HarnessError):
    """A Harness run failed or was used incorrectly."""


class RunCleanupError(HarnessError):
    """Run teardown failed after zero or one primary outcome was available."""

    def __init__(
        self,
        message: str,
        *,
        outcome: HarnessRunResult[Any] | None,
        causes: tuple[BaseException, ...],
    ) -> None:
        super().__init__(
            message,
            code="run_cleanup_failed",
            details={"cause_count": len(causes)},
            retry_hint="new_run",
        )
        self.outcome = outcome
        self.causes = causes
