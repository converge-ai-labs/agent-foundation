"""Webhook delivery: staging a transition's deliveries in the outbox, and the sender that settles each claim.

Receivers verify `X-A13n-Webhook-Signature: v1=<hex HMAC-SHA256 of "<timestamp>.<delivery id>.<body>">` with
the subscription's signing secret, and deduplicate by `X-A13n-Delivery-Id`: a retry after an uncertain result
can deliver twice, and deliveries are not ordered.
"""

import hashlib
import hmac
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import anyio
import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_logging import exception_details, get_logger
from pydantic import JsonValue
from sqlalchemy import Integer, String, column, or_, select, true, values
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from a13n_service.infra.crypto import Envelope, KeyRing
from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbound import open_http
from a13n_service.infra.outbox import Claim, OutboxRow, Undelivered, prepare, secret_location, settle
from a13n_service.resources.subscriptions.schemas import SECRET_UNAVAILABLE, LifecycleKind, WebhookTarget
from a13n_service.resources.subscriptions.service import signing_location
from a13n_service.resources.subscriptions.tables import SubscriptionRow

logger = get_logger(__name__)

DELIVERY_HEADER = "X-A13n-Delivery-Id"
TIMESTAMP_HEADER = "X-A13n-Webhook-Timestamp"
SIGNATURE_HEADER = "X-A13n-Webhook-Signature"


@dataclass(frozen=True, slots=True)
class RunFacts:
    """The identities of the run a lifecycle transition belongs to, as subscription filters select them."""

    workspace_id: str
    agent_id: str
    session_id: str
    thread_id: str


def webhook_target(keys: KeyRing, subscription: SubscriptionRow, delivery_id: str) -> dict[str, JsonValue]:
    """The outbox target of one delivery; the secret is re-encrypted for the outbox row `delivery_id`."""
    secret = keys.reveal(Envelope.model_validate(subscription.signing_secret), signing_location(subscription))
    protected = keys.protect(secret, secret_location(subscription.organization_id, delivery_id, "target"))
    return WebhookTarget(url=subscription.url, signing_secret=protected).model_dump(mode="json")


async def stage_webhooks(
    session: AsyncSession,
    keys: KeyRing,
    run: RunFacts,
    kinds: Sequence[LifecycleKind],
    payload: Mapping[str, JsonValue],
    *,
    limit: int,
) -> None:
    """Stage one delivery of `payload` per matching subscription and lifecycle kind in the caller's transaction.

    The subscription read here is the selection point; later edits affect only later transitions. `limit` is the
    workspace subscription cap, so it only drops subscriptions left over after the cap was lowered, and says so.
    Each delivery adds its own `id` and `type` to `payload`. A subscription whose signing secret cannot be
    decrypted gets dead deliveries instead, so it never fails the transition.
    """
    session.add_all(await prepare_webhooks(session, keys, [Transition(run, kinds, payload)], limit=limit))


@dataclass(frozen=True, slots=True)
class Transition:
    run: RunFacts
    kinds: Sequence[LifecycleKind]
    payload: Mapping[str, JsonValue]


