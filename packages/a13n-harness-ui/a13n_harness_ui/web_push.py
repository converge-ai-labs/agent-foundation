"""App-owned Web Push transport, independent of browser connections and Runs."""

from __future__ import annotations

import base64
import json
import time
from hashlib import sha256
from urllib.parse import quote, urlsplit

import http_ece
import httpx2
from a13n_logging import get_logger
from anyio import CapacityLimiter, WouldBlock, create_memory_object_stream, create_task_group, sleep
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.live import RootOperationNotice
from a13n_harness_ui.push_models import PushConfiguration, PushSubscriptionInput, PushTestResult, decode_key
from a13n_harness_ui.storage import LocalStore
from a13n_harness_ui.storage.push import PushRepository

_LOG = get_logger("a13n_harness_ui.web_push")


class WebPush:
    def __init__(self, store: LocalStore, client: httpx2.AsyncClient) -> None:
        self.repository = PushRepository(store.database.sessions)
        self._store = store
        self._client = client
        self._send, self._receive = create_memory_object_stream[tuple[str, RootOperationNotice]](128)
        self._limiter = CapacityLimiter(4)

    async def _key(self) -> ec.EllipticCurvePrivateKey:
        key = serialization.load_pem_private_key((await self.repository.private_key()).encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise HarnessUiError("Stored Web Push key is invalid.", code="push_key_invalid")
        return key

    async def configuration(self) -> PushConfiguration:
        public_key = (
            (await self._key())
            .public_key()
            .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        )
        return PushConfiguration(public_key=base64.urlsafe_b64encode(public_key).rstrip(b"=").decode())

    def enqueue(self, thread_id: str, notice: RootOperationNotice) -> None:
        # A full queue loses a reminder, never blocks or fails a Run settlement.
        try:
            self._send.send_nowait((thread_id, notice))
        except WouldBlock:
            _LOG.warning("Web Push queue full; notification omitted")

    async def run(self) -> None:
        async with self._send, self._receive:
            async for thread_id, notice in self._receive:
                try:
                    thread = await self._store.threads.get(thread_id)
                    if thread is None or thread.archived:
                        continue
                    title = {
                        "completed": "Task completed",
                        "failed": "Task failed",
                        "suspended": "Your input is needed",
                    }[notice.status]
                    payload = {
                        "title": f"{title} · {(thread.title or 'Conversation')[:80]}",
                        "body": notice.brief[:180],
                        "tag": f"a13n-harness-ui.{notice.receipt_id}",
                        "path": f"/threads/{quote(thread_id, safe='')}",
                    }
                    async with create_task_group() as deliveries:
                        for subscription in await self.repository.recipients():
                            deliveries.start_soon(self._deliver, subscription, payload)
                except Exception:
                    # Provider failures are diagnostics, not execution failures.
                    # Never log exception strings containing endpoints or payloads.
                    _LOG.warning("Web Push notification could not be prepared")

    async def test(self, subscription_id: str) -> PushTestResult:
        subscription = await self.repository.get(subscription_id)
        if subscription is None:
            raise HarnessUiError("Enable background notifications again.", code="push_subscription_missing")
        accepted = await self._deliver(
            subscription,
            {
                "title": "Harness UI test notification",
                "body": "Background notifications can reach this device even when WebUI is closed.",
                "tag": "a13n-harness-ui.test",
                "path": "/settings/notifications",
            },
        )
        return PushTestResult(accepted=accepted)

    async def _deliver(self, subscription: PushSubscriptionInput, payload: dict[str, str]) -> bool:
        async with self._limiter:
            # Do not deliver a queued snapshot after its subscription was removed.
            current = await self.repository.get(subscription.subscription_id)
            if current is None or current.keys != subscription.keys:
                return False
            try:
                key = await self._key()
                endpoint = urlsplit(subscription.endpoint)
                # The library's legacy subject regex rejects valid HTTPS URIs
                # with ports. Our input model validates the complete origin.
                subject = (
                    subscription.origin if subscription.origin.startswith("https://") else "mailto:webpush@localhost"
                )
                headers = Vapid(private_key=key, conf={"no-strict": True}).sign(
                    {
                        "aud": f"{endpoint.scheme}://{endpoint.netloc}",
                        "sub": subject,
                        "exp": int(time.time()) + 3600,
                    }
                )
                content = http_ece.encrypt(
                    json.dumps(payload, ensure_ascii=False).encode(),
                    private_key=ec.generate_private_key(ec.SECP256R1()),
                    dh=decode_key(subscription.keys.p256dh),
                    auth_secret=decode_key(subscription.keys.auth),
                    version="aes128gcm",
                )
                headers.update(
                    {
                        "Content-Encoding": "aes128gcm",
                        "Content-Type": "application/octet-stream",
                        "TTL": "3600",
                        "Urgency": "high",
                        "Topic": sha256(payload["tag"].encode()).hexdigest()[:32],
                    }
                )
                for attempt in range(2):
                    try:
                        response = await self._client.post(
                            subscription.endpoint, content=content, headers=headers, timeout=10, follow_redirects=False
                        )
                    except httpx2.TransportError:
                        if attempt == 0:
                            await sleep(1)
                            continue
                        raise
                    if response.status_code in {404, 410}:
                        await self.repository.remove(subscription.subscription_id, expected_auth=subscription.keys.auth)
                        return False
                    if 200 <= response.status_code < 300:
                        return True
                    if attempt == 0 and (response.status_code == 429 or response.status_code >= 500):
                        await sleep(1)
                        continue
                    _LOG.warning("Web Push service rejected notification", extra={"status_code": response.status_code})
                    return False
            except Exception:
                _LOG.warning("Web Push delivery failed")
            return False
