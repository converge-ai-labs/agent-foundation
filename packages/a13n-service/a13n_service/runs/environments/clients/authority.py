"""Control rechecks frozen run selection and live client authority before native dispatch."""

from a13n_harness.environment.models import EnvironmentAction, EnvironmentError, EnvironmentPermissionSet
from sqlalchemy import select

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import AuthorityRevoked, LeaseLost
from a13n_service.runs.environments.clients.routing import Request
from a13n_service.runs.environments.clients.service import Attachment
from a13n_service.runs.environments.clients.tables import ClientSettingsRow
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tool_executions.authority import authorize_control
from a13n_service.tenancy.authorize import WorkspaceScope, authorize


async def resolve(
    runtime: Runtime,
    request: Request,
    attachment: Attachment,
    actions: frozenset[EnvironmentAction],
    *,
    renewing: bool = False,
) -> EnvironmentMount:
    try:
        async with short_session(runtime.storage) as session:
            grant = await authorize_control(
                session,
                runtime,
                request.caller,
                thread_id=request.thread_id,
                environment_id=request.environment_id,
                actions=actions,
                cleanup=renewing or request.allows_cancelled_owner,
            )
            environment = await session.get(EnvironmentRow, request.environment_id)
            if environment is not None and environment.kind == "managed":
                from a13n_service.runs.environments import outbound

                if environment.workspace_id != grant.workspace_id or environment.id != attachment.environment_id:
                    raise LeaseLost()
                binding = await outbound.authorize_use(session, runtime, environment, grant.principal, grant.authority)
                if (
                    binding.device_id != attachment.device_id
                    or binding.credential_version != attachment.credential_version
                ):
                    raise LeaseLost()
                return grant.mount
            row = (
                await session.execute(
                    select(EnvironmentRow.organization_id, ClientSettingsRow.permission_ceiling)
                    .join(ClientSettingsRow, ClientSettingsRow.environment_id == EnvironmentRow.id)
                    .where(
                        EnvironmentRow.id == request.environment_id,
                        EnvironmentRow.id == attachment.environment_id,
                        EnvironmentRow.workspace_id == grant.workspace_id,
                        EnvironmentRow.owner_principal_id == grant.principal.id,
                        EnvironmentRow.kind == "external",
                        EnvironmentRow.status == "ready",
                        EnvironmentRow.device_id == attachment.device_id,
                        ClientSettingsRow.credential_version == attachment.credential_version,
                        ClientSettingsRow.token_hash.is_not(None),
                    )
                )
            ).one_or_none()
            if row is None or not actions <= EnvironmentPermissionSet.model_validate(row.permission_ceiling).operations:
                raise LeaseLost()
            authorize(grant.principal, WorkspaceScope(row.organization_id, grant.workspace_id), "write")
            return grant.mount
    except (ServiceError, LeaseLost, AuthorityRevoked) as error:
        raise EnvironmentError(
            "The client execution scope is no longer authorized.",
            code="environment_denied",
            details={"dispatch_stage": "pre_dispatch"},
        ) from error
