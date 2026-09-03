from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta

import pytest
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_models import IngressAdmissionRecord
from a13n_service.connectivity.ingress.domain import CreateIngressRequest
from a13n_service.connectivity.ingress.provider import (
    ExternalRef,
    InboundEvent,
    ProviderCompleteDecision,
    ProviderEventDecision,
    ProviderIrrelevantEventRouting,
    ProviderRequest,
    ProviderRequestError,
    ProviderRequiresBindingRouting,
)
from a13n_service.connectivity.ingress.providers.lark import LarkIngressAdapter
from a13n_service.connectivity.ingress.providers.registry import built_in_ingress_adapter_registry
from a13n_service.connectivity.ingress.raw_objects import IngressRawObjectStore
from a13n_service.connectivity.ingress.service import IngressService
from a13n_service.secrets import InternalSecretService
from a13n_service.storage.object_store import LocalObjectStore
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import AGENT_ID, NOW, SERVICE_ACCOUNT_ID, WORKSPACE_ID, actor

_APP_SECRET = "app-secret"
_ENCRYPT_KEY = "encrypt-key"
_VERIFICATION_TOKEN = "verification-token"


def _config(*, origin: str = "https://open.feishu.cn") -> dict[str, object]:
    return {
        "brand": "feishu",
        "open_api_origin": origin,
        "app_id": "cli_app",
        "tenant_key": "tenant-1",
        "bot_open_id": "ou_bot",
        "events_transport": "http",
    }


def _payload(
    *,
    event_id: str = "event-1",
    chat_type: str = "group",
    sender_open_id: str = "ou_human",
    message_type: str = "text",
    content: object | None = None,
    mentions: list[dict[str, object]] | None = None,
    thread_id: str | None = "omt_thread",
) -> dict[str, object]:
    message: dict[str, object] = {
        "message_id": "om_message",
        "root_id": "om_root",
        "parent_id": "om_parent",
        "create_time": "1788422400000",
        "chat_id": "oc_chat",
        "chat_type": chat_type,
        "message_type": message_type,
        "content": json.dumps(content or {"text": "@_user_1 hello 世界"}, ensure_ascii=False),
        "mentions": mentions
        if mentions is not None
        else [{"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "Bot"}],
    }
    if thread_id is not None:
        message["thread_id"] = thread_id
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "create_time": "1788422400",
            "token": _VERIFICATION_TOKEN,
            "app_id": "cli_app",
            "tenant_key": "tenant-1",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": sender_open_id},
                "sender_type": "user",
                "tenant_key": "tenant-1",
            },
            "message": message,
        },
    }


def _encrypted_request(
    payload: dict[str, object],
    *,
    timestamp: int | None = None,
    signature: str | None = None,
    corrupt_padding: bool = False,
) -> ProviderRequest:
    plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    iv = b"0123456789abcdef"
    encryptor = Cipher(
        algorithms.AES(hashlib.sha256(_ENCRYPT_KEY.encode()).digest()),
        modes.CBC(iv),
    ).encryptor()
    ciphertext = iv + encryptor.update(padded) + encryptor.finalize()
    if corrupt_padding:
        ciphertext = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    body = json.dumps({"encrypt": base64.b64encode(ciphertext).decode()}, sort_keys=True).encode()
    timestamp_text = str(timestamp if timestamp is not None else int(NOW.timestamp()))
    nonce = "nonce-1"
    expected = hashlib.sha256(timestamp_text.encode() + nonce.encode() + _ENCRYPT_KEY.encode() + body).hexdigest()
    return ProviderRequest(
        headers={
            "x-lark-request-timestamp": timestamp_text,
            "x-lark-request-nonce": nonce,
            "x-lark-signature": signature or expected,
        },
        body=body,
        content_type="application/json",
    )


def _credentials() -> dict[str, str]:
    return {
        "app_secret": _APP_SECRET,
        "encrypt_key": _ENCRYPT_KEY,
        "verification_token": _VERIFICATION_TOKEN,
    }


