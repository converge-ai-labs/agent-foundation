"""User-origin desktop access and safe suspension requests; never fabricate a tool call."""

from datetime import timedelta

import anyio
from sqlalchemy import exists, or_, select

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.runs.desktop_assistance import service
from a13n_service.runs.desktop_assistance.admission import aliases, environment_key, target
from a13n_service.runs.desktop_assistance.schemas import Access, Control, OpenView, Request, View, viewer_url
from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow, DesktopExecutionRow
from a13n_service.runs.environments.adapters import Target
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow
from a13n_service.runs.threads import get_run, get_thread, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def read(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str) -> View | None:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "run")
        await get_run(session, workspace_id, run_id)
        row = await session.scalar(
            select(DesktopAssistanceRow).where(
                DesktopAssistanceRow.run_id == run_id, DesktopAssistanceRow.cause == "user"
            )
        )
        return View.model_validate(row) if row else None


async def select_desktop(
    runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, body: Request
) -> tuple[Target, str]:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "run")
        run = await get_run(session, workspace_id, run_id)
        frozen = next((m for m in run.environment_mounts if m["name"] == body.mount), None)
        if frozen is None:
            raise not_found("mount", body.mount)
        if "environment.computer.observe" not in frozen.get("permission_ceiling", {}).get("operations", []):
            raise conflict("mount", body.mount, "desktop_observation_not_permitted")
        environment = await session.get(EnvironmentRow, frozen["environment_id"])
        assert environment is not None
        selected = await service.provider_target(session, runtime, actor, environment)
        key = environment_key(environment)
        if key is None:
            raise conflict("environment", environment.id, "desktop_identity_unavailable")
    return selected, key


async def register_desktop(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    body: Request,
    selected: Target,
    key: str,
    description: wire.Reply,
) -> View:
    if (
        not isinstance(description, wire.Description)
        or not description.read_only_enforced
        or not description.revocation_fences_grants
    ):
        raise conflict("environment", selected.environment_id, "desktop_endpoint_unavailable")
    try:
        viewer_url(description.websocket_url)
    except ValueError as error:
        raise conflict("environment", selected.environment_id, "desktop_viewer_route_unavailable") from error
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "run")
        run = await get_run(session, workspace_id, run_id)
        thread = await get_thread(session, workspace_id, run.thread_id, lock=True)
        require_open(thread)
        await session.refresh(run)
        await lock(session, RunRow, run.id)
        await service.require_no_agent_request(session, run.id)
        if run.id not in {thread.current_run_id, thread.last_run_id}:
            raise conflict("run", run.id, "desktop_run_superseded")
        previous = await session.scalar(
            select(DesktopAssistanceRow).where(
                DesktopAssistanceRow.run_id == run_id, DesktopAssistanceRow.cause == "user"
            )
        )
        if previous:
            if previous.mount != body.mount:
                raise conflict("run", run.id, "desktop_mount_already_selected")
            return View.model_validate(previous)
        environment = await lock(session, EnvironmentRow, selected.environment_id)
        assert environment is not None
        if environment_key(environment) != key:
            raise conflict("environment", environment.id, "desktop_target_changed")
        await service.provider_target(session, runtime, actor, environment)
        control = await target(session, key, environment_id=environment.id, run_id=run.id)
        if control.holder_id:
            raise conflict("environment", environment.id, "desktop_assistance_in_use")
        row = DesktopAssistanceRow(
            id=new_object_id("dassist"),
            workspace_id=workspace_id,
            run_id=run_id,
            tool_call_id=None,
            cause="user",
            environment_id=environment.id,
            target_key=key,
            mount=body.mount,
            reason=body.reason,
            state="viewing",
            generation=description.generation,
            deadline=await now(session) + timedelta(minutes=15),
        )
        session.add(row)
        await session.flush()
        await service.audit_transition(session, actor, row, "open")
        return View.model_validate(row)


