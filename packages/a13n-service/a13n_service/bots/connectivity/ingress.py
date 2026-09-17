"""Bot-owned selection and receipt contribution to canonical Run acceptance."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.bots.memory.binding import BotMemoryBinding
from a13n_service.bots.memory.bindings import bind
from a13n_service.bots.memory.selection import select_binding
from a13n_service.bots.memory.settings import settings_version
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.ingress.admission_domain import PreparedIngressBatch
from a13n_service.interactions.control_domain import RunAcceptanceReceipt, SteerReceipt
from a13n_service.interactions.errors import RunAcceptanceError

from .setup_tests import record_test_acceptance


@dataclass(frozen=True)
class PreparedBotIngress:
    account_id: str
    external_conversation_id: str
    binding: BotMemoryBinding
    settings_version: int

    async def accept(
        self,
        session: AsyncSession,
        batch: PreparedIngressBatch,
        receipt: RunAcceptanceReceipt | SteerReceipt,
        now: datetime,
    ) -> None:
        account = await session.scalar(
            select(AccountRecord).where(AccountRecord.id == self.account_id).with_for_update()
        )
        if (
            account is None
            or await settings_version(session, self.account_id) != self.settings_version
            or await select_binding(session, account, self.external_conversation_id, lock=True) != self.binding
        ):
            raise RunAcceptanceError("memory_binding_conflict", "Conversation configuration changed during acceptance")
        if isinstance(receipt, RunAcceptanceReceipt):
            await bind(session, receipt.run_id, self.binding)
        await record_test_acceptance(
            session,
            batch_id=batch.batch_id,
            run_id=receipt.run_id,
            steer_id=receipt.steer_id if isinstance(receipt, SteerReceipt) else None,
            now=now,
        )


class BotIngress:
    async def prepare(
        self, session: AsyncSession, account: AccountRecord, external_conversation_id: str
    ) -> PreparedBotIngress:
        binding = await select_binding(session, account, external_conversation_id)
        if binding is None:
            raise RunAcceptanceError("memory_binding_unavailable", "Unsupported conversation installation")
        return PreparedBotIngress(
            account.id, external_conversation_id, binding, await settings_version(session, account.id)
        )