@pytest.mark.anyio
async def test_lark_encrypted_unicode_event_is_verified_and_normalized() -> None:
    decision = await LarkIngressAdapter().authenticate_and_normalize(
        _encrypted_request(_payload()),
        ingress_id="ing_test",
        ingress_config=_config(),
        credentials=_credentials(),
        received_at=NOW,
    )

    assert isinstance(decision, ProviderEventDecision)
    assert decision.event.text == "hello 世界"
    assert decision.event.actor == {"open_id": "ou_human", "sender_type": "user"}
    assert decision.event.refs["discussion"].id == "tenant-1:omt_thread"
    assert decision.event.context["mentions"] == [{"key": "@_user_1", "open_id": "ou_bot"}]
    assert "hello 世界" not in repr(decision.event)


@pytest.mark.anyio
async def test_lark_rejects_wrong_stale_signature_and_invalid_padding() -> None:
    requests = (
        _encrypted_request(_payload(), signature="0" * 64),
        _encrypted_request(_payload(), timestamp=int((NOW - timedelta(minutes=6)).timestamp())),
        _encrypted_request(_payload(), corrupt_padding=True),
    )
    for request in requests:
        with pytest.raises(ProviderRequestError) as rejected:
            await LarkIngressAdapter().authenticate_and_normalize(
                request,
                ingress_id="ing_test",
                ingress_config=_config(),
                credentials=_credentials(),
                received_at=NOW,
            )
        assert rejected.value.response.status_code in {401}


@pytest.mark.anyio
async def test_lark_challenge_and_self_message_create_no_event() -> None:
    challenge_payload = _payload()
    header = challenge_payload["header"]
    assert isinstance(header, dict)
    header["event_type"] = "url_verification"
    challenge_payload["challenge"] = "challenge-value"
    adapter = LarkIngressAdapter()

    challenge = await adapter.authenticate_and_normalize(
        _encrypted_request(challenge_payload),
        ingress_id="ing_test",
        ingress_config=_config(),
        credentials=_credentials(),
        received_at=NOW,
    )
    own_message = await adapter.authenticate_and_normalize(
        _encrypted_request(_payload(sender_open_id="ou_bot")),
        ingress_id="ing_test",
        ingress_config=_config(),
        credentials=_credentials(),
        received_at=NOW,
    )

    assert isinstance(challenge, ProviderCompleteDecision)
    assert json.loads(challenge.response.body) == {"challenge": "challenge-value"}
    assert isinstance(own_message, ProviderCompleteDecision)
    assert json.loads(own_message.response.body) == {"codemsg": "success"}


@pytest.mark.anyio
async def test_lark_post_and_attachment_have_bounded_safe_projections() -> None:
    adapter = LarkIngressAdapter()
    post = await adapter.authenticate_and_normalize(
        _encrypted_request(
            _payload(
                message_type="post",
                content={
                    "en_us": {
                        "title": "Title",
                        "content": [
                            [
                                {"tag": "at", "user_id": "ou_bot", "user_name": "Bot"},
                                {"tag": "text", "text": " hello"},
                            ]
                        ],
                    }
                },
            )
        ),
        ingress_id="ing_test",
        ingress_config=_config(),
        credentials=_credentials(),
        received_at=NOW,
    )
    attachment = await adapter.authenticate_and_normalize(
        _encrypted_request(
            _payload(
                event_id="event-2",
                message_type="file",
                content={"file_key": "file-1", "file_name": "report.pdf", "unsafe": "drop"},
            )
        ),
        ingress_id="ing_test",
        ingress_config=_config(),
        credentials=_credentials(),
        received_at=NOW,
    )

    assert isinstance(post, ProviderEventDecision)
    assert post.event.text == "Title hello"
    assert isinstance(attachment, ProviderEventDecision)
    assert attachment.event.data == {"attachment": {"type": "file", "file_key": "file-1", "file_name": "report.pdf"}}