async def open_desktop(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, body: Request) -> View:
    selected, key = await select_desktop(runtime, actor, workspace_id, run_id, body)
    description = await service.dispatch(runtime, selected, wire.Describe(), operation_id=new_object_id("dtop"))
    return await register_desktop(runtime, actor, workspace_id, run_id, body, selected, key, description)


async def view_desktop(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, body: OpenView) -> Access:
    """Initialize assistance and grant read-only access in one bounded discovery scope."""
    operation_id = new_object_id("dtop")
    claim: service.AccessClaim | None = None
    try:
        with anyio.fail_after(service._ACCESS_TIMEOUT_SECONDS):
            selected, key = await select_desktop(runtime, actor, workspace_id, run_id, body)
            async with service.access_dispatch(runtime, selected, operation_id=operation_id) as send:
                description = await send(wire.Describe())
                view = await register_desktop(runtime, actor, workspace_id, run_id, body, selected, key, description)
                claim = await service.claim_access(
                    runtime,
                    actor,
                    workspace_id,
                    view.id,
                    Control(session_id=body.session_id, epoch=view.epoch),
                    mode="view",
                    renew=False,
                    operation_id=operation_id,
                )
                # Discovery happened outside storage: do not grant against a changed target or account.
                if claim.selected != selected:
                    raise conflict("environment", selected.environment_id, "desktop_target_changed")
                result, route = await service.grant_access(claim, description, send)
            # The provider scope must finish cleanup before access is published.
            return await service.publish_access(runtime, actor, workspace_id, claim, result, route)
    except BaseException as error:
        if claim is not None:
            await service.blocked(runtime, claim.identity, operation_id)
        if isinstance(error, Exception) and not isinstance(error, ServiceError):
            raise ServiceError(
                "unavailable", "Desktop transition could not be confirmed", {"dependency": "desktop_access"}
            ) from error
        raise


async def acquire(runtime: Runtime, actor: Principal, workspace_id: str, identity: str) -> View:
    async with transaction(runtime.storage) as session:
        row, control, _ = await service.authorized(session, runtime, actor, workspace_id, identity, waiting=False)
        if row.cause != "user":
            raise conflict("desktop_assistance", identity, "agent_requested_assistance")
        current = await now(session)
        if row.operation_expires_at is not None and row.operation_expires_at > current:
            raise conflict("desktop_assistance", identity, "desktop_transition_pending")
        if row.state == "viewing":
            service.available(row, current)
        if row.state in {"preparing", "awaiting_user", "human_control"}:
            return View.model_validate(row)
        if row.state != "viewing":
            raise conflict("desktop_assistance", identity, "desktop_transition_pending")
        run = await lock(session, RunRow, row.run_id)
        assert run is not None
        if run.status not in {"accepted", "running", "completed"} or run.cancel_requested_at:
            raise conflict("run", run.id, "desktop_run_not_takeable")
        execution = await lock(session, DesktopExecutionRow, run.id)
        if execution is None:
            raise conflict("run", run.id, "desktop_execution_not_ready")
        if not execution.supported:
            raise conflict("run", run.id, "desktop_task_tree_unsupported")
        if execution.uncertain or control.unmanaged_input:
            raise conflict("run", run.id, "desktop_background_work_unconfirmed")
        environment = await session.get(EnvironmentRow, row.environment_id)
        assert environment is not None
        identities = await aliases(session, environment)
        if await session.scalar(
            select(
                exists().where(
                    RunRow.id != run.id,
                    RunRow.status.in_(("accepted", "running")),
                    or_(*(RunRow.environment_mounts.contains([{"environment_id": i}]) for i in identities)),
                )
            )
        ):
            raise conflict("environment", environment.id, "desktop_busy")
        if control.holder_id and control.holder_id != row.id:
            raise conflict("environment", environment.id, "desktop_assistance_in_use")
        control.holder_id = row.id
        row.state = "preparing" if run.status != "completed" else "awaiting_user"
        if run.status == "completed" and (execution.inflight or control.inflight):
            raise conflict("run", run.id, "desktop_execution_unconfirmed")
        await service.audit_transition(session, actor, row, "request_control")
        return View.model_validate(row)
