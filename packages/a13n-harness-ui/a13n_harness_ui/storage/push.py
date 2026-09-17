"""Persistent opt-in subscriptions; delivery itself is deliberately best effort."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import delete, select

from a13n_harness_ui.push_models import PushKeys, PushSubscriptionInput

from .database import DatabaseSessions, short_session, transaction
from .models import WebPushKeyRecord, WebPushSubscriptionRecord


class PushRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def private_key(self) -> str:
        async with transaction(self._sessions) as session:
            record = await session.get(WebPushKeyRecord, 1)
            if record is None:
                key = ec.generate_private_key(ec.SECP256R1())
                pem = key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ).decode()
                record = WebPushKeyRecord(singleton_id=1, private_key=pem)
                session.add(record)
            return record.private_key

    async def save(self, subscription: PushSubscriptionInput) -> str:
        async with transaction(self._sessions) as session:
            record = await session.get(WebPushSubscriptionRecord, subscription.subscription_id)
            previous = json.loads(record.thread_ids_json) if record is not None else []
            threads = [item for item in previous if item not in subscription.thread_ids]
            threads = [*threads, *subscription.thread_ids][-256:]
            if record is None:
                record = WebPushSubscriptionRecord(subscription_id=subscription.subscription_id)
                session.add(record)
            record.endpoint = subscription.endpoint
            record.p256dh = subscription.keys.p256dh
            record.auth = subscription.keys.auth
            record.origin = subscription.origin
            record.thread_ids_json = json.dumps(threads)
            record.updated_at = datetime.now(UTC)
        return subscription.subscription_id

    async def get(self, subscription_id: str) -> PushSubscriptionInput | None:
        async with short_session(self._sessions) as session:
            record = await session.get(WebPushSubscriptionRecord, subscription_id)
            return None if record is None else _subscription(record)

    async def recipients(self, thread_id: str) -> tuple[PushSubscriptionInput, ...]:
        cutoff = datetime.now(UTC) - timedelta(days=90)
        async with transaction(self._sessions) as session:
            await session.execute(
                delete(WebPushSubscriptionRecord).where(WebPushSubscriptionRecord.updated_at < cutoff)
            )
            records = (await session.scalars(select(WebPushSubscriptionRecord))).all()
            return tuple(_subscription(record) for record in records if thread_id in json.loads(record.thread_ids_json))

    async def remove(self, subscription_id: str, *, expected_auth: str | None = None) -> None:
        async with transaction(self._sessions) as session:
            statement = delete(WebPushSubscriptionRecord).where(
                WebPushSubscriptionRecord.subscription_id == subscription_id
            )
            if expected_auth is not None:
                statement = statement.where(WebPushSubscriptionRecord.auth == expected_auth)
            await session.execute(statement)


def _subscription(record: WebPushSubscriptionRecord) -> PushSubscriptionInput:
    return PushSubscriptionInput(
        endpoint=record.endpoint,
        keys=PushKeys(p256dh=record.p256dh, auth=record.auth),
        origin=record.origin,
        thread_ids=tuple(json.loads(record.thread_ids_json)),
    )
