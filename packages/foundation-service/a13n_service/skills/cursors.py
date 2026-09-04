"""Opaque query-bound cursors for Skill Management collections."""

from __future__ import annotations

from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)


class SkillCursorError(ValueError):
    pass


def encode_skill_cursor(*, name: str, skill_id: str, scope: dict[str, object]) -> str:
    return encode_collection_cursor(
        {
            "name": name,
            "id": skill_id,
        },
        kind="skills",
        scope=scope,
    )


def decode_skill_cursor(value: str, *, scope: dict[str, object]) -> tuple[str, str]:
    payload = _decode(value, kind="skills", scope=scope)
    name = payload.get("name")
    skill_id = payload.get("id")
    if not isinstance(name, str) or not isinstance(skill_id, str) or not skill_id.startswith("sk_"):
        raise SkillCursorError("invalid cursor")
    return name, skill_id


def encode_revision_cursor(*, version: int, revision_id: str, scope: dict[str, object]) -> str:
    return encode_collection_cursor(
        {
            "version": version,
            "id": revision_id,
        },
        kind="revisions",
        scope=scope,
    )


def decode_revision_cursor(value: str, *, scope: dict[str, object]) -> tuple[int, str]:
    payload = _decode(value, kind="revisions", scope=scope)
    version = payload.get("version")
    revision_id = payload.get("id")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        or not isinstance(revision_id, str)
        or not revision_id.startswith("skr_")
    ):
        raise SkillCursorError("invalid cursor")
    return version, revision_id


def encode_reference_cursor(*, agent_name: str, agent_id: str, scope: dict[str, object]) -> str:
    return encode_collection_cursor(
        {
            "name": agent_name,
            "id": agent_id,
        },
        kind="skill-references",
        scope=scope,
    )


def decode_reference_cursor(value: str, *, scope: dict[str, object]) -> tuple[str, str]:
    payload = _decode(value, kind="skill-references", scope=scope)
    agent_name = payload.get("name")
    agent_id = payload.get("id")
    if not isinstance(agent_name, str) or not isinstance(agent_id, str) or not agent_id.startswith("ap_"):
        raise SkillCursorError("invalid cursor")
    return agent_name, agent_id


def _decode(value: str, *, kind: str, scope: dict[str, object]) -> dict[str, object]:
    try:
        return decode_collection_cursor(value, kind=kind, scope=scope)
    except CollectionCursorMismatchError as error:
        raise SkillCursorError("cursor does not match this query") from error
    except InvalidCollectionCursorError as error:
        raise SkillCursorError("invalid cursor") from error
