"""Safe Alembic server-default comparison helpers."""

from typing import Any

from sqlalchemy import JSON, Column


def compare_server_default(
    _: Any,
    __: Column[Any],
    metadata_column: Column[Any],
    inspected_default: str | None,
    ___: Any,
    rendered_metadata_default: str | None,
) -> bool | None:
    """Compare JSON defaults without invoking PostgreSQL's undefined JSON equality."""

    if not isinstance(metadata_column.type, JSON):
        return None
    if inspected_default is None or rendered_metadata_default is None:
        return inspected_default != rendered_metadata_default
    return _normalize_json_default(inspected_default) != _normalize_json_default(rendered_metadata_default)


def _normalize_json_default(value: str) -> str:
    normalized = value.strip()
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    for suffix in ("::json", "::jsonb"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
            break
    return normalized


__all__ = ["compare_server_default"]
