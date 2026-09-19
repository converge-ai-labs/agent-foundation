"""Fence one upstream shared-configuration creation per Provider and app.

The adapter first reconciles against upstream. A durable claim permits one POST;
if its result is lost, only finding the upstream configuration permits progress.
There is intentionally no expiry that could turn an uncertain POST into a retry.
"""

from a13n_harness.providers.connector.contracts import ConnectorProviderError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.storage import is_unique_conflict, transaction

from .domain import ConnectorProviderStatus
from .models import ConnectorProviderRecord, ConnectorSharedSetupClaimRecord

_CLAIM_CONSTRAINT = "uq_connector_shared_setup_claims_scope"


async def reserve_shared_setup(
    sessions: async_sessionmaker[AsyncSession],
    configuration_key: str,
    *,
    provider_id: str,
    credential_generation: int,
    connector_key: str,
) -> None:
    try:
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
            session.add(
                ConnectorSharedSetupClaimRecord(
                    id=new_object_id("csc"),
                    provider_id=provider_id,
                    connector_key=connector_key,
                    configuration_key=configuration_key,
                    credential_generation=credential_generation,
                )
            )
    except IntegrityError as error:
        if is_unique_conflict(error, constraint=_CLAIM_CONSTRAINT):
            raise ConnectorProviderError("shared_setup_outcome_unknown") from error
        raise
