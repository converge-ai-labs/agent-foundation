"""Batch compensation over live owners; notifications only shorten the scan interval."""

import asyncio

from a13n_logging import get_logger
from redis.exceptions import RedisError
from sqlalchemy import or_, select, tuple_

from a13n_service.infra.db import short_session
from a13n_service.resources.egress_credentials.notifications import CHANNEL
from a13n_service.resources.egress_credentials.tables import CredentialRow
from a13n_service.runs.environments.egress.tables import SessionCredentialRow
from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)


class Synchronizer:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.changed = asyncio.Event()
        self.offset = 0

    async def scan(self, *, force: bool = False) -> int:
        owners = self.runtime.environment_contexts.active()
        if not owners:
            self.offset = 0
            return 0
        batch = self.runtime.settings.environments.credential_sync_batch
        # Round-robin bounded batches keep a large owner population from starving its tail.
        self.offset %= len(owners)
        selected = (owners[self.offset :] + owners[: self.offset])[:batch]
        self.offset += len(selected)
        row = SessionCredentialRow
        async with short_session(self.runtime.storage) as session:
            pending = set(
                (
                    await session.execute(
                        select(
                            row.thread_id,
                            row.environment_id,
                            row.binding_name,
                            row.session_version,
                        )
                        .join(CredentialRow, CredentialRow.id == row.credential_id)
                        .where(
                            tuple_(row.thread_id, row.environment_id, row.binding_name, row.session_version).in_(
                                [owner.sync_key for owner in selected]
                            ),
                            or_(
                                row.applied_credential_version.is_(None),
                                row.applied_credential_version != CredentialRow.credential_version,
                            ),
                        )
                        .distinct()
                    )
                ).tuples()
            )

        async def apply(owner):
            try:
                if owner.sync_key in pending or owner.sync_failed:
                    return int(await owner.synchronize(force=force))
            except Exception as error:
                logger.warning(
                    "Session credential synchronization failed",
                    extra={
                        "thread_id": owner.thread_id,
                        "environment_id": owner.key[0],
                        "error_type": type(error).__name__,
                    },
                )
            return 0

        # Limit native concurrency independently from the bounded database batch.
        semaphore = asyncio.Semaphore(8)

        async def limited(owner):
            async with semaphore:
                return await apply(owner)

        return sum(await asyncio.gather(*(limited(owner) for owner in selected)))

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self._listen(), name="credential-notifications")
            while True:
                force = self.changed.is_set()
                self.changed.clear()
                try:
                    await self.scan(force=force)
                except Exception as error:
                    logger.warning("Credential version scan failed", extra={"error_type": type(error).__name__})
                try:
                    await asyncio.wait_for(
                        self.changed.wait(), timeout=self.runtime.settings.environments.credential_sync_seconds
                    )
                except TimeoutError:
                    pass

    async def _listen(self) -> None:
        interval = self.runtime.settings.environments.credential_sync_seconds
        while True:
            try:
                async with self.runtime.redis.pubsub() as channel:
                    await channel.subscribe(CHANNEL)
                    while True:
                        message = await channel.get_message(ignore_subscribe_messages=True, timeout=interval)
                        if message is not None:
                            self.changed.set()
            except RedisError:
                await asyncio.sleep(interval)
