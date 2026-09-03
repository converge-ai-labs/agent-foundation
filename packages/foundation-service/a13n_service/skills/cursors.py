"""Opaque query-bound cursors for Skill Management collections."""

from __future__ import annotations

import base64
import hashlib
import json


class SkillCursorError(ValueError):
    pass


def encode_skill_cursor(*, name: str, skill_id: str, scope: dict[str, object]) -> str:
    return _encode(
        {
            "v": "1",
            "kind": "skills",
            "name": name,
            "id": skill_id,
            "scope": _scope_digest(scope),
        }
    )


def decode_skill_cursor(value: str, *, scope: dict[str, object]) -> tuple[str, str]:
    payload = _decode(value, kind="skills", scope=scope)
    name = payload.get("name")
    skill_id = payload.get("id")
    if not isinstance(name, str) or not isinstance(skill_id, str) or not skill_id.startswith("sk_"):
        raise SkillCursorError("invalid cursor")
    return name, skill_id


def encode_revision_cursor(*, version: int, revision_id: str, scope: dict[str, object]) -> str:
    return _encode(
        {
            "v": "1",
            "kind": "revisions",
            "version": version,
            "id": revision_id,
            "scope": _scope_digest(scope),
        }
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
    return _encode(
        {
            "v": "1",
            "kind": "skill-references",
            "name": agent_name,
            "id": agent_id,
            "scope": _scope_digest(scope),
        }
    )


def decode_reference_cursor(value: str, *, scope: dict[str, object]) -> tuple[str, str]:
    payload = _decode(value, kind="skill-references", scope=scope)
    agent_name = payload.get("name")
    agent_id = payload.get("id")
    if not isinstance(agent_name, str) or not isinstance(agent_id, str) or not agent_id.startswith("ap_"):
        raise SkillCursorError("invalid cursor")
    return agent_name, agent_id


def _encode(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def _decode(value: str, *, kind: str, scope: dict[str, object]) -> dict[str, object]:
    if not value or len(value) > 2048:
        raise SkillCursorError("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise SkillCursorError("invalid cursor") from error
    if (
        not isinstance(payload, dict)
        or payload.get("v") != "1"
        or payload.get("kind") != kind
        or payload.get("scope") != _scope_digest(scope)
    ):
        raise SkillCursorError("cursor does not match this query")
    return payload


def _scope_digest(scope: dict[str, object]) -> str:
    encoded = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()
