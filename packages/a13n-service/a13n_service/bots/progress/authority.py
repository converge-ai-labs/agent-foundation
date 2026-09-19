"""Revalidate the original execution authority for progress and control."""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.iam import AuthenticatedActor, PrincipalRef, PrincipalType, WorkspaceAction, authorize_workspace
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.interactions.access import authorize_interaction
from a13n_service.interactions.models import RunRecord, SessionRecord


class ProgressUnavailable(ValueError):
    """The accepted task no longer has a usable provider source."""


async def authorize_progress(
    session: AsyncSession, account: AccountRecord, run: RunRecord, *, action: WorkspaceAction
) -> tuple[AuthenticatedActor, str]:
    source = await session.get(SessionRecord, run.session_id)
    if (
        source is None
        or account.deleted_at is not None
        or account.status != "active"
        or account.organization_id != run.organization_id
        or account.workspace_id != source.workspace_id
        or run.authority_principal_type != "service_account"
        or account.execution_service_account_id != run.authority_principal_id
    ):
        raise ProgressUnavailable("source_changed")
    actor = AuthenticatedActor(
        principal=PrincipalRef(principal_type=PrincipalType.service_account, principal_id=run.authority_principal_id),
        auth_method="internal",
        credential_id="bot-task-control",
        boundary_workspace_id=source.workspace_id,
    )
    await authorize_workspace(
        session, actor=actor, workspace_id=source.workspace_id, action=WorkspaceAction.application_account_use
    )
    await authorize_interaction(
        session,
        actor=actor,
        workspace_id=source.workspace_id,
        session_id=run.session_id,
        agent_id=run.agent_id,
        action=action,
    )
    workspace = await session.get(WorkspaceRecord, source.workspace_id)
    if workspace is None:
        raise ProgressUnavailable("workspace_unavailable")
    return actor, workspace.key
