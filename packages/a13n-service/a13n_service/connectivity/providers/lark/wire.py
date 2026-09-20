"""Lark/Feishu HTTP v1 authentication, decryption, and event normalization."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from pydantic import JsonValue, TypeAdapter, ValidationError

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.ingress.provider import (
    ExternalRef,
    InboundEvent,
    ProviderActionDecision,
    ProviderCompleteDecision,
    ProviderEventDecision,
    ProviderHttpResponse,
    ProviderRequest,
    ProviderRequestDecision,
    ProviderRequestError,
)

CONTEXT_VERSION = "lark_message_v1"

_SIGNATURE_MAX_AGE_SECONDS = 5 * 60
_MESSAGE_EVENT = "im.message.receive_v1"
_ATTACHMENT_TYPES = frozenset({"image", "file", "audio", "video"})
_ATTACHMENT_FIELDS = frozenset({"image_key", "file_key", "file_name", "duration", "file_size"})
_JSON_OBJECT = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class LarkIdentity:
    app_id: str
    tenant_key: str
    bot_open_id: str


def authenticate_and_normalize(
    request: ProviderRequest,
    *,
    identity: LarkIdentity,
    encrypt_key: str | None,
    verification_token: str,
    received_at: datetime,
) -> ProviderRequestDecision:
    if encrypt_key is not None:
        _verify_signature(request, encrypt_key=encrypt_key, received_at=received_at)
    outer = _parse_object(request.body)
    payload = _decrypt_envelope(outer, encrypt_key=encrypt_key)
    # URL verification uses a top-level envelope, even for schema 2.0 subscriptions.
    if payload.get("type") == "url_verification":
        if not _same(payload.get("token"), verification_token):
            raise _request_error(401, "invalid_verification_token")
        return _challenge_response(payload)
    header = _object(payload.get("header"))
    if payload.get("schema") != "2.0" or header is None:
        raise _request_error(400, "invalid_payload")
    if not _same(header.get("token"), verification_token):
        raise _request_error(401, "invalid_verification_token")
    return normalize_payload(payload, identity=identity, received_at=received_at)


def normalize_payload(payload: JsonObject, *, identity: LarkIdentity, received_at: datetime) -> ProviderRequestDecision:
    header = _object(payload.get("header"))
    if payload.get("schema") != "2.0" or header is None:
        raise _request_error(400, "invalid_payload")
    if header.get("app_id") != identity.app_id or header.get("tenant_key") != identity.tenant_key:
        raise _request_error(404, "ingress_not_found")
    event_type = header.get("event_type")
    if event_type == "url_verification":
        return _challenge_response(payload)
    if event_type == "card.action.trigger":
        return _normalize_action(payload)
    if event_type != _MESSAGE_EVENT:
        return ProviderCompleteDecision(response=lark_acknowledgement())
    event_id = header.get("event_id")
    event = _object(payload.get("event"))
    if not isinstance(event_id, str) or not event_id or event is None:
        raise _request_error(400, "invalid_payload")
    try:
        normalized = _normalize_message(event_id, event, identity, received_at)
    except (ValidationError, ValueError) as error:
        raise _request_error(400, "invalid_payload") from error
    if normalized is None:
        return ProviderCompleteDecision(response=lark_acknowledgement())
    return ProviderEventDecision(event=normalized)


def _challenge_response(payload: JsonObject) -> ProviderCompleteDecision:
    challenge = payload.get("challenge")
    if not isinstance(challenge, str) or not 1 <= len(challenge) <= 4096:
        raise _request_error(400, "invalid_payload")
    return ProviderCompleteDecision(response=_json_response({"challenge": challenge}))


def lark_acknowledgement() -> ProviderHttpResponse:
    return _json_response({"codemsg": "success"})


def _verify_signature(request: ProviderRequest, *, encrypt_key: str, received_at: datetime) -> None:
    timestamp_text = request.headers.get("x-lark-request-timestamp")
    nonce = request.headers.get("x-lark-request-nonce")
    signature = request.headers.get("x-lark-signature")
    if timestamp_text is None or nonce is None or signature is None or len(nonce) > 1024:
        raise _request_error(401, "invalid_signature")
    if not timestamp_text.isascii() or not timestamp_text.isdigit():
        raise _request_error(401, "invalid_signature")
    try:
        timestamp = int(timestamp_text)
    except ValueError as error:
        raise _request_error(401, "invalid_signature") from error
    now = int(received_at.astimezone(UTC).timestamp())
    if abs(now - timestamp) > _SIGNATURE_MAX_AGE_SECONDS:
        raise _request_error(401, "stale_request")
    expected = hashlib.sha256(
        timestamp_text.encode() + nonce.encode() + encrypt_key.encode() + request.body
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise _request_error(401, "invalid_signature")


def _decrypt_envelope(outer: JsonObject, *, encrypt_key: str | None) -> JsonObject:
    encrypted = outer.get("encrypt")
    if encrypted is None:
        return outer
    if not isinstance(encrypted, str) or encrypt_key is None:
        raise _request_error(401, "invalid_encryption")
    try:
        ciphertext = base64.b64decode(encrypted, validate=True)
        if len(ciphertext) < 32 or len(ciphertext) % 16:
            raise ValueError("invalid ciphertext length")
        decryptor = Cipher(
            algorithms.AES(hashlib.sha256(encrypt_key.encode()).digest()),
            modes.CBC(ciphertext[:16]),
        ).decryptor()
        padded = decryptor.update(ciphertext[16:]) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        return _parse_object(plaintext)
    except (ValueError, binascii.Error) as error:
        raise _request_error(401, "invalid_encryption") from error


def _normalize_message(
    event_id: str,
    event: JsonObject,
    identity: LarkIdentity,
    received_at: datetime,
) -> InboundEvent | None:
    sender = _object(event.get("sender"))
    message = _object(event.get("message"))
    sender_id = _object(sender.get("sender_id")) if sender is not None else None
    if sender is None or sender_id is None or message is None:
        raise ValueError("missing sender or message")
    open_id = sender_id.get("open_id")
    sender_type = sender.get("sender_type")
    sender_tenant = sender.get("tenant_key")
    if not isinstance(open_id, str) or not open_id or not isinstance(sender_type, str):
        raise ValueError("invalid sender")
    if sender_tenant != identity.tenant_key:
        raise ValueError("invalid sender tenant")
    if open_id == identity.bot_open_id:
        return None

    message_id = _required_string(message, "message_id")
    chat_id = _required_string(message, "chat_id")
    chat_type = _required_string(message, "chat_type")
    if chat_type not in {"p2p", "group"}:
        return None
    message_type = _required_string(message, "message_type")
    create_time = _integer_string(message.get("create_time"))
    mentions, bot_mention_keys = _mentions(message.get("mentions"), identity.bot_open_id)
    text, data = _message_content(
        message_type,
        message.get("content"),
        bot_mention_keys,
        bot_open_id=identity.bot_open_id,
    )
    if text is None and not data:
        return None
    mentioned = bool(bot_mention_keys)
    if text is not None and not text and not data:
        return None

    thread_id = _optional_string(message.get("thread_id"))
    root_id = _optional_string(message.get("root_id"))
    parent_id = _optional_string(message.get("parent_id"))
    discussion_id = thread_id or root_id or (chat_id if chat_type == "p2p" else message_id)
    return InboundEvent(
        identity_kind="lark.event",
        external_event_id=event_id,
        normalization_version=CONTEXT_VERSION,
        type="lark.message",
        occurred_at=datetime.fromtimestamp(int(create_time) / 1000, tz=UTC),
        received_at=received_at,
        text=text,
        actor={"open_id": open_id, "sender_type": sender_type},
        context={
            "tenant_key": identity.tenant_key,
            "chat_id": chat_id,
            "chat_type": chat_type,
            "message_id": message_id,
            "thread_id": thread_id,
            "root_id": root_id,
            "parent_id": parent_id,
            "message_type": message_type,
            "event_kind": "message",
            "mentioned": mentioned,
            "mentions": mentions,
            "discussion_id": discussion_id,
        },
        refs={
            "conversation": ExternalRef(kind="lark.conversation", id=f"{identity.tenant_key}:{chat_id}"),
            "discussion": ExternalRef(kind="lark.discussion", id=f"{identity.tenant_key}:{discussion_id}"),
            "message": ExternalRef(kind="lark.message", id=f"{identity.tenant_key}:{message_id}"),
        },
        data=data,
        ordering_key=f"{create_time}:{event_id}",
    )


def _message_content(
    message_type: str,
    raw_content: JsonValue | None,
    bot_mention_keys: tuple[str, ...],
    *,
    bot_open_id: str,
) -> tuple[str | None, JsonObject]:
    if not isinstance(raw_content, str) or len(raw_content) > 512 * 1024:
        raise ValueError("invalid message content")
    content = _parse_object(raw_content.encode())
    if message_type == "text":
        text = content.get("text")
        if not isinstance(text, str):
            raise ValueError("invalid text content")
        return _remove_mentions(text, bot_mention_keys), {}
    if message_type == "post":
        text = _post_text(content, bot_mention_keys, bot_open_id=bot_open_id)
        localized = [value for key, value in sorted(content.items()) if isinstance(value, dict)]
        body = localized[0] if localized else content
        paragraphs = body.get("content")
        attachments: list[JsonValue] = []
        if isinstance(paragraphs, list):
            for paragraph in paragraphs:
                if not isinstance(paragraph, list):
                    continue
                for node in paragraph:
                    key = node.get("image_key") if isinstance(node, dict) and node.get("tag") == "img" else None
                    if isinstance(key, str) and 0 < len(key) <= 2048:
                        attachments.append({"type": "image", "image_key": key})
        return text, {"attachments": attachments} if attachments else {}
    if message_type in _ATTACHMENT_TYPES:
        metadata = {
            key: value
            for key, value in content.items()
            if key in _ATTACHMENT_FIELDS and (isinstance(value, int) or (isinstance(value, str) and len(value) <= 2048))
        }
        return None, {"attachment": {"type": message_type, **metadata}}
    return None, {}


def _post_text(content: JsonObject, bot_mention_keys: tuple[str, ...], *, bot_open_id: str) -> str:
    localized = [value for key, value in sorted(content.items()) if isinstance(key, str) and isinstance(value, dict)]
    body = localized[0] if localized else content
    title = body.get("title")
    paragraphs = body.get("content")
    if title is not None and not isinstance(title, str):
        raise ValueError("invalid post title")
    if not isinstance(paragraphs, list) or len(paragraphs) > 256:
        raise ValueError("invalid post paragraphs")
    parts: list[str] = [title] if title else []
    node_count = 0
    for paragraph in paragraphs:
        if not isinstance(paragraph, list) or len(paragraph) > 256:
            raise ValueError("invalid post paragraph")
        line: list[str] = []
        for node in paragraph:
            node_count += 1
            if node_count > 1024 or not isinstance(node, dict):
                raise ValueError("invalid post nodes")
            tag = node.get("tag")
            if tag in {"text", "a"}:
                text = node.get("text")
                if isinstance(text, str):
                    line.append(text)
            elif tag == "at" and node.get("user_id") != bot_open_id:
                name = node.get("user_name")
                if isinstance(name, str):
                    line.append(f"@{name}")
        if line:
            parts.append("".join(line))
    return _remove_mentions("\n".join(parts), bot_mention_keys)


def _mentions(value: JsonValue | None, bot_open_id: str) -> tuple[list[JsonValue], tuple[str, ...]]:
    if value is None:
        return [], ()
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError("invalid mentions")
    safe: list[JsonValue] = []
    keys: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid mention")
        key = item.get("key")
        identity = item.get("id")
        open_id = identity.get("open_id") if isinstance(identity, dict) else None
        if not isinstance(key, str) or not isinstance(open_id, str):
            raise ValueError("invalid mention")
        safe.append({"key": key, "open_id": open_id})
        if open_id == bot_open_id:
            keys.append(key)
    return safe, tuple(keys)


def _remove_mentions(text: str, keys: tuple[str, ...]) -> str:
    for key in keys:
        text = text.replace(key, " ")
    return " ".join(text.split())


def _parse_object(body: bytes) -> JsonObject:
    try:
        return _JSON_OBJECT.validate_python(json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, RecursionError) as error:
        raise _request_error(400, "invalid_payload") from error


def _object(value: JsonValue | None) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def _required_string(value: JsonObject, key: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not 1 <= len(selected) <= 2048:
        raise ValueError(f"invalid {key}")
    return selected


def _optional_string(value: JsonValue | None) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("invalid optional string")
    return value


def _integer_string(value: JsonValue | None) -> str:
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 16:
        raise ValueError("invalid timestamp")
    return value


def _same(value: JsonValue | None, expected: str) -> bool:
    return isinstance(value, str) and hmac.compare_digest(value, expected)


def _json_response(body: JsonObject) -> ProviderHttpResponse:
    return ProviderHttpResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body=json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    )


def _request_error(status_code: int, reason_code: str) -> ProviderRequestError:
    return ProviderRequestError(ProviderHttpResponse(status_code=status_code), reason_code=reason_code)


def _normalize_action(payload: JsonObject) -> ProviderRequestDecision:
    event = _object(payload.get("event")) or {}
    operator = _object(event.get("operator")) or {}
    context = _object(event.get("context")) or {}
    action = _object(event.get("action")) or {}
    value = _object(action.get("value")) or {}
    kind = value.get("kind")
    if kind not in {"a13n.task_control.v1", "a13n.routine.v1"}:
        return ProviderCompleteDecision(response=lark_acknowledgement())
    try:
        if event.get("host") != "im_message":
            raise ValueError("unsupported card host")
        operation = value.get("action")
        if kind == "a13n.routine.v1":
            if operation not in {"confirm", "cancel", "pause", "resume", "delete"}:
                raise ValueError("unsupported routine action")
            operation = f"routine_{operation}"
            reference = value.get("routine_id")
        else:
            if operation != "stop":
                raise ValueError("unsupported task action")
            reference = value.get("run_id")
        return ProviderActionDecision.model_validate(
            {
                "action": operation,
                "reference": reference,
                "token": value.get("token"),
                "actor_id": operator.get("open_id"),
                "conversation_id": context.get("open_chat_id"),
                "message_id": context.get("open_message_id"),
            }
        )
    except (ValidationError, ValueError) as error:
        raise _request_error(400, "invalid_card_action") from error
