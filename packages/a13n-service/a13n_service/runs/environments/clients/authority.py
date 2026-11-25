"""Operation-local, batched Control authorization for Sessions and their transfers."""

from collections.abc import Sequence
from dataclasses import dataclass

from a13n_envd_client.eip.v1 import ProcessInfo, SessionDescriptor
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError, EnvironmentPermissionSet
from a13n_harness.environment.process_recovery import ShellExecutionState
from sqlalchemy import select

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.providers.service import resolved_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.attempts import AuthorityRevoked, LeaseLost
from a13n_service.runs.environments import connection, outbound
from a13n_service.runs.environments.clients.routing import LifecycleCaller, Request
from a13n_service.runs.environments.clients.service import Attachment
from a13n_service.runs.environments.clients.tables import ClientSettingsRow
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import RunRow
from a13n_service.runs.tool_executions.authority import ControlSnapshot
from a13n_service.runs.tool_executions.routing import Caller
from a13n_service.tenancy.access import require_active_workspace
from a13n_service.tenancy.authorize import WorkspaceScope, authorize
from a13n_service.tenancy.cloudflare_deployments import DeploymentRow


@dataclass(frozen=True, slots=True)
class Check:
    request: Request
    attachment: Attachment
    actions: frozenset[EnvironmentAction]
    renewing: bool = False
    descriptor: SessionDescriptor | None = None
    process: ProcessInfo | None = None


async def resolve(
    runtime: Runtime,
    request: Request,
    attachment: Attachment,
    actions: frozenset[EnvironmentAction],
    *,
    renewing: bool = False,
    descriptor: SessionDescriptor | None = None,
    process: ProcessInfo | None = None,
) -> EnvironmentMount:
    result = (await resolve_many(runtime, [Check(request, attachment, actions, renewing, descriptor, process)]))[0]
    if isinstance(result, EnvironmentError):
        raise result
    return result


