"""Opaque, query-bound cursor encoding for Model Management collections."""

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


def encode_model_cursor(*, updated_at: datetime, item_id: str, scope: dict[str, object]) -> str:
    return encode_time_cursor(updated_at, item_id, scope=scope)


def decode_model_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    try:
        return decode_time_cursor(value, scope=scope, id_prefix=("mdl_", "mprov_"))
    except CollectionCursorMismatchError as error:
        raise CursorError("cursor does not match this query") from error
    except InvalidCollectionCursorError as error:
        raise CursorError("invalid cursor") from error
