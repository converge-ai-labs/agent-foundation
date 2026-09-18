"""Control-owned projection of canonical Run status and cancellation."""

import secrets
from datetime import timedelta

import anyio
import httpx2
from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.connectivity.http import ConnectivityHttpError, EndpointValidator
from a13n_service.iam import AuthorizationError
from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.control_domain import InterruptRequest
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import ObjectStoreError, transaction
from a13n_service.temporal import utc_now

from .authority import ProgressUnavailable
from .delivery import CardDelivery
from .models import ProgressRecord


class ProgressService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        commands: InteractionCommands,
        http: httpx2.AsyncClient,
        endpoints: EndpointValidator,
        protector: SecretProtector,
        *,
        public_origin: str | None = None,
    ) -> None:
        self.sessions = sessions
        self.commands = commands
        self.delivery = CardDelivery(sessions, http, endpoints, protector, public_origin=public_origin)

    async def scan(self) -> Sweep:
        completed = failed = 0

        async def worker() -> None:
            nonlocal completed, failed
            for _ in range(5):
                claim = await self._claim()
                if claim is None:
                    break
                run_id, lease = claim
                try:
                    with anyio.fail_after(40):
                        await self._process(run_id, lease)
                    completed += 1
                except (ConnectivityHttpError, httpx2.HTTPError, ObjectStoreError, TimeoutError):
                    await self.delivery.finish(run_id, lease, error="delivery_failed")
                    failed += 1
                except (AuthorizationError, InteractionCommandError, SecretProtectionError, ProgressUnavailable):
                    await self.delivery.finish(run_id, lease, error="control_or_source_unavailable")
                    failed += 1

        async def resilient_worker() -> None:
            nonlocal failed
            try:
                await worker()
            except DBAPIError:
                # Unfinished claims expire; a database outage never restarts the process.
                failed += 1

        async with anyio.create_task_group() as tasks:
            for _ in range(4):
                tasks.start_soon(resilient_worker)
        return Sweep(examined=completed + failed, completed=completed, failed=failed)

    async def _claim(self) -> tuple[str, str] | None:
        now = utc_now()
        async with transaction(self.sessions) as session:
            row = await session.scalar(
                select(ProgressRecord)
                .where(
                    ProgressRecord.done.is_(False),
                    ProgressRecord.available_at <= now,
                    or_(ProgressRecord.lease_until.is_(None), ProgressRecord.lease_until <= now),
                )
                .order_by(ProgressRecord.available_at, ProgressRecord.run_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            lease = secrets.token_urlsafe(24)
            row.lease_token = lease
            row.lease_until = now + timedelta(seconds=60)
            return row.run_id, lease

    async def _process(self, run_id: str, lease: str) -> None:
        work = await self.delivery.load(run_id, lease)
        if work is None:
            return
        if work.progress.stopping and not work.progress.sealed:
            await self.commands.active.interrupt(
                actor=work.actor,
                run_id=run_id,
                idempotency_key=f"bot-stop:{run_id}",
                request=InterruptRequest(
                    expected_run_version=work.run_version, expected_thread_version=work.thread_version
                ),
            )
        await self.delivery.publish(run_id, lease)
