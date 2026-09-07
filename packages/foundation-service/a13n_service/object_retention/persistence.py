"""Short relational fences shared by object writers and reference commits."""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.ids import new_object_id
from a13n_service.temporal import utc_now

from .models import ObjectPublicationRecord


async def lock_publication(database: AsyncSession, key: str, *, observed_at: datetime) -> ObjectPublicationRecord:
    """Reserve absent keys as well as lock existing ones, including on SQLite."""
    insert = sqlite_insert if database.get_bind().dialect.name == "sqlite" else postgres_insert
    await database.execute(
        insert(ObjectPublicationRecord)
        .values(
            key=key,
            generation=new_object_id("opg"),
            phase="ready",
            object_version=None,
            lease_expires_at=None,
            created_at=observed_at,
            updated_at=observed_at,
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )
    record = await database.scalar(
        select(ObjectPublicationRecord)
        .where(ObjectPublicationRecord.key == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert record is not None
    return record


async def require_object_publications(database: AsyncSession, keys: Iterable[str]) -> None:
    """Lock before adding business references, in the same transaction as acceptance.

    Existing unregistered objects were verified by the owning publication path.
    Reserving their key also serializes them against discovery by the collector.
    """
    for key in sorted(set(keys)):
        record = await lock_publication(database, key, observed_at=utc_now())
        if record.phase != "ready":
            raise ApplicationError(
                "object_publication_changed",
                "Object publication changed; retry the operation.",
                category=ErrorCategory.unavailable,
                retry_after_seconds=1,
            )