def test_lark_config_requires_matching_or_operator_allowed_origin() -> None:
    adapter = LarkIngressAdapter(allowed_provider_origins=("https://lark.internal.example",))

    assert (
        adapter.validate_config(
            _config(origin="https://lark.internal.example/"),
            config_version="lark_http_v1",
        )["open_api_origin"]
        == "https://lark.internal.example"
    )
    with pytest.raises(ValueError, match="operator-allowed"):
        LarkIngressAdapter().validate_config(
            _config(origin="https://attacker.example"),
            config_version="lark_http_v1",
        )
    with pytest.raises(ValueError, match="exact HTTPS origin"):
        built_in_ingress_adapter_registry(
            allowed_provider_origins=("http://lark.internal.example",),
        )


def test_lark_route_overlap_and_interaction_classification() -> None:
    adapter = LarkIngressAdapter()
    left, discussion = adapter.validate_route(
        match={"event_kinds": ["message"], "chat_types": ["group"], "chat_ids": ["oc_1"]},
        provider_policy={"interaction_mode": "discussion", "reply_mode": "thread"},
        ingress_config=_config(),
        config_version="lark_http_v1",
    )
    right, _policy = adapter.validate_route(
        match={"event_kinds": ["message"], "chat_types": ["group"], "chat_ids": ["oc_2"]},
        provider_policy={"interaction_mode": "chat", "reply_mode": "main"},
        ingress_config=_config(),
        config_version="lark_http_v1",
    )
    assert adapter.prove_non_overlap(left, right) is True
    event = _normalized_event()
    assert isinstance(
        adapter.classify(event, discussion, _config(), config_version="lark_http_v1"),
        ProviderRequiresBindingRouting,
    )
    ignored = adapter.classify(
        event,
        {"interaction_mode": "mention", "reply_mode": "thread"},
        _config(),
        config_version="lark_http_v1",
    )
    assert isinstance(ignored, ProviderIrrelevantEventRouting)


def _normalized_event() -> InboundEvent:
    return InboundEvent(
        identity_kind="lark.event",
        external_event_id="event-1",
        normalization_version="lark_message_v1",
        type="lark.message",
        received_at=NOW,
        text="hello",
        context={
            "chat_id": "oc_1",
            "chat_type": "group",
            "message_id": "om_1",
            "discussion_id": "omt_1",
            "event_kind": "message",
            "mentioned": False,
        },
        refs={
            "conversation": ExternalRef(kind="lark.conversation", id="tenant-1:oc_1"),
            "discussion": ExternalRef(kind="lark.discussion", id="tenant-1:omt_1"),
        },
        data={},
        ordering_key="1:event-1",
    )


@pytest.mark.anyio
async def test_lark_real_protocol_fixture_is_durable_before_ack(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_secrets: InternalSecretService,
    connectivity_objects: LocalObjectStore,
) -> None:
    registry = built_in_ingress_adapter_registry()
    ingress_service = IngressService(connectivity_sessions, registry, connectivity_secrets, clock=lambda: NOW)
    ingress = await ingress_service.create_ingress(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="lark-ingress",
        request=CreateIngressRequest(
            name="Lark",
            provider_key="lark",
            provider_config_version="lark_http_v1",
            provider_config=_config(),
            execution_service_account_id=SERVICE_ACCOUNT_ID,
            agents=(AGENT_ID,),
            default_agent_id=AGENT_ID,
            credentials=_credentials(),
        ),
    )
    event_service = IngressEventService(
        connectivity_sessions,
        registry,
        connectivity_secrets,
        IngressRawObjectStore(connectivity_objects),
        request_max_bytes=8 * 1024 * 1024,
        raw_retention_seconds=0,
        workspace_pending_max_count=100,
        workspace_pending_max_bytes=1024 * 1024,
        ingress_pending_max_count=100,
        ingress_pending_max_bytes=1024 * 1024,
        batch_max_bytes=1024 * 1024,
        dedup_horizon_seconds=7 * 24 * 60 * 60,
        clock=lambda: NOW,
    )

    response = await event_service.receive(
        ingress_id=ingress.id,
        request=_encrypted_request(_payload()),
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"codemsg": "success"}
    async with connectivity_sessions() as session:
        admission = await session.scalar(select(IngressAdmissionRecord))
    assert admission is not None
    assert admission.provider_key == "lark"
    assert admission.provider_context_json["chat_id"] == "oc_chat"
