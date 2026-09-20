"""Authenticate card ownership before accepting a durable stop request."""

import hmac

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.bots.routines.service import handle_action
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.ingress.provider import ProviderActionDecision
from a13n_service.connectivity.providers.lark.progress import action_toast
from a13n_service.iam import AuthorizationError, WorkspaceAction
from a13n_service.interactions.domain import RunStatus
from a13n_service.interactions.models import RunRecord
from a13n_service.temporal import utc_now

from .authority import ProgressUnavailable, authorize_progress
from .domain import SEALED
from .models import ProgressRecord


def toast(message: str, kind: str = "info") -> JsonObject:
    return action_toast(message, kind)


class ProgressActions:
    async def handle(self, session: AsyncSession, account: AccountRecord, action: ProviderActionDecision) -> JsonObject:
        if action.action.startswith("routine_"):
            try:
                return await handle_action(session, account, action)
            except (AuthorizationError, ProgressUnavailable):
                return {}

        def respond(message: str, kind: str = "info") -> JsonObject:
            return {} if account.provider_key == "slack" else toast(message, kind)

        row = await session.scalar(
            select(ProgressRecord).where(ProgressRecord.run_id == action.reference).with_for_update()
        )
        # One authenticated App connection can serve installations in several workspaces.
        # A different installation must not replace the owning installation's callback result.
        if row is not None and row.account_id != account.id:
            return {}
        if (
            row is None
            or row.account_id != account.id
            or row.provider_key != account.provider_key
            or row.account_version != account.version
            or row.conversation_id != action.conversation_id
            or row.message_id != action.message_id
            or action.actor_id not in row.requester_ids
            or not hmac.compare_digest(row.action_token, action.token)
        ):
            return respond("This action is unavailable. Only the original requester can stop this task.", "error")
        run = await session.get(RunRecord, row.run_id)
        if run is not None and run.status == RunStatus.waiting:
            return respond("This task needs input. Open details to continue.")
        if run is None or run.status in SEALED:
            return respond("This task has already ended.")
        try:
            await authorize_progress(session, account, run, action=WorkspaceAction.run_interrupt)
        except (AuthorizationError, ProgressUnavailable):
            return respond("Task control is no longer authorized.", "error")
        if row.stop_requested and row.done:
            return respond("Unable to stop this task. Open details to try again.", "error")
        if row.stop_requested:
            return respond("A stop request is already pending.")
        row.stop_requested = True
        row.available_at = utc_now()
        # Reopening a failed card delivery must not reset its delivery retry budget.
        row.done = False
        return respond("Stop requested. Checking execution status.")
