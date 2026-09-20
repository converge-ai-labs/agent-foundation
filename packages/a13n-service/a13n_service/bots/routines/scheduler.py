"""Control-owned schedule reconciliation through atomic canonical Run acceptance."""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.bots.memory.bindings import bind
from a13n_service.bots.memory.selection import select_binding
from a13n_service.bots.progress.authority import ProgressUnavailable
from a13n_service.bots.progress.models import ProgressRecord
from a13n_service.connectivity.accounts.reception import InputOverride
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.native_context import InboundRunContext
from a13n_service.iam import AuthorizationError
from a13n_service.interactions.command_values import StartRunCommand
from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.control_domain import RunAcceptanceReceipt, SteerReceipt
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.input import AgentInput, TextContent
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, utc_now

from .authority import authorize_routine
from .cards import RoutineCards
from .domain import RoutineDefinition
from .models import RoutineRecord
from .service import changed


@dataclass(frozen=True)
class Claim:
    id: str
    version: int
    lease: str
    due: datetime


class _LostClaim(Exception):
    pass


class RoutineScheduler:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], commands: InteractionCommands, cards: RoutineCards
    ) -> None:
        self.sessions, self.commands, self.cards = sessions, commands, cards

    async def scan(self) -> Sweep:
        completed = failed = 0
        for _ in range(10):
            claim = await self.claim()
            if claim is None:
                break
            try:
                with anyio.fail_after(40):
                    await self.execute(claim)
                completed += 1
            except _LostClaim:
                await self.finish_error(claim, "configuration_changed", pause=False)
            except (AuthorizationError, ProgressUnavailable):
                await self.finish_error(claim, "execution_authority_changed", pause=True)
                failed += 1
            except (InteractionCommandError, TimeoutError):
                await self.finish_error(claim, "run_acceptance_failed", pause=False)
                failed += 1
        for _ in range(10):
            if not await self.cards.publish_one():
                break
        return Sweep(examined=completed + failed, completed=completed, failed=failed)

    async def claim(self) -> Claim | None:
        now = utc_now()
        async with transaction(self.sessions) as session:
            row = await session.scalar(
                select(RoutineRecord)
                .where(
                    RoutineRecord.state == "active",
                    RoutineRecord.next_run_at <= now,
                    RoutineRecord.available_at <= now,
                    or_(RoutineRecord.lease_until.is_(None), RoutineRecord.lease_until <= now),
                )
                .order_by(RoutineRecord.available_at, RoutineRecord.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            if row.last_run_id:
                last = await session.get(RunRecord, row.last_run_id)
                if last is not None and last.status in {"accepted", "running", "waiting"}:
                    row.available_at = now + timedelta(seconds=30)
                    return None
            lease = secrets.token_urlsafe(24)
            row.lease_token = lease
            row.lease_until = now + timedelta(seconds=60)
            assert row.next_run_at is not None and row.lease_token is not None
            return Claim(row.id, row.version, lease, assume_utc(row.next_run_at))

    async def execute(self, claim: Claim) -> None:
        async with short_session(self.sessions) as session:
            row = await self._current(session, claim)
            account, source, actor = await authorize_routine(session, row)
            workspace_id = account.workspace_id
            agent_id = source.agent_id
            definition = RoutineDefinition.model_validate(row.definition_json)
            context = InboundRunContext.model_validate(row.native_context_json)
            # Future work posts a new task card in the same channel. It cannot schedule more work.
            context = context.model_copy(update={"action_policy": {**context.action_policy, "reply_mode": "main"}})
            target = await session.get(AccountTargetRecord, context.target_id) if context.target_id else None
            override = (
                InputOverride.model_validate(target.config_override_json).invocation_override()
                if (target and target.config_override_json)
                else None
            )

        async def commit(session: AsyncSession, receipt: RunAcceptanceReceipt | SteerReceipt) -> None:
            current = await self._current(session, claim, lock=True)
            account, _, _ = await authorize_routine(session, current)
            if isinstance(receipt, SteerReceipt):
                raise _LostClaim()
            binding = await select_binding(session, account, current.conversation_id)
            if binding is not None:
                await bind(session, receipt.run_id, binding)
            now = utc_now()
            session.add(
                ProgressRecord(
                    run_id=receipt.run_id,
                    account_id=account.id,
                    account_version=account.version,
                    provider_key="slack",
                    conversation_id=current.conversation_id,
                    source_message_id=str(context.provider_context["root_thread_ts"]),
                    reply_in_thread=False,
                    requester_ids=[current.owner_id],
                    action_token=secrets.token_urlsafe(32),
                    available_at=now,
                    created_at=now,
                    done=False,
                )
            )
            current.last_run_id = receipt.run_id
            current.next_run_at = definition.schedule.next_after(max(now, claim.due))
            if current.next_run_at is None:
                current.state = "completed"
            current.available_at = now
            current.last_error = None
            current.attempts = 0
            current.lease_token = None
            current.lease_until = None
            changed(current)

        await self.commands.runs.start(
            actor=actor,
            workspace_id=workspace_id,
            idempotency_key=f"routine:{claim.id}:{claim.version}:{claim.due.isoformat()}",
            request=StartRunCommand(
                agent_id=agent_id,
                config_override=override,
                input=AgentInput(
                    schema_version="1",
                    content=(
                        TextContent(
                            text=(
                                f"Execute the confirmed scheduled task: {definition.title}\n"
                                f"Scheduled for {claim.due.isoformat()} ({definition.schedule.timezone}).\n"
                                f"{definition.prompt}\n"
                                "Deliver the result to this channel using slack.reply. Do not create another schedule."
                            )
                        ),
                    ),
                ),
            ),
            origin=SubmissionOrigin(
                trigger_type="bot_schedule", native_tool_contexts=(context.model_dump(mode="json"),)
            ),
            transaction_hook=commit,
        )

    async def _current(self, session: AsyncSession, claim: Claim, *, lock: bool = False) -> RoutineRecord:
        row = await session.get(RoutineRecord, claim.id, with_for_update=lock)
        if (
            row is None
            or row.state != "active"
            or row.version != claim.version
            or row.lease_token != claim.lease
            or row.lease_until is None
            or assume_utc(row.lease_until) <= utc_now()
        ):
            raise _LostClaim()
        return row

    async def finish_error(self, claim: Claim, error: str, *, pause: bool) -> None:
        async with transaction(self.sessions) as session:
            row = await session.get(RoutineRecord, claim.id, with_for_update=True)
            if row is None or row.lease_token != claim.lease:
                return
            row.lease_token = None
            row.lease_until = None
            if row.version != claim.version:
                return
            row.last_error = error
            row.attempts += 1
            row.available_at = utc_now() + timedelta(seconds=min(300, 2**row.attempts))
            if pause or row.attempts >= 5:
                row.state = "paused"
                row.next_run_at = None
                changed(row)
