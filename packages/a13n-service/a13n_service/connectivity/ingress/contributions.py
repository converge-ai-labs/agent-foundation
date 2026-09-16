"""Transaction-local application participation in authenticated ingress acceptance."""

from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.interactions.control_domain import RunAcceptanceReceipt, SteerReceipt

from .admission_domain import BatchConfiguration, PreparedIngressBatch
from .provider import InboundEvent


class PreparedIngressContribution(Protocol):
    async def accept(
        self,
        session: AsyncSession,
        batch: PreparedIngressBatch,
        receipt: RunAcceptanceReceipt | SteerReceipt,
        now: datetime,
    ) -> None: ...


class IngressContribution(Protocol):
    async def prepare(
        self, session: AsyncSession, account: AccountRecord, external_conversation_id: str
    ) -> PreparedIngressContribution: ...


class IngressObservations(Protocol):
    async def ignored(
        self,
        database: AsyncSession,
        *,
        account: AccountRecord,
        event: InboundEvent,
        target_kind: str,
        external_target_id: str,
        reason_code: str,
        now: datetime,
    ) -> None: ...
    async def admitted(
        self,
        database: AsyncSession,
        *,
        account: AccountRecord,
        configuration: BatchConfiguration,
        event: InboundEvent,
        admission_id: str,
        batch_id: str,
        binding_id: str,
        now: datetime,
    ) -> None: ...
    async def rejected(self, database: AsyncSession, *, batch_id: str, reason_code: str) -> None: ...