async def resolve_many(runtime: Runtime, checks: Sequence[Check]) -> list[EnvironmentMount | EnvironmentError]:
    if not checks:
        return []
    async with short_session(runtime.storage) as session:
        snapshot = await ControlSnapshot.load(
            session, runtime, [check.request.caller for check in checks if isinstance(check.request.caller, Caller)]
        )
        active_workspaces: set[str] = set()
        rows = {
            environment.id: (environment, config)
            for environment, config in await session.execute(
                select(EnvironmentRow, ClientSettingsRow)
                .outerjoin(ClientSettingsRow, ClientSettingsRow.environment_id == EnvironmentRow.id)
                .where(EnvironmentRow.id.in_({check.request.environment_id for check in checks}))
            )
        }
        provider_ids = {environment.provider_id for environment, _ in rows.values() if environment.kind == "managed"}
        providers = (
            {
                row.id: (row, deployment_id is not None)
                for row, deployment_id in await session.execute(
                    select(EnvironmentProviderRow, DeploymentRow.id)
                    .outerjoin(DeploymentRow, DeploymentRow.provider_id == EnvironmentProviderRow.id)
                    .where(EnvironmentProviderRow.id.in_(provider_ids))
                )
            }
            if provider_ids
            else {}
        )
        results: list[EnvironmentMount | EnvironmentError] = []
        for check in checks:
            request, attachment, actions = check.request, check.attachment, check.actions
            try:
                pair = rows.get(request.environment_id)
                if pair is None:
                    raise LeaseLost()
                environment, config = pair
                lifecycle = request.caller if isinstance(request.caller, LifecycleCaller) else None
                if isinstance(request.caller, LifecycleCaller):
                    if (
                        environment.kind != "managed"
                        or request.mount_name != "lifecycle"
                        or any(
                            not action.value.startswith(
                                (
                                    "environment.file.",
                                    "environment.shell.",
                                    "environment.process.",
                                    "environment.output.",
                                )
                            )
                            for action in actions
                        )
                        or request.method == "computer.close_observation"
                    ):
                        raise LeaseLost()
                    workspace_id = environment.workspace_id
                    if workspace_id not in active_workspaces:
                        await require_active_workspace(session, workspace_id)
                        active_workspaces.add(workspace_id)
                    mount = EnvironmentMount(name="lifecycle", environment_id=environment.id)
                    grant = None
                elif request.retained_cleanup:
                    run, mount = retained_mount(snapshot, check)
                    grant = None
                    workspace_id = run.workspace_id
                else:
                    assert request.thread_id is not None
                    grant = snapshot.authorize(
                        request.caller,
                        thread_id=request.thread_id,
                        environment_id=request.environment_id,
                        mount_name=request.mount_name,
                        actions=actions,
                        cleanup=check.renewing or request.allows_cancelled_owner,
                    )
                    mount, workspace_id = grant.mount, grant.workspace_id
                if environment.workspace_id != workspace_id or environment.id != attachment.environment_id:
                    raise LeaseLost()
                scope = WorkspaceScope(environment.organization_id, workspace_id)
                if environment.kind == "managed":
                    provider_entry = providers.get(environment.provider_id)
                    if provider_entry is None or environment.workspace_id != provider_entry[0].workspace_id:
                        raise LeaseLost()
                    if grant is not None:
                        authorize(grant.principal, scope, "run", authority=grant.authority)
                    provider_row, managed_deployment = provider_entry
                    provider = resolved_provider(provider_row, managed_deployment=managed_deployment)
                    binding = outbound.binding_for(
                        runtime.registry,
                        environment,
                        provider,
                        executing=True,
                        lifecycle=lifecycle,
                        at=snapshot.current,
                    )
                    if (binding.device_id, binding.credential_version) != (
                        attachment.device_id,
                        attachment.credential_version,
                    ):
                        raise LeaseLost()
                    results.append(
                        connection.resolve(
                            runtime.registry.get("environment", provider.type),
                            Handle.model_validate(environment.handle),
                            mount,
                        ).mount(mount)
                    )
                else:
                    if (
                        environment.kind != "external"
                        or environment.status != "ready"
                        or config is None
                        or config.token_hash is None
                        or config.credential_version != attachment.credential_version
                        or environment.device_id != attachment.device_id
                        or not actions <= EnvironmentPermissionSet.model_validate(config.permission_ceiling).operations
                    ):
                        raise LeaseLost()
                    if grant is not None:
                        authorize(grant.principal, scope, "run", authority=grant.authority)
                    results.append(mount)
            except (ServiceError, LeaseLost, AuthorityRevoked) as error:
                # Unavailable authorization evidence is not a revocation.
                if isinstance(error, ServiceError) and error.code == "unavailable":
                    raise
                results.append(
                    EnvironmentError(
                        "The client execution scope is no longer authorized.",
                        code="environment_denied",
                        details={"dispatch_stage": "pre_dispatch"},
                    )
                )
        return results


def retained_mount(snapshot: ControlSnapshot, check: Check) -> tuple[RunRow, EnvironmentMount]:
    """Existing-work authority is restricted to the admitted Shell and its native resources."""
    request = check.request
    assert isinstance(request.caller, Caller) and request.thread_id is not None
    run = snapshot.owner(request.caller, thread_id=request.thread_id, cleanup=True)
    execution = snapshot.executions.get(request.caller.execution_id or "")
    if execution is None or execution.shell is None:
        raise LeaseLost()
    shell = ShellExecutionState.model_validate(execution.shell)
    state = shell.process
    if state.device_id != check.attachment.device_id:
        raise LeaseLost()
    if check.descriptor is not None and (state.device_id, state.session_id, state.generation) != (
        check.descriptor.device_id,
        check.descriptor.session_id,
        check.descriptor.generation,
    ):
        raise LeaseLost()
    if request.method == "process.resolve_start":
        if request.params.get("target_operation_id") != state.start_operation_id:
            raise LeaseLost()
    elif request.method.startswith("process."):
        if state.identity is None or request.params.get("handle") != state.identity.process_id:
            raise LeaseLost()
    elif request.method == "output.release":
        process = check.process
        if process is None or state.identity is None or process.handle.root != state.identity.process_id:
            raise LeaseLost()
        if request.params.get("reference") not in (
            process.output.stdout.reference.model_dump(mode="json"),
            process.output.stderr.reference.model_dump(mode="json"),
        ):
            raise LeaseLost()
    for value in execution.environment_mounts:
        mount = EnvironmentMount.model_validate(value)
        if mount.name == shell.mount_name == request.mount_name and mount.environment_id == request.environment_id:
            if check.actions <= mount.permission_ceiling.operations:
                return run, mount
    raise LeaseLost()
