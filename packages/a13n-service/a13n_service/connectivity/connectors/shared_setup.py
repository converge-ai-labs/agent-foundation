"""Fence one upstream shared-configuration creation per Provider and app.

The adapter first reconciles against upstream. A durable claim permits one POST;
if its result is lost, only finding the upstream configuration permits progress.
There is intentionally no expiry that could turn an uncertain POST into a retry.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import transaction

from .contracts import ConnectorProviderError
from .domain import ConnectorProviderStatus
from .models import ConnectorProviderRecord


async def reserve_shared_setup(
    sessions: async_sessionmaker[AsyncSession], *, provider_id: str, credential_generation: int, connector_key: str
) -> None:
    async with transaction(sessions) as session:
        record = await session.scalar(
            select(ConnectorProviderRecord).where(ConnectorProviderRecord.id == provider_id).with_for_update()
        )
        if (
            record is None
            or record.status != ConnectorProviderStatus.active.value
            or record.credential_generation != credential_generation
        ):
            raise ConnectorProviderError("connector_provider_changed")
        claims = record.setup_claims_json
        if connector_key in claims:
            raise ConnectorProviderError("shared_setup_outcome_unknown")
        record.setup_claims_json = {**claims, connector_key: credential_generation}
