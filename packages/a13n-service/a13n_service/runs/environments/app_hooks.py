"""Apply the Service callback policy to App Environment declarations."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING

from a13n_harness.environment import EnvironmentRunCallbacks, EnvironmentRunExtensionContext
from a13n_logging import get_logger

from a13n_service.app_sdk.hooks import EnvironmentHookAbort
from a13n_service.infra.telemetry import meter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from a13n_service.app_plugins import AppActivation
    from a13n_service.app_sdk.hooks import RunCallback
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)
FAILURES = meter.create_counter("a13n.app.environment_hook.failures", unit="{callback}")


async def invoke(
    callback: Callable[[], Awaitable[object]],
    *,
    app_id: str,
    phase: str,
    timeout: float,
    target_id: str,
    abortable: bool,
) -> bool:
    """Return whether the callback completed successfully; only explicit eligible aborts escape."""
    try:
        async with asyncio.timeout(timeout):
            await callback()
    except Exception as error:
        result = (
            "aborted"
            if isinstance(error, EnvironmentHookAbort)
            else ("timeout" if isinstance(error, TimeoutError) else "error")
        )
        FAILURES.add(1, {"phase": phase, "result": result})
        logger.warning(
            "App Environment callback did not complete",
            extra={
                "app_id": app_id,
                "phase": phase,
                "target_id": target_id,
                "result": result,
                "error_type": type(error).__name__,
            },
        )
        if abortable and isinstance(error, EnvironmentHookAbort):
            raise
        return False
    return True


async def _run_callback(
    callback: RunCallback,
    app_id: str,
    phase: str,
    timeout: float,
    context: EnvironmentRunExtensionContext,
) -> None:
    await invoke(
        partial(callback, context),
        app_id=app_id,
        phase=phase,
        timeout=timeout,
        target_id=context.run_id,
        abortable=phase == "on_enter",
    )


def run_extensions(
    runtime: Runtime,
    activation: AppActivation | None,
    owner_app_id: str | None,
) -> tuple[EnvironmentRunCallbacks, ...]:
    if activation is None:
        return ()
    enabled = {app.app_id for app in activation.apps if app.enabled}
    return tuple(
        EnvironmentRunCallbacks(
            extension_id=f"{app_id}:{index}",
            on_enter=partial(_run_callback, hook.on_enter, app_id, "on_enter", hook.timeout)
            if hook.on_enter is not None
            else None,
            on_exit=partial(_run_callback, hook.on_exit, app_id, "on_exit", hook.timeout)
            if hook.on_exit is not None
            else None,
        )
        for index, (app_id, hook) in enumerate(runtime.environment_run_hooks)
        if app_id in enabled and (hook.scope == "organization" or app_id == owner_app_id)
    )
