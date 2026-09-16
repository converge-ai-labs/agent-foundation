"""Slack inspection response validation, independent of configured identity claims."""

from pydantic import ValidationError

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.http import ConnectivityHttpError
from a13n_service.connectivity.inspection import (
    ConversationCandidate,
    ConversationInfo,
    ConversationPage,
    InstallationInfo,
)


def installation(auth: JsonObject, response: JsonObject) -> InstallationInfo:
    bot = response.get("bot")
    if not isinstance(bot, dict) or bot.get("id") != auth.get("bot_id"):
        raise ConnectivityHttpError("invalid_provider_response")
    if bot.get("user_id") != auth.get("user_id"):
        raise ConnectivityHttpError("invalid_provider_response")
    try:
        return InstallationInfo.model_validate(
            {
                "app_id": bot.get("app_id"),
                "organization_id": auth.get("team_id"),
                "organization_name": auth.get("team"),
                "bot_id": auth.get("user_id"),
                "bot_name": bot.get("name"),
                "enterprise_id": auth.get("enterprise_id"),
                "enabled": bot.get("deleted") is False,
            }
        )
    except ValidationError as error:
        raise ConnectivityHttpError("invalid_provider_response") from error


def conversation(response: JsonObject, *, expected_id: str) -> ConversationInfo:
    channel = response.get("channel")
    if not isinstance(channel, dict) or channel.get("id") != expected_id:
        raise ConnectivityHttpError("invalid_provider_response")
    audience = "unknown"
    if channel.get("is_im") is True or channel.get("is_mpim") is True:
        audience = "direct"
    elif channel.get("is_im") is False and channel.get("is_mpim") is False:
        if channel.get("is_private") is True:
            audience = "private"
        elif channel.get("is_private") is False:
            audience = "public"
    archived = channel.get("is_archived")
    try:
        return ConversationInfo.model_validate(
            {
                "id": expected_id,
                "name": channel.get("name") or expected_id,
                "organization_id": channel.get("context_team_id"),
                "audience": audience,
                "is_member": channel.get("is_member"),
                "is_active": not archived if isinstance(archived, bool) else None,
                "external": channel.get("is_ext_shared"),
            }
        )
    except ValidationError as error:
        raise ConnectivityHttpError("invalid_provider_response") from error


def conversations(response: JsonObject, *, limit: int) -> ConversationPage:
    channels = response.get("channels")
    metadata = response.get("response_metadata", {})
    if not isinstance(channels, list) or len(channels) > limit or not isinstance(metadata, dict):
        raise ConnectivityHttpError("invalid_provider_response")
    cursor = metadata.get("next_cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ConnectivityHttpError("invalid_provider_response")
    try:
        items = []
        for channel in channels:
            if not isinstance(channel, dict):
                raise ConnectivityHttpError("invalid_provider_response")
            items.append(ConversationCandidate.model_validate({"id": channel.get("id"), "name": channel.get("name")}))
        return ConversationPage.model_validate({"items": tuple(items), "cursor": cursor or None})
    except ValidationError as error:
        raise ConnectivityHttpError("invalid_provider_response") from error
