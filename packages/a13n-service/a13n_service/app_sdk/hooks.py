"""Callback contracts for trusted App Environment hooks."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from a13n_harness.environment import EnvironmentExecution, EnvironmentRunExtensionContext


type HookScope = Literal["app", "organization"]
type LifecyclePhase = Literal["after_create", "after_start", "before_stop", "before_delete"]


class EnvironmentHookAbort(Exception):
    """Reject entry explicitly. The message is public and must not contain secrets."""

    def __init__(self, code: str, message: str, *, permanent: bool = True) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) or not 0 < len(message) <= 1024:
            raise ValueError("Invalid Environment hook diagnostic")
        super().__init__(message)
        self.code = code
        self.message = message
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class EnvironmentHookContext:
    app_id: str
    phase: LifecyclePhase
    organization_id: str
    workspace_id: str
    environment_id: str
    owner_app_id: str | None
    app_context: dict[str, str] | None
    replaces_environment_id: str | None
    operation_id: str
    generation: int
    working_directory: str
    execution: EnvironmentExecution


def validate_hook(scope: HookScope, timeout: float) -> None:
    if scope not in {"app", "organization"} or not 0 < timeout <= 30:
        raise ValueError("An Environment hook requires a valid scope and timeout in (0, 30]")


@dataclass(frozen=True, slots=True)
class EnvironmentLifecycleHook:
    phase: LifecyclePhase
    run: Callable[[EnvironmentHookContext], Awaitable[object]]
    timeout: float = 5.0
    scope: HookScope = "app"

    def __post_init__(self) -> None:
        validate_hook(self.scope, self.timeout)
        if self.phase not in {"after_create", "after_start", "before_stop", "before_delete"}:
            raise ValueError("Invalid Environment lifecycle hook phase")


type RunCallback = Callable[[EnvironmentRunExtensionContext], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class EnvironmentRunHook:
    on_enter: RunCallback | None = None
    on_exit: RunCallback | None = None
    timeout: float = 5.0
    scope: HookScope = "app"

    def __post_init__(self) -> None:
        validate_hook(self.scope, self.timeout)
        if self.on_enter is None and self.on_exit is None:
            raise ValueError("An Environment Run hook requires an enter or exit callback")
