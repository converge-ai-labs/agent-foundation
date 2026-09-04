"""Opaque scope-bound Plugin collection cursors."""

from __future__ import annotations

from datetime import datetime

from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from a13n_service.temporal import assume_utc, require_aware_utc


class PluginCursorError(ValueError):
    pass


def encode_plugin_cursor(*, updated_at: datetime, plugin_id: str, scope: dict[str, object]) -> str:
    return encode_collection_cursor(
        {"time": _time(updated_at), "id": plugin_id},
        kind="plugin",
        scope=scope,
    )


def decode_plugin_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    payload = _decode(value, kind="plugin", scope=scope)
    plugin_id = payload.get("id")
    if not isinstance(plugin_id, str) or not plugin_id.startswith("plg_"):
        raise PluginCursorError("invalid cursor")
    return _parse_time(payload), plugin_id


def encode_plugin_version_cursor(*, created_at: datetime, version_id: str, scope: dict[str, object]) -> str:
    return encode_collection_cursor(
        {"time": _time(created_at), "id": version_id},
        kind="plugin-version",
        scope=scope,
    )


def decode_plugin_version_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    payload = _decode(value, kind="plugin-version", scope=scope)
    version_id = payload.get("id")
    if not isinstance(version_id, str) or not version_id.startswith("plgv_"):
        raise PluginCursorError("invalid cursor")
    return _parse_time(payload), version_id


def _decode(value: str, *, kind: str, scope: dict[str, object]) -> dict[str, object]:
    try:
        return decode_collection_cursor(value, kind=kind, scope=scope)
    except CollectionCursorMismatchError as error:
        raise PluginCursorError("cursor does not match this query") from error
    except InvalidCollectionCursorError as error:
        raise PluginCursorError("invalid cursor") from error


def _time(value: datetime) -> str:
    return assume_utc(value).isoformat().replace("+00:00", "Z")


def _parse_time(payload: dict[str, object]) -> datetime:
    try:
        value = datetime.fromisoformat(str(payload["time"]).replace("Z", "+00:00"))
        return require_aware_utc(value)
    except (KeyError, ValueError) as error:
        raise PluginCursorError("invalid cursor") from error
