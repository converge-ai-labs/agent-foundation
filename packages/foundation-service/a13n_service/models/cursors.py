"""Opaque, query-bound cursor encoding for Model Management collections."""

from __future__ import annotations

from datetime import datetime

from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from a13n_service.temporal import assume_utc


class CursorError(ValueError):
    pass


def encode_model_cursor(*, updated_at: datetime, item_id: str, scope: dict[str, object]) -> str:
    return encode_collection_cursor(
        {
            "updated_at": assume_utc(updated_at).isoformat().replace("+00:00", "Z"),
            "id": item_id,
        },
        scope=scope,
    )


def decode_model_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    try:
        payload = decode_collection_cursor(value, scope=scope)
    except CollectionCursorMismatchError as error:
        raise CursorError("cursor does not match this query") from error
    except InvalidCollectionCursorError as error:
        raise CursorError("invalid cursor") from error
    try:
        updated_at = datetime.fromisoformat(str(payload["updated_at"]).replace("Z", "+00:00"))
        item_id = payload["id"]
        if not isinstance(item_id, str) or not item_id.startswith(("mdl_", "mprov_")):
            raise CursorError("invalid cursor")
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, CursorError):
            raise
        raise CursorError("invalid cursor") from error
    return assume_utc(updated_at), item_id
