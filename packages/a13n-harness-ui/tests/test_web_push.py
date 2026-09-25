from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import http_ece
import httpx2
import pytest
from a13n_harness_ui import web_push
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.live import RootOperationNotice
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.push_models import PushKeys, PushSubscriptionInput, decode_key
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage.database import open_database, transaction
from a13n_harness_ui.storage.models import WebPushSubscriptionRecord
from a13n_harness_ui.storage.push import PushRepository
from a13n_harness_ui.web_push import WebPush
from anyio import Event, create_task_group, fail_after
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from pydantic import ValidationError
from pydantic_ai.models.function import FunctionModel

from .test_app import _write_configuration


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def subscription(*, endpoint: str = "https://fcm.googleapis.com/fcm/send/test"):
    key = ec.generate_private_key(ec.SECP256R1())
    return key, PushSubscriptionInput(
        endpoint=endpoint,
        keys=PushKeys(
            p256dh=encoded(
                key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            ),
            auth=encoded(b"test-auth-secret"),
        ),
        origin="https://anui.example.test:8090",
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/send/test",
        "https://127.0.0.1/test",
        "https://fcm.googleapis.com.evil.test/push",
        "https://user:password@fcm.googleapis.com/push",
        "https://fcm.googleapis.com:123/push",
        "https://fcm.googleapis.com/push#fragment",
    ],
)
def test_subscription_rejects_non_browser_destinations(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        subscription(endpoint=endpoint)


def test_subscription_validates_keys_and_origin() -> None:
    _, valid = subscription()
    for keys in ({"p256dh": "bad", "auth": valid.keys.auth}, {"p256dh": valid.keys.p256dh, "auth": "bad"}):
        with pytest.raises(ValidationError):
            PushKeys.model_validate(keys)
    for origin in (
        "http://remote.example",
        "https://example.test/path",
        "https://user@example.test",
        "https://example.test?q=1",
    ):
        with pytest.raises(ValidationError):
            PushSubscriptionInput.model_validate({**valid.model_dump(), "origin": origin})


@pytest.mark.anyio
async def test_repository_persists_key_and_expires_unmaintained_subscriptions(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    settings = StorageSettings(data_root=tmp_path)
    _, device = subscription()
    async with open_database(path, settings) as first, open_database(path, settings) as second:
        one, two = PushRepository(first.sessions), PushRepository(second.sessions)
        keys: list[str] = []

        async def save_key(repo: PushRepository) -> None:
            keys.append(await repo.private_key())

        async with create_task_group() as tasks:
            tasks.start_soon(save_key, one)
            tasks.start_soon(save_key, two)
            tasks.start_soon(one.save, device)
            tasks.start_soon(two.save, device)
        assert len(set(keys)) == 1
        saved = await one.get(device.subscription_id)
        assert saved == device
        assert await two.recipients() == ()
        assert await one.mark_active(device.subscription_id)
        assert await two.recipients() == (device,)
        await one.remove(device.subscription_id, expected_auth="old-auth")
        assert await one.get(device.subscription_id) is not None
    async with open_database(path, settings) as reopened:
        repository = PushRepository(reopened.sessions)
        assert await repository.private_key() == keys[0]
        assert await repository.get(device.subscription_id) is not None
        async with transaction(reopened.sessions) as session:
            record = await session.get(WebPushSubscriptionRecord, device.subscription_id)
            assert record is not None
            record.updated_at = datetime.now(UTC) - timedelta(days=91)
        assert await repository.recipients() == ()
        assert await repository.get(device.subscription_id) is None


@pytest.mark.anyio
async def test_push_encrypts_preview_and_signs_valid_vapid_with_port(tmp_path: Path) -> None:
    receiver, device = subscription()
    requests: list[httpx2.Request] = []

    def accept(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(201)

    async with open_harness_ui_app(HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))) as app:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(accept)) as client:
            push = WebPush(app._store, client)
            await push.repository.save(device)
            public = await push.configuration()
            assert (await push.test(device.subscription_id)).accepted
            request = requests[0]
            payload = json.loads(
                http_ece.decrypt(
                    request.content, private_key=receiver, auth_secret=decode_key(device.keys.auth), version="aes128gcm"
                )
            )
            assert payload["path"] == "/settings/notifications"
            assert payload["tag"] == "a13n-harness-ui.test"
            assert b"Harness UI" not in request.content
            assert request.headers["Content-Encoding"] == "aes128gcm"
            assert request.headers["TTL"] == "3600"
            assert len(request.headers["Topic"]) == 32
            token, public_header = request.headers["Authorization"].removeprefix("vapid t=").split(",k=")
            assert public_header == public.public_key
            header, claims, signature = token.split(".")
            claim = json.loads(decode_key(claims))
            assert claim["aud"] == "https://fcm.googleapis.com"
            assert claim["sub"] == device.origin
            assert datetime.now(UTC).timestamp() < claim["exp"] < datetime.now(UTC).timestamp() + 3601
            sig = decode_key(signature)
            der = utils.encode_dss_signature(int.from_bytes(sig[:32]), int.from_bytes(sig[32:]))
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decode_key(public.public_key)).verify(
                der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,attempts,removed",
    [
        (201, 1, False),
        (410, 1, True),
        (404, 1, True),
        (403, 1, False),
        (429, 2, False),
        (503, 2, False),
        (307, 1, False),
    ],
)
async def test_provider_failures_are_bounded_and_expired_subscriptions_removed(
    tmp_path: Path, status: int, attempts: int, removed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, device = subscription()
    requests = []
    backoffs = []

    async def backoff(seconds):
        backoffs.append(seconds)

    monkeypatch.setattr(web_push, "sleep", backoff)

    def respond(request):
        requests.append(request)
        return httpx2.Response(status, headers={"Location": "http://127.0.0.1/private"})

    async with open_harness_ui_app(HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))) as app:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            push = WebPush(app._store, client)
            await push.repository.save(device)
            assert (await push.test(device.subscription_id)).accepted is (status == 201)
            assert len(requests) == attempts
            assert backoffs == [1] * (attempts - 1)
            assert (await push.repository.get(device.subscription_id) is None) is removed
            await push.repository.remove(device.subscription_id)
            assert not await push._deliver(device, {"tag": "removed"})
            assert len(requests) == attempts


