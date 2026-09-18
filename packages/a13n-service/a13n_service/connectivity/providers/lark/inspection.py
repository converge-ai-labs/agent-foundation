"""Feishu/Lark inspection response validation."""

from a13n_harness.providers.http import ProviderHttpError
from pydantic import ValidationError

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.inspection import (
    ConversationCandidate,
    ConversationInfo,
    ConversationPage,
    InstallationInfo,
)


def installation(bot_response: JsonObject, tenant_response: JsonObject, *, app_id: str) -> InstallationInfo:
    # bot/v3/info returns a top-level bot, unlike the data envelope of tenant/v2.
    bot = bot_response.get("bot")
    data = tenant_response.get("data")
    tenant = data.get("tenant") if isinstance(data, dict) else None
    if not isinstance(bot, dict) or not isinstance(tenant, dict):
        raise ProviderHttpError("invalid_provider_response")
    activation = bot.get("activate_status")
    if type(activation) is not int or activation not in range(7):
        raise ProviderHttpError("invalid_provider_response")
    try:
        return InstallationInfo.model_validate(
            {
                "app_id": app_id,
                "organization_id": tenant.get("tenant_key"),
                "organization_name": tenant.get("name"),
                "bot_id": bot.get("open_id"),
                "bot_name": bot.get("app_name"),
                "enabled": activation == 2,
            }
        )
    except ValidationError as error:
        raise ProviderHttpError("invalid_provider_response") from error


def conversation(detail: JsonObject, membership: JsonObject, *, chat_id: str) -> ConversationInfo:
    data = detail.get("data")
    member = membership.get("data")
    if not isinstance(data, dict) or not isinstance(member, dict):
        raise ProviderHttpError("invalid_provider_response")
    audience = "unknown"
    mode = data.get("chat_mode")
    if mode == "p2p":
        audience = "direct"
    elif mode in ("group", "topic"):
        if data.get("chat_type") == "public":
            audience = "public"
        elif data.get("chat_type") == "private":
            audience = "private"
    try:
        return ConversationInfo.model_validate(
            {
                "id": chat_id,
                "name": data.get("name") or chat_id,
                "organization_id": data.get("tenant_key"),
                "audience": audience,
                "is_member": member.get("is_in_chat"),
                "is_active": True,
                "external": data.get("external"),
            }
        )
    except ValidationError as error:
        raise ProviderHttpError("invalid_provider_response") from error


def conversations(response: JsonObject, *, limit: int) -> ConversationPage:
    data = response.get("data")
    if not isinstance(data, dict):
        raise ProviderHttpError("invalid_provider_response")
    items = data.get("items")
    more = data.get("has_more")
    if not isinstance(items, list) or len(items) > limit or not isinstance(more, bool):
        raise ProviderHttpError("invalid_provider_response")
    cursor = data.get("page_token") if more else None
    if more and not cursor:
        raise ProviderHttpError("invalid_provider_response")
    try:
        candidates = []
        for item in items:
            if not isinstance(item, dict):
                raise ProviderHttpError("invalid_provider_response")
            candidates.append(
                ConversationCandidate.model_validate({"id": item.get("chat_id"), "name": item.get("name")})
            )
        return ConversationPage.model_validate({"items": tuple(candidates), "cursor": cursor})
    except ValidationError as error:
        raise ProviderHttpError("invalid_provider_response") from error
