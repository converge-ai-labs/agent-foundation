"""Opaque scope-bound cursors for Agent collections."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime


class AgentCursorError(ValueError):
    pass


def encode_agent_cursor(*, updated_at: datetime, agent_id: str, scope: dict[str, object]) -> str:
    return _encode({"v": "1", "kind": "agent", "time": _time(updated_at), "id": agent_id, "scope": _scope(scope)})


def decode_agent_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    payload = _decode(value, kind="agent", scope=scope)
    agent_id = payload.get("id")
    if not isinstance(agent_id, str) or not agent_id.startswith("ap_"):
        raise AgentCursorError("invalid cursor")
    return _parse_time(payload), agent_id


def encode_revision_cursor(*, version: int, revision_id: str, scope: dict[str, object]) -> str:
    return _encode(
        {
            "v": "1",
            "kind": "revision",
            "number": version,
            "id": revision_id,
            "scope": _scope(scope),
        }
    )


def decode_revision_cursor(value: str, *, scope: dict[str, object]) -> tuple[int, str]:
    payload = _decode(value, kind="revision", scope=scope)
    revision_id = payload.get("id")
    number = payload.get("number")
    if not isinstance(revision_id, str) or not revision_id.startswith("apr_"):
        raise AgentCursorError("invalid cursor")
    if not isinstance(number, int) or number < 1:
        raise AgentCursorError("invalid cursor")
    return number, revision_id


def _encode(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def _decode(value: str, *, kind: str, scope: dict[str, object]) -> dict[str, object]:
    if not value or len(value) > 2048:
        raise AgentCursorError("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
    except (ValueError, json.JSONDecodeError) as error:
        raise AgentCursorError("invalid cursor") from error
    if (
        not isinstance(payload, dict)
        or payload.get("v") != "1"
        or payload.get("kind") != kind
        or payload.get("scope") != _scope(scope)
    ):
        raise AgentCursorError("cursor does not match this query")
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
        raise AgentCursorError("invalid cursor") from error
    return value.astimezone(UTC)
