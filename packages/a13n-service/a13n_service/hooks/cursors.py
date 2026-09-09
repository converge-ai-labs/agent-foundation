"""Opaque, query-bound HookSubscription collection cursors."""

from __future__ import annotations

from datetime import datetime

from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_time_cursor,
    encode_time_cursor,
)


class HookCursorError(ValueError):
    pass


def encode_hook_cursor(*, updated_at: datetime, subscription_id: str, scope: dict[str, object]) -> str:
    return encode_time_cursor(updated_at, subscription_id, scope=scope)


def decode_hook_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    try:
        return decode_time_cursor(value, scope=scope, id_prefix="hsub_")
    except CollectionCursorMismatchError as error:
        raise HookCursorError("cursor does not match this collection") from error
    except InvalidCollectionCursorError as error:
        raise HookCursorError("invalid cursor") from error


__all__ = ["HookCursorError", "decode_hook_cursor", "encode_hook_cursor"]
