"""Operation-local, batched Control authorization for Sessions and their transfers."""

from collections.abc import Sequence
from dataclasses import dataclass

from a13n_harness.environment.models import EnvironmentAction, EnvironmentError, EnvironmentPermissionSet
from sqlalchemy import select

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.providers.service import resolved_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.attempts import AuthorityRevoked, LeaseLost
from a13n_service.runs.environments import connection, outbound
from a13n_service.runs.environments.clients.routing import Request
from a13n_service.runs.environments.clients.service import Attachment
from a13n_service.runs.environments.clients.tables import ClientSettingsRow
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tool_executions.authority import ControlSnapshot
from a13n_service.tenancy.authorize import WorkspaceScope, authorize


@dataclass(frozen=True, slots=True)
class Check:
    request: Request
    attachment: Attachment
    actions: frozenset[EnvironmentAction]
    renewing: bool = False


async def resolve(
    runtime: Runtime,
    request: Request,
    attachment: Attachment,
    actions: frozenset[EnvironmentAction],
    *,
    renewing: bool = False,
) -> EnvironmentMount:
    result = (await resolve_many(runtime, [Check(request, attachment, actions, renewing)]))[0]
    if isinstance(result, EnvironmentError):
        raise result
    return result


async def resolve_many(runtime: Runtime, checks: Sequence[Check]) -> list[EnvironmentMount | EnvironmentError]:
    if not checks:
        return []
    async with short_session(runtime.storage) as session:
        snapshot = await ControlSnapshot.load(session, runtime, [check.request.caller for check in checks])
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
                row.id: (row.workspace_id, resolved_provider(row))
                for row in await session.scalars(
                    select(EnvironmentProviderRow).where(EnvironmentProviderRow.id.in_(provider_ids))
                )
            }
            if provider_ids
            else {}
        )
        results: list[EnvironmentMount | EnvironmentError] = []
        for check in checks:
            request, attachment, actions = check.request, check.attachment, check.actions
            try:
                grant = snapshot.authorize(
                    request.caller,
                    thread_id=request.thread_id,
                    environment_id=request.environment_id,
                    actions=actions,
                    cleanup=check.renewing or request.allows_cancelled_owner,
                )
                pair = rows.get(request.environment_id)
                if pair is None:
                    raise LeaseLost()
                environment, config = pair
                if environment.workspace_id != grant.workspace_id or environment.id != attachment.environment_id:
                    raise LeaseLost()
                scope = WorkspaceScope(environment.organization_id, grant.workspace_id)
                if environment.kind == "managed":
                    provider_entry = providers.get(environment.provider_id)
                    if provider_entry is None or environment.workspace_id != provider_entry[0]:
                        raise LeaseLost()
                    provider = provider_entry[1]
                    authorize(grant.principal, scope, "run", authority=grant.authority)
                    binding = outbound.binding_for(runtime.registry, environment, provider, executing=True)
                    if (binding.device_id, binding.credential_version) != (
                        attachment.device_id,
                        attachment.credential_version,
                    ):
                        raise LeaseLost()
                    results.append(
                        connection.resolve(
                            runtime.registry.get("environment", provider.type),
                            Handle.model_validate(environment.handle),
                            grant.mount,
                        ).mount(grant.mount)
                    )
                else:
                    if (
                        environment.kind != "external"
                        or environment.status != "ready"
                        or config is None
                        or config.token_hash is None
                        or config.credential_version != attachment.credential_version
                        or environment.device_id != attachment.device_id
                        or environment.owner_principal_id != grant.principal.id
                        or not actions <= EnvironmentPermissionSet.model_validate(config.permission_ceiling).operations
                    ):
                        raise LeaseLost()
                    authorize(grant.principal, scope, "write")
                    results.append(grant.mount)
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
