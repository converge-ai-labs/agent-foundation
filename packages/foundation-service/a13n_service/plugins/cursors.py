"""Opaque scope-bound Plugin collection cursors."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime


class PluginCursorError(ValueError):
    pass


def encode_plugin_cursor(*, updated_at: datetime, plugin_id: str, scope: dict[str, object]) -> str:
    return _encode({"v": "1", "kind": "plugin", "time": _time(updated_at), "id": plugin_id, "scope": _scope(scope)})


def decode_plugin_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    payload = _decode(value, kind="plugin", scope=scope)
    plugin_id = payload.get("id")
    if not isinstance(plugin_id, str) or not plugin_id.startswith("plg_"):
        raise PluginCursorError("invalid cursor")
    return _parse_time(payload), plugin_id


def encode_plugin_version_cursor(*, created_at: datetime, version_id: str, scope: dict[str, object]) -> str:
    return _encode(
        {"v": "1", "kind": "plugin-version", "time": _time(created_at), "id": version_id, "scope": _scope(scope)}
    )


def decode_plugin_version_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    payload = _decode(value, kind="plugin-version", scope=scope)
    version_id = payload.get("id")
    if not isinstance(version_id, str) or not version_id.startswith("plgv_"):
        raise PluginCursorError("invalid cursor")
    return _parse_time(payload), version_id


def _encode(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def _decode(value: str, *, kind: str, scope: dict[str, object]) -> dict[str, object]:
    if not value or len(value) > 2048:
        raise PluginCursorError("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
    except (ValueError, json.JSONDecodeError) as error:
        raise PluginCursorError("invalid cursor") from error
    if (
        not isinstance(payload, dict)
        or payload.get("v") != "1"
        or payload.get("kind") != kind
        or payload.get("scope") != _scope(scope)
    ):
        raise PluginCursorError("cursor does not match this query")
    return payload


def _scope(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _time(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_time(payload: dict[str, object]) -> datetime:
    try:
        value = datetime.fromisoformat(str(payload["time"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as error:
        raise PluginCursorError("invalid cursor") from error
    return value.astimezone(UTC)