async def prepare_webhooks(
    session: AsyncSession, keys: KeyRing, transitions: Sequence[Transition], *, limit: int
) -> list[OutboxRow]:
    """Select subscriptions once for the batch, applying filters and the cap separately to each run."""
    if not transitions:
        return []
    facts = values(
        column("ordinal", Integer),
        column("workspace_id", String),
        column("agent_id", String),
        column("session_id", String),
        column("thread_id", String),
        column("kinds", ARRAY(String)),
        name="transitions",
    ).data(
        [
            (i, t.run.workspace_id, t.run.agent_id, t.run.session_id, t.run.thread_id, list(t.kinds))
            for i, t in enumerate(transitions)
        ]
    )
    matches = (
        select(SubscriptionRow)
        .where(
            SubscriptionRow.workspace_id == facts.c.workspace_id,
            SubscriptionRow.enabled,
            SubscriptionRow.kinds.has_any(facts.c.kinds),
            *(
                or_(~SubscriptionRow.filter.has_key(name), SubscriptionRow.filter[name].astext == facts.c[name])
                for name in ("agent_id", "session_id", "thread_id")
            ),
        )
        .order_by(SubscriptionRow.id)
        .limit(limit + 1)
        .lateral()
    )
    subscription = aliased(SubscriptionRow, matches)
    selected = await session.execute(
        select(facts.c.ordinal, subscription)
        .select_from(facts.join(matches, true()))
        .order_by(facts.c.ordinal, subscription.id)
    )
    counts = [0] * len(transitions)
    rows: list[OutboxRow] = []
    for ordinal, matched in selected:
        transition = transitions[ordinal]
        counts[ordinal] += 1
        if counts[ordinal] > limit:
            logger.warning(
                "Matching subscriptions exceed the workspace cap",
                extra={"workspace_id": transition.run.workspace_id, "limit": limit},
            )
            continue
        for kind in transition.kinds:
            if kind in matched.kinds:
                rows.append(_prepare(keys, matched, {**transition.payload, "type": kind}))
    return rows


def _prepare(keys: KeyRing, subscription: SubscriptionRow, payload: dict[str, JsonValue]) -> OutboxRow:
    # The delivery ID is the outbox row ID, the dedupe identity receivers see, and the secret's AAD.
    delivery_id = new_object_id("obx")
    dead = None
    try:
        target = webhook_target(keys, subscription, delivery_id)
    except (ServiceError, ValueError) as error:
        logger.error(
            "Subscription signing secret cannot be decrypted",
            extra={"subscription_id": subscription.id, "exception_details": exception_details(error)},
        )
        target, dead = {"url": subscription.url}, SECRET_UNAVAILABLE
    return prepare(
        organization_id=subscription.organization_id,
        workspace_id=subscription.workspace_id,
        kind="webhook",
        row_id=delivery_id,
        subscription_id=subscription.id,
        target=target,
        payload={**payload, "id": delivery_id},
        dead=dead,
    )


def signature(secret: bytes, timestamp: str, delivery_id: str, body: bytes) -> str:
    return "v1=" + hmac.new(secret, f"{timestamp}.{delivery_id}.".encode() + body, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class WebhookSender:
    """The outbox handler for `webhook` rows: one signed POST per claim, then settlement under that claim."""

    storage: Storage
    keys: KeyRing
    policy: EndpointPolicy
    timeout: float  # bounds the whole POST; it stays below the outbox claim lease

    async def __call__(self, claim: Claim) -> None:
        error = await self._post(claim)
        if error is not None:
            raise Undelivered(error)
        async with transaction(self.storage) as session:
            await settle(session, claim, "delivered")

    async def _post(self, claim: Claim) -> str | None:
        """None when the receiver accepted the delivery, otherwise a short reason without secrets.

        The status alone decides; the response body is never read. Redirects are not followed, and the policy
        validates the declared destination URL on every attempt.
        """
        try:
            target = WebhookTarget.model_validate(claim.target)
            secret = self.keys.reveal(target.signing_secret, secret_location(claim.organization_id, claim.id, "target"))
            body = json.dumps(claim.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            timestamp = str(int(time.time()))
            headers = {
                "content-type": "application/json",
                DELIVERY_HEADER: claim.id,
                TIMESTAMP_HEADER: timestamp,
                SIGNATURE_HEADER: signature(secret, timestamp, claim.id, body),
            }
            with anyio.fail_after(self.timeout):
                async with (
                    open_http(self.policy, timeout=self.timeout, max_bytes=0) as client,
                    client.stream("POST", target.url, content=body, headers=headers) as response,
                ):
                    status = response.status_code
        except (httpx2.HTTPError, ServiceError, ValueError, TimeoutError) as error:
            return type(error).__name__
        return None if 200 <= status < 300 else f"HTTP {status}"
