"""Opaque, query-bound HookSubscription collection cursors."""

from __future__ import annotations

from datetime import datetime

from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from a13n_service.temporal import assume_utc


class HookCursorError(ValueError):
    pass


def encode_hook_cursor(*, updated_at: datetime, subscription_id: str, scope: dict[str, object]) -> str:
    return encode_collection_cursor(
        {
            "updated_at": assume_utc(updated_at).isoformat().replace("+00:00", "Z"),
            "id": subscription_id,
        },
        scope=scope,
    )


def decode_hook_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    try:
        payload = decode_collection_cursor(value, scope=scope)
        updated_at = datetime.fromisoformat(str(payload["updated_at"]).replace("Z", "+00:00"))
        subscription_id = payload["id"]
        if not isinstance(subscription_id, str) or not subscription_id.startswith("hsub_"):
            raise ValueError("invalid subscription ID")
    except CollectionCursorMismatchError as error:
        raise HookCursorError("cursor does not match this collection") from error
    except (KeyError, TypeError, ValueError) as error:
        raise HookCursorError("invalid cursor") from error
    return assume_utc(updated_at), subscription_id


__all__ = ["HookCursorError", "decode_hook_cursor", "encode_hook_cursor"]
