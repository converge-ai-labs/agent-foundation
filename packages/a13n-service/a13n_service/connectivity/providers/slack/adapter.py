"""Slack HTTP v1 webhook authentication, normalization, and routing."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, TypeAdapter, ValidationError

from a13n_service.connectivity.accounts.domain import AccountProviderDefinition
from a13n_service.connectivity.accounts.reception import InputBatchingPolicy
from a13n_service.connectivity.adapters import JsonObject
from a13n_service.connectivity.ingress.provider import (
    AdmissionReceipt,
    ExternalRef,
    InboundEvent,
    ProviderCompleteDecision,
    ProviderEligibleEventRouting,
    ProviderEventDecision,
    ProviderEventRouting,
    ProviderHttpResponse,
    ProviderIrrelevantEventRouting,
    ProviderRequest,
    ProviderRequestDecision,
    ProviderRequestError,
    ProviderRequiresBindingRouting,
    ReceptionDefaults,
)

from ..common.messaging import MessagingPolicy

CONTEXT_VERSION = "slack_event_v1"

_CONFIG_VERSION = "slack_http_v1"
_REQUEST_MAX_BYTES = 1024 * 1024
_SIGNATURE_MAX_AGE_SECONDS = 5 * 60
_DEDUP_HORIZON_SECONDS = 24 * 60 * 60
_EVENT_KINDS = frozenset({"app_mention", "message"})
_CONVERSATION_KINDS = frozenset({"channel", "group", "im", "mpim"})
_NATIVE_ACTIONS = ("slack.reply", "slack.list_members", "slack.read_messages")
_JSON_OBJECT = TypeAdapter(JsonObject)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SlackAccountConfig(_StrictModel):
    event_transport: Literal["http", "websocket"] = "http"
    api_app_id: str = Field(min_length=1, max_length=128)
    team_id: str = Field(min_length=1, max_length=128)
    enterprise_id: str | None = Field(default=None, min_length=1, max_length=128)
    bot_user_id: str = Field(min_length=1, max_length=128)


class SlackAccountCredentials(_StrictModel):
    app_token: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    signing_secret: SecretStr | None = Field(default=None, min_length=1, max_length=512)
    bot_token: SecretStr = Field(min_length=1, max_length=4096)


class SlackIngressAdapter:
    provider_key = "slack"
    config_versions = frozenset({_CONFIG_VERSION})
    max_request_bytes = _REQUEST_MAX_BYTES
    dedup_horizon_seconds = _DEDUP_HORIZON_SECONDS

    def validate_config(self, value: object, *, config_version: str) -> JsonObject:
        _require_version(config_version)
        return _model_json(SlackAccountConfig.model_validate(value))

    def describe_account(self, *, config_version: str) -> AccountProviderDefinition:
        _require_version(config_version)
        return AccountProviderDefinition(
            provider_key=self.provider_key,
            config_version=config_version,
            configuration_schema=SlackAccountConfig.model_json_schema(),
            credential_schema=SlackAccountCredentials.model_json_schema(),
            reception_policy_schema=MessagingPolicy.model_json_schema(),
            target_kinds=("conversation",),
        )

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> JsonObject:
        _require_version(config_version)
        credentials = SlackAccountCredentials.model_validate(value)
        return {key: secret.get_secret_value() for key, secret in credentials.model_dump(exclude_none=True).items()}

    def configuration_identity(self, value: JsonObject, *, config_version: str) -> object:
        _require_version(config_version)
        config = SlackAccountConfig.model_validate(value)
        return (
            config.api_app_id,
            config.team_id,
            config.enterprise_id,
            config.bot_user_id,
        )

    def validate_reception_policy(self, value: object, *, config_version: str) -> JsonObject:
        _require_version(config_version)
        return _model_json(MessagingPolicy.model_validate(value))

    def validate_target(self, kind: str, external_id: str) -> str:
        if (
            kind != "conversation"
            or not 1 <= len(external_id) <= 128
            or any(character.isspace() for character in external_id)
        ):
            raise ValueError("Invalid slack target")
        return external_id

    def event_target(self, event: InboundEvent) -> tuple[str, str]:
        identifier = str(event.context["channel_id"])
        return "conversation", self.validate_target("conversation", identifier)

    async def authenticate_and_normalize(
        self,
        request: ProviderRequest,
        *,
        account_id: str,
        account_config: JsonObject,
        credentials: JsonObject,
        received_at: datetime,
    ) -> ProviderRequestDecision:
        del account_id
        config = SlackAccountConfig.model_validate(account_config)
        if config.event_transport != "http":
            return ProviderCompleteDecision(response=ProviderHttpResponse(status_code=404, body=b"", headers={}))
        secret = _required_string(credentials, "signing_secret")
        _authenticate(request, secret=secret, received_at=received_at)
        payload = _parse_object(request.body)
        return normalize_payload(payload, config, received_at)

    def reception_defaults(
        self,
        event: InboundEvent,
        account_config: JsonObject,
        *,
        config_version: str,
    ) -> ReceptionDefaults:
        del event
        _require_version(config_version)
        SlackAccountConfig.model_validate(account_config)
        return ReceptionDefaults(
            input_batching=InputBatchingPolicy(min_interval_ms=1, max_batch_events=10),
            provider_policy=_model_json(MessagingPolicy(interaction_mode="mention", reply_mode="thread")),
        )

    def classify(
        self,
        event: InboundEvent,
        provider_policy: JsonObject,
        account_config: JsonObject,
        *,
        config_version: str,
    ) -> ProviderEventRouting:
        _require_version(config_version)
        config = SlackAccountConfig.model_validate(account_config)
        policy = MessagingPolicy.model_validate(provider_policy)
        direct = event.context.get("conversation_kind") == "im"
        mentioned = event.context.get("mentioned") is True
        if not direct and policy.interaction_mode == "mention" and not mentioned:
            return ProviderIrrelevantEventRouting(reason_code="mention_required")
        if not direct and policy.interaction_mode == "discussion" and not mentioned:
            return ProviderRequiresBindingRouting(
                external_ref_key="discussion",
                provider_context=_provider_context(event, config),
                native_actions=_NATIVE_ACTIONS,
            )
        ref_key = "conversation" if policy.interaction_mode == "chat" else "discussion"
        return ProviderEligibleEventRouting(
            external_ref_key=ref_key,
            provider_context=_provider_context(event, config),
            native_actions=_NATIVE_ACTIONS,
        )

    def acknowledge(self, receipt: AdmissionReceipt) -> ProviderHttpResponse:
        del receipt
        return _acknowledgement()

    def failure_response(self, reason_code: str) -> ProviderHttpResponse:
        if reason_code == "delivery_identity_conflict":
            return ProviderHttpResponse(status_code=409)
        return ProviderHttpResponse(status_code=503, headers={"retry-after": "1"})


def _authenticate(request: ProviderRequest, *, secret: str, received_at: datetime) -> None:
    timestamp_text = request.headers.get("x-slack-request-timestamp")
    signature = request.headers.get("x-slack-signature")
    if timestamp_text is None or signature is None:
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
    signed = b"v0:" + timestamp_text.encode("ascii") + b":" + request.body
    expected = "v0=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise _request_error(401, "invalid_signature")


def _verify_installation(payload: JsonObject, config: SlackAccountConfig) -> None:
    if payload.get("api_app_id") != config.api_app_id or payload.get("team_id") != config.team_id:
        raise _request_error(404, "ingress_not_found")
    if payload.get("enterprise_id") != config.enterprise_id:
        raise _request_error(404, "ingress_not_found")
    if payload.get("type") != "event_callback":
        return
    authorizations = payload.get("authorizations")
    if not isinstance(authorizations, list) or not any(
        isinstance(item, dict)
        and item.get("team_id") == config.team_id
        and item.get("enterprise_id") == config.enterprise_id
        for item in authorizations
    ):
        raise _request_error(404, "ingress_not_found")


def _normalize_event(
    event_id: str,
    event: dict[str, JsonValue],
    config: SlackAccountConfig,
    received_at: datetime,
) -> InboundEvent | None:
    event_kind = event.get("type")
    conversation_kind = event.get("channel_type", "channel" if event_kind == "app_mention" else None)
    channel_id = event.get("channel")
    message_ts = event.get("ts")
    user_id = event.get("user")
    if not isinstance(event_kind, str) or event_kind not in _EVENT_KINDS:
        return None
    if not isinstance(conversation_kind, str) or conversation_kind not in _CONVERSATION_KINDS:
        return None
    if not isinstance(channel_id, str) or not channel_id:
        return None
    if not isinstance(message_ts, str) or not message_ts:
        return None
    if not isinstance(user_id, str) or not user_id:
        return None
    if user_id == config.bot_user_id or event.get("bot_id") is not None:
        return None
    subtype = event.get("subtype")
    if subtype is not None:
        return None
    text = event.get("text")
    if not isinstance(text, str):
        return None
    mention_pattern = re.compile(rf"<@{re.escape(config.bot_user_id)}(?:\|[^>]+)?>")
    mentioned = mention_pattern.search(text) is not None
    normalized_text = " ".join(mention_pattern.sub(" ", text).split())
    if not normalized_text:
        return None
    root_thread_ts = event.get("thread_ts")
    if not isinstance(root_thread_ts, str) or not root_thread_ts:
        root_thread_ts = message_ts
    return InboundEvent(
        identity_kind="slack.delivery",
        external_event_id=event_id,
        normalization_version=CONTEXT_VERSION,
        type=f"slack.{event_kind}",
        occurred_at=_slack_timestamp(message_ts),
        received_at=received_at,
        text=normalized_text,
        actor={"user_id": user_id},
        context={
            "team_id": config.team_id,
            "channel_id": channel_id,
            "conversation_kind": conversation_kind,
            "event_kind": event_kind,
            "message_ts": message_ts,
            "root_thread_ts": root_thread_ts,
            "mentioned": mentioned,
        },
        refs={
            "conversation": ExternalRef(kind="slack.conversation", id=f"{config.team_id}:{channel_id}"),
            "discussion": ExternalRef(
                kind="slack.discussion",
                id=f"{config.team_id}:{channel_id}:{root_thread_ts}",
            ),
            "message": ExternalRef(
                kind="slack.message",
                id=f"{config.team_id}:{channel_id}:{message_ts}",
            ),
        },
        data={},
        ordering_key=f"{message_ts}:{event_id}",
    )


def _slack_timestamp(value: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(Decimal(value)), tz=UTC)
    except (InvalidOperation, ValueError, OverflowError) as error:
        raise _request_error(400, "invalid_payload") from error


def _required_string(value: JsonObject, key: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not selected:
        raise _request_error(503, "credential_unavailable")
    return selected


def _provider_context(event: InboundEvent, config: SlackAccountConfig) -> JsonObject:
    return {
        "team_id": config.team_id,
        "channel_id": event.context["channel_id"],
        "message_ts": event.context["message_ts"],
        "root_thread_ts": event.context["root_thread_ts"],
        "conversation_kind": event.context["conversation_kind"],
    }


def _parse_object(body: bytes) -> JsonObject:
    try:
        return _JSON_OBJECT.validate_python(json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, RecursionError) as error:
        raise _request_error(400, "invalid_payload") from error


def _model_json(value: BaseModel) -> JsonObject:
    return _JSON_OBJECT.validate_python(value.model_dump(mode="json"))


def _request_error(status_code: int, reason_code: str) -> ProviderRequestError:
    return ProviderRequestError(ProviderHttpResponse(status_code=status_code), reason_code=reason_code)


def _acknowledgement() -> ProviderHttpResponse:
    return ProviderHttpResponse(status_code=200)


def _json_response(status_code: int, body: JsonObject) -> ProviderHttpResponse:
    return ProviderHttpResponse(
        status_code=status_code,
        headers={"content-type": "application/json"},
        body=json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
    )


def _require_version(value: str) -> None:
    if value != _CONFIG_VERSION:
        raise ValueError("unsupported Slack configuration version")


def normalize_payload(
    payload: JsonObject, config: SlackAccountConfig, received_at: datetime
) -> ProviderRequestDecision:
    _verify_installation(payload, config)
    payload_type = payload.get("type")
    if payload_type == "url_verification":
        challenge = payload.get("challenge")
        if not isinstance(challenge, str) or not 1 <= len(challenge) <= 4096:
            raise _request_error(400, "invalid_payload")
        return ProviderCompleteDecision(response=_json_response(200, {"challenge": challenge}))
    if payload_type != "event_callback":
        return ProviderCompleteDecision(response=_acknowledgement())
    event_id = payload.get("event_id")
    event = payload.get("event")
    if not isinstance(event_id, str) or not event_id or not isinstance(event, dict):
        raise _request_error(400, "invalid_payload")
    try:
        normalized = _normalize_event(event_id, event, config, received_at)
    except ValidationError as error:
        raise _request_error(400, "invalid_payload") from error
    if normalized is None:
        return ProviderCompleteDecision(response=_acknowledgement())
    return ProviderEventDecision(event=normalized)
