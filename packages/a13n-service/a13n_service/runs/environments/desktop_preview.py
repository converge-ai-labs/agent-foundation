"""An explicit read-only E2B/Modal/Daytona desktop snapshot, without a Run or human-control grant."""

import anyio
from a13n_harness.environment.computer import ComputerScreenshot
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentError

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.runs.desktop_assistance.service import provider_target
from a13n_service.runs.environments.adapters import close, execution_connector
from a13n_service.runs.environments.service import _find
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def capture(runtime: Runtime, actor: Principal, workspace_id: str, identity: str) -> ComputerScreenshot:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        row = await _find(session, scope, identity)
        target = await provider_target(session, runtime, actor, row)
        if not row.provider_identity or row.provider_identity.get("type") not in {"e2b", "modal", "daytona", "vercel"}:
            raise conflict("environment", identity, "desktop_preview_unsupported")
    execution = None
    try:
        with anyio.fail_after(runtime.settings.providers.operation_seconds):
            connector = await execution_connector(runtime, target)
            execution = await connector.open()
            computer = execution.operations.computer
            if computer is None or "environment.computer.observe" not in execution.descriptor.permissions.operations:
                raise conflict("environment", identity, "desktop_observation_not_permitted")
            screenshot = await computer.observe()
            async with short_session(runtime.storage) as session:
                scope = await workspace_scope(session, actor, workspace_id, "run")
                current = await provider_target(session, runtime, actor, await _find(session, scope, identity))
                if current != target:
                    raise conflict("environment", identity, "desktop_target_changed")
            return screenshot
    except (EnvironmentProviderError, EnvironmentError, TimeoutError) as error:
        raise ServiceError(
            "unavailable",
            "The desktop preview could not be captured",
            {"reason": "environment_timeout" if isinstance(error, TimeoutError) else error.code},
        ) from None
    finally:
        if execution is not None:
            await close(execution)
