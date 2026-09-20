"""Compose setup observations and event subscriptions at the Bot boundary."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.bots.connectivity.setup_tests import SetupObservations
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.ingress.admission_domain import BatchConfiguration
from a13n_service.connectivity.ingress.provider import InboundEvent

from .events import observe


class BotObservations(SetupObservations):
    @staticmethod
    async def ignored(
        database: AsyncSession,
        *,
        account: AccountRecord,
        event: InboundEvent,
        target_kind: str,
        external_target_id: str,
        reason_code: str,
        now: datetime,
    ) -> None:
        if reason_code in {"event_subscription_only", "event_not_selected"}:
            await observe(database, account, event, now)
        await SetupObservations.ignored(
            database,
            account=account,
            event=event,
            target_kind=target_kind,
            external_target_id=external_target_id,
            reason_code=reason_code,
            now=now,
        )

    @staticmethod
    async def admitted(
        database: AsyncSession,
        *,
        account: AccountRecord,
        configuration: BatchConfiguration,
        event: InboundEvent,
        admission_id: str,
        batch_id: str,
        binding_id: str,
        now: datetime,
    ) -> None:
        await observe(database, account, event, now)
        await SetupObservations.admitted(
            database,
            account=account,
            configuration=configuration,
            event=event,
            admission_id=admission_id,
            batch_id=batch_id,
            binding_id=binding_id,
            now=now,
        )