@pytest.mark.anyio
async def test_webui_run_push_does_not_need_any_browser_observer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = _write_configuration(tmp_path)
    receiver, template = subscription()
    delivered = Event()
    payloads = []

    async def stream(messages, info):
        yield "Fixed **background delivery** without an open browser."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    async def post(self, url, **kwargs):
        # Mock the external boundary only; App, settlement, worker and crypto are real.
        payloads.append(
            json.loads(
                http_ece.decrypt(kwargs["content"], private_key=receiver, auth_secret=decode_key(template.keys.auth))
            )
        )
        delivered.set()
        return httpx2.Response(201)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    monkeypatch.setattr(httpx2.AsyncClient, "post", post)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=configuration, host_mode="webui") as app:
        thread = await app.create_thread(title="Background work")
        saved = await app.subscribe_push(template)
        assert saved.subscription_id == template.subscription_id
        await app.record_push_activity(saved.subscription_id)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Fix delivery")
        result = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        assert result.status == "completed"
        with fail_after(5):
            await delivered.wait()
        assert payloads == [
            {
                "title": "Task completed · Background work",
                "body": "Fixed background delivery without an open browser.",
                "tag": f"a13n-harness-ui.{receipt.receipt_id}",
                "path": f"/threads/{thread.thread_id}",
            }
        ]
        await app.unsubscribe_push(saved.subscription_id)


def test_queue_capacity_does_not_block_settlement(tmp_path: Path) -> None:
    # Construct the queue without opening a network client or database.
    from types import SimpleNamespace
    from typing import Any, cast

    push = WebPush(cast(Any, SimpleNamespace(database=SimpleNamespace(sessions=None))), cast(Any, None))
    notice = RootOperationNotice(receipt_id="receipt-one", status="completed", brief="Done")
    for _ in range(200):
        push.enqueue("thread-one", notice)
    assert push._send.statistics().current_buffer_used == 128


@pytest.mark.anyio
async def test_activity_cutoff_is_strict_server_time_and_reconciliation_is_not_activity(tmp_path, monkeypatch):
    import a13n_harness_ui.storage.push as storage_push

    now = datetime(2026, 9, 18, 12, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(storage_push, "datetime", Clock)
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = PushRepository(database.sessions)
        devices = [subscription(endpoint=f"https://fcm.googleapis.com/fcm/send/{i}")[1] for i in range(4)]
        for device in devices:
            await repository.save(device)
        async with transaction(database.sessions) as session:
            for device, age in zip(
                devices[:3], [timedelta(hours=5), timedelta(hours=6), timedelta(hours=7)], strict=True
            ):
                record = await session.get(WebPushSubscriptionRecord, device.subscription_id)
                record.last_active_at = now - age
        assert await repository.recipients() == (devices[0],)
        # Saving keys/origin must not extend the activity window.
        await repository.save(devices[2])
        assert await repository.recipients() == (devices[0],)
        assert await repository.mark_active(devices[2].subscription_id)
        assert {d.subscription_id for d in await repository.recipients()} == {
            devices[0].subscription_id,
            devices[2].subscription_id,
        }
        async with transaction(database.sessions) as session:
            record = await session.get(WebPushSubscriptionRecord, devices[2].subscription_id)
            assert record.last_active_at == record.updated_at == now
        await repository.remove(devices[2].subscription_id)
        assert not await repository.mark_active(devices[2].subscription_id)
        assert await repository.get(devices[2].subscription_id) is None
