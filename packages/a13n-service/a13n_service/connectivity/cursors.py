"""Opaque, query-bound cursors shared by Connectivity collections."""

from __future__ import annotations

from datetime import datetime

from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_time_cursor,
    encode_time_cursor,
)


class CursorError(ValueError):
    pass


def encode_cursor(*, updated_at: datetime, object_id: str, scope: dict[str, object]) -> str:
    return encode_time_cursor(updated_at, object_id, scope=scope)


def decode_cursor(value: str, *, scope: dict[str, object], id_prefix: str) -> tuple[datetime, str]:
    try:
        return decode_time_cursor(value, scope=scope, id_prefix=f"{id_prefix}_")
    except CollectionCursorMismatchError as error:
        raise CursorError("cursor does not match this query") from error
    except InvalidCollectionCursorError as error:
        raise CursorError("invalid cursor") from error
