"""Scope-bound collection positions for Environment resources."""

from collections.abc import Mapping

from a13n_service.collection_cursors import decode_collection_cursor, encode_collection_cursor

from .errors import invalid_environment


def encode_cursor(position: str, *, scope: Mapping[str, object]) -> str:
    return encode_collection_cursor({"position": position}, kind="environment_resources", scope=dict(scope))


def decode_cursor(value: str, *, scope: Mapping[str, object]) -> str:
    try:
        payload = decode_collection_cursor(value, kind="environment_resources", scope=dict(scope))
        position = payload.get("position")
        if not isinstance(position, str) or not position:
            raise ValueError("invalid position")
        return position
    except ValueError as error:
        raise invalid_environment("Cursor is invalid or belongs to another collection") from error
