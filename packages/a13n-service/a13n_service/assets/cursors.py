"""Opaque query-bound cursor for Asset collections."""

from __future__ import annotations

from datetime import datetime

from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_time_cursor,
    encode_time_cursor,
)


class AssetCursorError(ValueError):
    pass


def encode_asset_cursor(*, created_at: datetime, asset_id: str, scope: dict[str, object]) -> str:
    return encode_time_cursor(created_at, asset_id, scope=scope, time_field="created_at")


def decode_asset_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    try:
        return decode_time_cursor(value, scope=scope, id_prefix="ast_", time_field="created_at")
    except CollectionCursorMismatchError as error:
        raise AssetCursorError("cursor does not match this query") from error
    except InvalidCollectionCursorError as error:
        raise AssetCursorError("invalid cursor") from error
