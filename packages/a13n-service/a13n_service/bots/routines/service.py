"""Channel-scoped proposals and authenticated confirmation transitions."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.bots.progress.models import ProgressRecord
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.ingress.provider import ProviderActionDecision
from a13n_service.connectivity.native_context import InboundRunContext
from a13n_service.interactions.models import RunRecord
from a13n_service.temporal import assume_utc, utc_now

from .authority import authorize_routine
from .context import conversation_id, is_group
from .domain import ProposeRoutine, RoutineDefinition
from .models import RoutineRecord


class RoutineInputError(ValueError):
    """A correctable proposal error, safe to return to the requesting model."""


def changed(row: RoutineRecord) -> None:
    row.version += 1
    row.action_token = secrets.token_urlsafe(32)
    row.updated_at = utc_now()
    row.card_available_at = utc_now()
    row.card_attempts = 0


class RoutineService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def propose(
        self, session: AsyncSession, *, run_id: str, context: InboundRunContext, arguments: ProposeRoutine
    ) -> JsonObject:
        progress = await session.get(ProgressRecord, run_id)
        run = await session.get(RunRecord, run_id)
        if (
            progress is None
            or run is None
            or run.trigger_type != "inbound"
            or len(progress.requester_ids) != 1
            or progress.account_id != context.account_id
            or progress.provider_key != context.provider_key
            or progress.conversation_id != conversation_id(context)
            or not is_group(context)
        ):
            raise RoutineInputError("routine_requester_unavailable")
        now = utc_now()
        owner = progress.requester_ids[0]
        account = await session.get(AccountRecord, context.account_id)
        if account is None:
            raise RoutineInputError("routine_source_unavailable")
        key = hashlib.sha256(f"{run_id}:{arguments.request_key}".encode()).hexdigest()[:32]
        identifier = arguments.routine_id or f"routine_{key}"
        row = await session.get(RoutineRecord, identifier, with_for_update=True)
        if row is not None and (
            row.account_id != account.id or row.conversation_id != progress.conversation_id or row.owner_id != owner
        ):
            raise RoutineInputError("routine_not_owned_in_this_channel")
        if row is not None and arguments.routine_id is None:
            return {"routine_id": row.id, "state": row.state, "confirmation_required": row.proposal_json is not None}
        if arguments.routine_id is not None and (row is None or row.state == "deleted"):
            raise RoutineInputError("routine_unavailable")
        if arguments.definition is not None and arguments.definition.schedule.next_after(now) is None:
            raise RoutineInputError("routine_time_must_be_in_future")
        await session.get(AccountRecord, context.account_id, with_for_update=True)
        if row is None:
            count = await session.scalar(
                select(func.count())
                .select_from(RoutineRecord)
                .where(RoutineRecord.account_id == account.id, RoutineRecord.state != "deleted")
            )
            if count is not None and count >= 100:
                raise RoutineInputError("routine_account_limit_reached")
            target = await session.get(AccountTargetRecord, context.target_id) if context.target_id else None
            row = RoutineRecord(
                id=identifier,
                account_id=account.id,
                account_version=account.version,
                source_run_id=run_id,
                target_version=target.version if target else None,
                native_context_json=context.model_dump(mode="json"),
                conversation_id=progress.conversation_id,
                owner_id=owner,
                state="draft",
                version=1,
                action_token=secrets.token_urlsafe(32),
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        await authorize_routine(session, row)
        row.proposal_json = arguments.model_dump(mode="json")
        row.proposal_expires_at = now + timedelta(hours=24)
        changed(row)
        await session.flush()
        return {
            "routine_id": row.id,
            "state": row.state,
            "confirmation_required": True,
            "message": "Awaiting the requester's card confirmation. No change is active yet.",
        }

    async def list(self, session: AsyncSession, *, context: InboundRunContext, cursor: str | None) -> JsonObject:
        query = select(RoutineRecord).where(
            RoutineRecord.account_id == context.account_id,
            RoutineRecord.conversation_id == conversation_id(context),
            RoutineRecord.state != "deleted",
        )
        if cursor:
            query = query.where(RoutineRecord.id > cursor)
        rows = list((await session.scalars(query.order_by(RoutineRecord.id).limit(21))).all())
        items = []
        for row in rows[:20]:
            run = await session.get(RunRecord, row.last_run_id) if row.last_run_id else None
            items.append(
                {
                    "id": row.id,
                    "state": row.state,
                    "definition": row.definition_json,
                    "pending_change": row.proposal_json,
                    "next_run_at": assume_utc(row.next_run_at).isoformat() if row.next_run_at else None,
                    "last_run_status": run.status if run else None,
                    "last_error": row.last_error,
                    "card_error": row.card_error,
                }
            )
        return {"items": items, "next_cursor": rows[19].id if len(rows) > 20 else None}


async def handle_action(session: AsyncSession, account: AccountRecord, action: ProviderActionDecision) -> JsonObject:
    row = await session.get(RoutineRecord, action.reference, with_for_update=True)
    if (
        row is None
        or account.provider_key not in {"slack", "lark"}
        or row.account_id != account.id
        or row.conversation_id != action.conversation_id
        or row.message_id != action.message_id
        or row.owner_id != action.actor_id
        or not hmac.compare_digest(row.action_token, action.token)
        or row.state == "deleted"
    ):
        return {}
    # Pausing/deleting remains possible even if execution authority has since been revoked.
    if action.action == "routine_confirm":
        if (
            row.proposal_json is None
            or row.proposal_expires_at is None
            or assume_utc(row.proposal_expires_at) <= utc_now()
        ):
            return {}
        proposal = ProposeRoutine.model_validate(row.proposal_json)
        operation = proposal.operation
    else:
        proposal = None
        operation = action.action.removeprefix("routine_")
    if operation in {"save", "resume"}:
        await authorize_routine(session, row)
        definition = (
            proposal.definition
            if proposal and proposal.definition
            else (RoutineDefinition.model_validate(row.definition_json) if row.definition_json else None)
        )
        if definition is None:
            return {}
        next_at = definition.schedule.next_after(utc_now())
        if next_at is None:
            row.last_error = "scheduled_time_expired"
            changed(row)
            return {}
        row.definition_json = definition.model_dump(mode="json")
        row.state = "active"
        row.next_run_at = next_at
        row.last_error = None
        row.attempts = 0
        row.available_at = utc_now()
    elif operation == "pause":
        row.state = "paused"
        row.next_run_at = None
    elif operation == "delete":
        row.state = "deleted"
        row.next_run_at = None
    elif operation == "cancel":
        if row.state == "draft":
            row.state = "deleted"
    else:
        return {}
    row.proposal_json = None
    row.proposal_expires_at = None
    changed(row)
    return {}
