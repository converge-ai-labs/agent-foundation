"""Connectivity-owned notification polling with durable admission before cursor movement."""

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
import httpx2
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.http import ConnectivityHttpError, EndpointValidator
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.management import canonical_json
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .notifications import notification_event, scan_notifications
from .polling_config import POLLING_VERSION, GitHubPollingConfig, GitHubPollingCredentials
from .polling_models import GitHubPollRecord
from .rest import GitHubREST

logger = logging.getLogger("a13n_service.connectivity.github.polling")


@dataclass(frozen=True)
class PollClaim:
    account_id: str
    generation: int
    account_version: int
    credential_generation: int
    since: datetime
    interval: int


class GitHubNotificationPoller:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        ingress: IngressEventService,
        http: httpx2.AsyncClient,
        endpoints: EndpointValidator,
        *,
        instance_id: str,
        clock: Clock = utc_now,
    ) -> None:
        self.sessions, self.ingress, self.http, self.endpoints = sessions, ingress, http, endpoints
        self.instance_id, self.clock = instance_id, clock

    async def run(self) -> None:
        while True:
            try:
                worked = await self.run_once()
            except Exception:
                logger.warning("github_poll_scan_failed")
                worked = False
            await anyio.sleep(0 if worked else 2)

    async def claim(self) -> PollClaim | None:
        now = self.clock()
        async with transaction(self.sessions) as db:
            account = await db.scalar(
                select(AccountRecord)
                .outerjoin(GitHubPollRecord, GitHubPollRecord.account_id == AccountRecord.id)
                .where(
                    AccountRecord.provider_key == "github",
                    AccountRecord.provider_config_version == POLLING_VERSION,
                    AccountRecord.status == "active",
                    AccountRecord.receive_enabled.is_(True),
                    AccountRecord.deleted_at.is_(None),
                    or_(GitHubPollRecord.account_id.is_(None), GitHubPollRecord.available_at <= now),
                    or_(GitHubPollRecord.claim_expires_at.is_(None), GitHubPollRecord.claim_expires_at <= now),
                )
                .order_by(GitHubPollRecord.available_at.asc().nullsfirst(), AccountRecord.id)
                .limit(1)
                .with_for_update(of=AccountRecord, skip_locked=True)
            )
            if account is None:
                return None
            config = GitHubPollingConfig.model_validate(account.provider_config_json)
            row = await db.get(GitHubPollRecord, account.id, with_for_update=True)
            if row is None:
                row = GitHubPollRecord(
                    account_id=account.id,
                    cursor_at=now - timedelta(seconds=config.initial_lookback_seconds),
                    available_at=now,
                    has_history=False,
                    claim_generation=0,
                )
                db.add(row)
            row.claim_generation += 1
            row.claim_owner = self.instance_id
            row.claim_expires_at = now + timedelta(seconds=120)
            since = assume_utc(row.cursor_at) - (timedelta(seconds=60) if row.has_history else timedelta(0))
            return PollClaim(
                account.id,
                row.claim_generation,
                account.version,
                account.credential_generation,
                since,
                config.poll_interval_seconds,
            )

    def fence(self, claim: PollClaim) -> Callable[[AsyncSession], Awaitable[None]]:
        async def check(db: AsyncSession) -> None:
            row = await db.get(GitHubPollRecord, claim.account_id, with_for_update=True)
            if (
                row is None
                or row.claim_owner != self.instance_id
                or row.claim_generation != claim.generation
                or row.claim_expires_at is None
                or assume_utc(row.claim_expires_at) <= self.clock()
            ):
                raise ConnectivityHttpError("poll_lease_lost")

        return check

    async def run_once(self) -> bool:
        claim = await self.claim()
        if claim is None:
            return False
        cursor: datetime | None = None
        error_code: str | None = None
        delay = claim.interval
        try:
            with anyio.fail_after(90):
                snapshot, adapter, credentials = await self.ingress.load_runtime(claim.account_id)
                if (snapshot.version, snapshot.credential_generation) != (
                    claim.account_version,
                    claim.credential_generation,
                ):
                    raise ConnectivityHttpError("account_changed")
                config = GitHubPollingConfig.model_validate(snapshot.provider_config)
                secret = GitHubPollingCredentials.model_validate(credentials).personal_access_token.get_secret_value()
                rest = GitHubREST(self.http, self.endpoints, config.api_origin)
                user = await rest.object("/user", token=secret)
                if user.get("id") != config.user_id:
                    raise ConnectivityHttpError("bot_identity_mismatch")
                notifications, next_cursor, minimum = await scan_notifications(
                    rest, secret, claim.since, before=self.clock()
                )
                delay = max(delay, minimum)
                for notification in notifications:
                    event = await notification_event(
                        rest, secret, notification, user_id=config.user_id, now=self.clock()
                    )
                    if event is not None:
                        await self.ingress.admit_authenticated(
                            snapshot=snapshot,
                            adapter=adapter,
                            event=event,
                            request_digest=hashlib.sha256(
                                canonical_json(notification.digest_content()).encode()
                            ).hexdigest(),
                            fence=self.fence(claim),
                        )
                cursor = next_cursor
        except Exception as error:
            error_code = error.code if isinstance(error, ConnectivityHttpError) else "poll_failed"
            if isinstance(error, ConnectivityHttpError) and error.retry_after_seconds is not None:
                delay = max(delay, error.retry_after_seconds)
            logger.warning("github_poll_failed", extra={"account_id": claim.account_id, "reason_code": error_code})
        async with transaction(self.sessions) as db:
            # Lock in the same order as admission; changed credentials/configuration cannot advance a cursor.
            account = await db.get(AccountRecord, claim.account_id, with_for_update=True)
            await self.fence(claim)(db)
            row = await db.get(GitHubPollRecord, claim.account_id)
            assert row is not None
            if account is None or (account.version, account.credential_generation) != (
                claim.account_version,
                claim.credential_generation,
            ):
                cursor, error_code = None, "account_changed"
            if cursor is not None:
                row.cursor_at = max(assume_utc(row.cursor_at), cursor) if row.has_history else cursor
                row.has_history = True
            row.available_at = self.clock() + timedelta(seconds=delay)
            row.checked_at, row.error_code = self.clock(), error_code
            row.claim_owner, row.claim_expires_at = None, None
        return True
