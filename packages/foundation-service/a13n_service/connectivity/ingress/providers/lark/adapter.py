"""Lark/Feishu HTTP v1 ingress configuration and routing."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.ingress.domain import InputBatchingPolicy
from a13n_service.connectivity.ingress.provider import (
    AdmissionReceipt,
    DefaultRoute,
    InboundEvent,
    ProviderEligibleEventRouting,
    ProviderEventRouting,
    ProviderHttpResponse,
    ProviderIrrelevantEventRouting,
    ProviderRequest,
    ProviderRequestDecision,
    ProviderRequestError,
    ProviderRequiresBindingRouting,
)

from ..common.mapping import default_event_mapping
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


class LarkIngressConfig(_StrictModel):
    brand: Literal["feishu", "lark"]
    open_api_origin: str = Field(min_length=1, max_length=2048)
    app_id: str = Field(min_length=1, max_length=256)
    tenant_key: str = Field(min_length=1, max_length=256)
    bot_open_id: str = Field(min_length=1, max_length=256)
    events_transport: Literal["http"] = "http"


class LarkRouteMatch(_StrictModel):
    event_kinds: tuple[Literal["message"], ...] = Field(min_length=1, max_length=1)
    chat_types: tuple[Literal["p2p", "group"], ...] = Field(min_length=1, max_length=2)
    chat_ids: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("event_kinds", "chat_types", "chat_ids")
    @classmethod
    def unique_sorted(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        if len(set(value)) != len(value) or any(not item or len(item) > 256 for item in value):
            raise ValueError("Lark Route scopes must contain unique bounded values")
        return tuple(sorted(value))


class LarkIngressAdapter:
    provider_key = "lark"
    config_versions = frozenset({_CONFIG_VERSION})
    allows_runtime_ambiguity = False
    max_request_bytes = _REQUEST_MAX_BYTES
    dedup_horizon_seconds = _DEDUP_HORIZON_SECONDS

    def __init__(self, *, allowed_provider_origins: tuple[str, ...] = ()) -> None:
        self._allowed_provider_origins = normalize_provider_origins(allowed_provider_origins)

    def validate_config(self, value: object, *, config_version: str) -> JsonObject:
        _require_version(config_version)
        config = LarkIngressConfig.model_validate(value)
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
        config = LarkIngressConfig.model_validate(value)
        return (
            config.brand,
            config.open_api_origin,
            config.app_id,
            config.tenant_key,
            config.bot_open_id,
            config.events_transport,
        )

    def validate_route(
        self,
        *,
        match: object,
        provider_policy: object,
        ingress_config: JsonObject,
        config_version: str,
    ) -> tuple[JsonObject, JsonObject]:
        _require_version(config_version)
        LarkIngressConfig.model_validate(ingress_config)
        return _model_json(LarkRouteMatch.model_validate(match)), _model_json(
            MessagingPolicy.model_validate(provider_policy)
        )

    def prove_non_overlap(self, left: JsonObject, right: JsonObject) -> bool | None:
        left_match = LarkRouteMatch.model_validate(left)
        right_match = LarkRouteMatch.model_validate(right)
        if set(left_match.event_kinds).isdisjoint(right_match.event_kinds):
            return True
        if set(left_match.chat_types).isdisjoint(right_match.chat_types):
            return True
        if left_match.chat_ids is not None and right_match.chat_ids is not None:
            return set(left_match.chat_ids).isdisjoint(right_match.chat_ids)
        return False

    async def authenticate_and_normalize(
        self,
        request: ProviderRequest,
        *,
        ingress_id: str,
        ingress_config: JsonObject,
        credentials: JsonObject,
        received_at: datetime,
    ) -> ProviderRequestDecision:
        del ingress_id
        config = LarkIngressConfig.model_validate(ingress_config)
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

    def route_matches(self, event: InboundEvent, match: JsonObject, *, config_version: str) -> bool:
        _require_version(config_version)
        route = LarkRouteMatch.model_validate(match)
        event_kind = event.context.get("event_kind")
        chat_type = event.context.get("chat_type")
        chat_id = event.context.get("chat_id")
        return (
            isinstance(event_kind, str)
            and event_kind in route.event_kinds
            and isinstance(chat_type, str)
            and chat_type in route.chat_types
            and isinstance(chat_id, str)
            and (route.chat_ids is None or chat_id in route.chat_ids)
        )

    def default_route(
        self,
        event: InboundEvent,
        ingress_config: JsonObject,
        *,
        config_version: str,
    ) -> DefaultRoute:
        del event
        _require_version(config_version)
        LarkIngressConfig.model_validate(ingress_config)
        return DefaultRoute(
            input_mapping=default_event_mapping(),
            input_batching=InputBatchingPolicy(min_interval_ms=1, max_batch_events=10),
            provider_policy=_model_json(MessagingPolicy(interaction_mode="mention", reply_mode="thread")),
        )

    def classify(
        self,
        event: InboundEvent,
        provider_policy: JsonObject,
        ingress_config: JsonObject,
        *,
        config_version: str,
    ) -> ProviderEventRouting:
        _require_version(config_version)
        config = LarkIngressConfig.model_validate(ingress_config)
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


def _provider_context(event: InboundEvent, config: LarkIngressConfig) -> JsonObject:
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
