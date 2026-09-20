"""Revalidate exact installation, channel, and execution authority before future work."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.bots.progress.authority import ProgressUnavailable, authorize_progress
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.native_context import InboundRunContext
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent
from a13n_service.interactions.models import RunRecord

from .context import conversation_id, is_group
from .models import RoutineRecord


async def authorize_routine(
    session: AsyncSession, row: RoutineRecord
) -> tuple[AccountRecord, RunRecord, AuthenticatedActor]:
    account = await session.get(AccountRecord, row.account_id, with_for_update=True, populate_existing=True)
    run = await session.get(RunRecord, row.source_run_id)
    if (
        account is None
        or run is None
        or account.provider_key not in {"slack", "lark"}
        or account.version != row.account_version
        or not account.receive_enabled
    ):
        raise ProgressUnavailable("routine_source_changed")
    actor, _ = await authorize_progress(session, account, run, action=WorkspaceAction.run_read)
    context = InboundRunContext.model_validate(row.native_context_json)
    if (
        context.execution_principal_ref != actor.principal
        or context.account_id != account.id
        or context.provider_key != account.provider_key
        or not is_group(context)
        or conversation_id(context) != row.conversation_id
    ):
        raise ProgressUnavailable("routine_source_changed")
    target = await session.scalar(
        select(AccountTargetRecord)
        .where(
            AccountTargetRecord.account_id == account.id,
            AccountTargetRecord.target_kind == "conversation",
            AccountTargetRecord.external_target_id == row.conversation_id,
        )
        .with_for_update()
    )
    if (
        (target.id if target else None) != context.target_id
        or (target.version if target else None) != row.target_version
        or (target is not None and not target.receive_enabled)
        or (target is None and account.reception_scope == "configured_targets")
    ):
        raise ProgressUnavailable("routine_channel_changed")
    agent_id = target.agent_id if target is not None and target.agent_id else account.default_agent_id
    if agent_id != run.agent_id:
        raise ProgressUnavailable("routine_agent_changed")
    await authorize_agent(
        session,
        actor=actor,
        workspace_id=account.workspace_id,
        agent_id=run.agent_id,
        action=WorkspaceAction.agent_invoke,
    )
    return account, run, actor
