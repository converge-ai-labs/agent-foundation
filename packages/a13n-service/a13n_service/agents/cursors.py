"""Opaque scope-bound cursors for Agent collections."""

from __future__ import annotations

from datetime import datetime

from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_collection_cursor,
    decode_time_cursor,
    encode_collection_cursor,
    encode_time_cursor,
)


class AgentCursorError(ValueError):
    pass


def encode_agent_cursor(*, updated_at: datetime, agent_id: str, scope: dict[str, object]) -> str:
    return encode_time_cursor(updated_at, agent_id, scope=scope, time_field="time", kind="agent")


def decode_agent_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    try:
        return decode_time_cursor(value, scope=scope, id_prefix="ap_", time_field="time", kind="agent")
    except CollectionCursorMismatchError as error:
        raise AgentCursorError("cursor does not match this query") from error
    except InvalidCollectionCursorError as error:
        raise AgentCursorError("invalid cursor") from error


def encode_revision_cursor(*, version: int, revision_id: str, scope: dict[str, object]) -> str:
    return encode_collection_cursor(
        {
            "number": version,
            "id": revision_id,
        },
        kind="revision",
        scope=scope,
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


def _decode(value: str, *, kind: str, scope: dict[str, object]) -> dict[str, object]:
    try:
        return decode_collection_cursor(value, kind=kind, scope=scope)
    except CollectionCursorMismatchError as error:
        raise AgentCursorError("cursor does not match this query") from error
    except InvalidCollectionCursorError as error:
        raise AgentCursorError("invalid cursor") from error
