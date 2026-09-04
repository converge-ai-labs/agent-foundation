"""Opaque query-bound cursor for Asset collections."""

from __future__ import annotations

from datetime import datetime

from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from a13n_service.temporal import assume_utc


class AssetCursorError(ValueError):
    pass


def encode_asset_cursor(*, created_at: datetime, asset_id: str, scope: dict[str, object]) -> str:
    return encode_collection_cursor(
        {
            "created_at": assume_utc(created_at).isoformat().replace("+00:00", "Z"),
            "id": asset_id,
        },
        scope=scope,
    )


def decode_asset_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    try:
        payload = decode_collection_cursor(value, scope=scope)
    except CollectionCursorMismatchError as error:
        raise AssetCursorError("cursor does not match this query") from error
    except InvalidCollectionCursorError as error:
        raise AssetCursorError("invalid cursor") from error
    try:
        created_at = datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00"))
        asset_id = payload["id"]
        if not isinstance(asset_id, str) or not asset_id.startswith("ast_"):
            raise AssetCursorError("invalid cursor")
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, AssetCursorError):
            raise
        raise AssetCursorError("invalid cursor") from error
    return assume_utc(created_at), asset_id
