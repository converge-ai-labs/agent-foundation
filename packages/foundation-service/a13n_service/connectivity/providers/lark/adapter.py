"""Lark/Feishu HTTP v1 ingress configuration and routing."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from a13n_service.connectivity.accounts.reception import InputBatchingPolicy
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.ingress.provider import (
    AdmissionReceipt,
    InboundEvent,
    ProviderEligibleEventRouting,
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
from ..common.origins import normalize_provider_origins, require_provider_origin
from .wire import LarkIdentity, authenticate_and_normalize, lark_acknowledgement

_CONFIG_VERSION = "lark_http_v1"
_REQUEST_MAX_BYTES = 1024 * 1024
_DEDUP_HORIZON_SECONDS = 24 * 60 * 60
_NATIVE_ACTIONS = ("lark.reply", "lark.list_members", "lark.read_messages")
_OFFICIAL_ORIGINS = frozenset({"https://open.feishu.cn", "https://open.larksuite.com"})
_BRAND_ORIGIN = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}
_JSON_OBJECT = TypeAdapter(JsonObject)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LarkAccountConfig(_StrictModel):
    brand: Literal["feishu", "lark"]
    open_api_origin: str = Field(min_length=1, max_length=2048)
    app_id: str = Field(min_length=1, max_length=256)
    tenant_key: str = Field(min_length=1, max_length=256)
    bot_open_id: str = Field(min_length=1, max_length=256)


class LarkIngressAdapter:
    provider_key = "lark"
    config_versions = frozenset({_CONFIG_VERSION})
    max_request_bytes = _REQUEST_MAX_BYTES
    dedup_horizon_seconds = _DEDUP_HORIZON_SECONDS

    def __init__(self, *, allowed_provider_origins: tuple[str, ...] = ()) -> None:
        self._allowed_provider_origins = normalize_provider_origins(allowed_provider_origins)

    def validate_config(self, value: object, *, config_version: str) -> JsonObject:
        _require_version(config_version)
        config = LarkAccountConfig.model_validate(value)
        origin = require_provider_origin(
            config.open_api_origin,
            official_origins=_OFFICIAL_ORIGINS,
            allowed_custom_origins=self._allowed_provider_origins,
        )
        if origin in _OFFICIAL_ORIGINS and origin != _BRAND_ORIGIN[config.brand]:
            raise ValueError("Lark brand and official API origin do not match")
        return _model_json(config.model_copy(update={"open_api_origin": origin}))

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> JsonObject:
        _require_version(config_version)
        if set(value) not in (
            {"app_secret", "verification_token"},
            {"app_secret", "encrypt_key", "verification_token"},
        ):
            raise ValueError("Lark credentials are incomplete")
        limits = {"app_secret": 4096, "encrypt_key": 4096, "verification_token": 4096}
        if any(not secret or len(secret) > limits[key] for key, secret in value.items()):
            raise ValueError("Lark credentials are invalid")
        return dict(value)

    def configuration_identity(self, value: JsonObject, *, config_version: str) -> object:
        _require_version(config_version)
        config = LarkAccountConfig.model_validate(value)
        return (
            config.brand,
            config.open_api_origin,
            config.app_id,
            config.tenant_key,
            config.bot_open_id,
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
            raise ValueError("Invalid lark target")
        return external_id

    def event_target(self, event: InboundEvent) -> tuple[str, str]:
        identifier = str(event.context["chat_id"])
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
        config = LarkAccountConfig.model_validate(account_config)
        return authenticate_and_normalize(
            request,
            identity=LarkIdentity(
                app_id=config.app_id,
                tenant_key=config.tenant_key,
                bot_open_id=config.bot_open_id,
            ),
            encrypt_key=_optional_credential(credentials, "encrypt_key"),
            verification_token=_required_credential(credentials, "verification_token"),
            received_at=received_at,
        )

    def reception_defaults(
        self,
        event: InboundEvent,
        account_config: JsonObject,
        *,
        config_version: str,
    ) -> ReceptionDefaults:
        del event
        _require_version(config_version)
        LarkAccountConfig.model_validate(account_config)
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
        config = LarkAccountConfig.model_validate(account_config)
        policy = MessagingPolicy.model_validate(provider_policy)
        direct = event.context.get("chat_type") == "p2p"
        mentioned = event.context.get("mentioned") is True
        if not direct and policy.interaction_mode == "mention" and not mentioned:
            return ProviderIrrelevantEventRouting(reason_code="mention_required")
        context = _provider_context(event, config)
        if not direct and policy.interaction_mode == "discussion" and not mentioned:
            return ProviderRequiresBindingRouting(
                external_ref_key="discussion",
                provider_context=context,
                native_actions=_NATIVE_ACTIONS,
            )
        return ProviderEligibleEventRouting(
            external_ref_key="conversation" if policy.interaction_mode == "chat" else "discussion",
            provider_context=context,
            native_actions=_NATIVE_ACTIONS,
        )

    def acknowledge(self, receipt: AdmissionReceipt) -> ProviderHttpResponse:
        del receipt
        return lark_acknowledgement()

    def failure_response(self, reason_code: str) -> ProviderHttpResponse:
        if reason_code == "delivery_identity_conflict":
            return ProviderHttpResponse(status_code=409)
        return ProviderHttpResponse(status_code=503, headers={"retry-after": "1"})


def _provider_context(event: InboundEvent, config: LarkAccountConfig) -> JsonObject:
    return {
        "tenant_key": config.tenant_key,
        "chat_id": event.context["chat_id"],
        "chat_type": event.context["chat_type"],
        "message_id": event.context["message_id"],
        "discussion_id": event.context["discussion_id"],
    }


def _required_credential(value: JsonObject, key: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not selected:
        raise ProviderRequestError(
            ProviderHttpResponse(status_code=503),
            reason_code="credential_unavailable",
        )
    return selected


def _optional_credential(value: JsonObject, key: str) -> str | None:
    selected = value.get(key)
    if selected is None:
        return None
    if not isinstance(selected, str) or not selected:
        raise ProviderRequestError(
            ProviderHttpResponse(status_code=503),
            reason_code="credential_unavailable",
        )
    return selected


def _model_json(value: BaseModel) -> JsonObject:
    return _JSON_OBJECT.validate_python(value.model_dump(mode="json"))


def _require_version(value: str) -> None:
    if value != _CONFIG_VERSION:
        raise ValueError("unsupported Lark configuration version")
