"""Compatibility coverage for collection cursor wire envelopes."""

import base64
import json
from collections.abc import Callable
from datetime import datetime

import pytest
from a13n_service.agents.cursors import (
    AgentCursorError,
    decode_agent_cursor,
    encode_agent_cursor,
)
from a13n_service.agents.cursors import (
    decode_revision_cursor as decode_agent_revision_cursor,
)
from a13n_service.agents.cursors import (
    encode_revision_cursor as encode_agent_revision_cursor,
)
from a13n_service.assets.cursors import AssetCursorError, decode_asset_cursor, encode_asset_cursor
from a13n_service.connectivity.cursors import CursorError as ConnectivityCursorError
from a13n_service.connectivity.cursors import decode_cursor as decode_connectivity_cursor
from a13n_service.connectivity.cursors import encode_cursor as encode_connectivity_cursor
from a13n_service.models.cursors import CursorError as ModelCursorError
from a13n_service.models.cursors import decode_model_cursor, encode_model_cursor
from a13n_service.skills.cursors import (
    SkillCursorError,
    decode_reference_cursor,
    decode_skill_cursor,
    encode_reference_cursor,
    encode_skill_cursor,
)
from a13n_service.skills.cursors import (
    decode_revision_cursor as decode_skill_revision_cursor,
)
from a13n_service.skills.cursors import (
    encode_revision_cursor as encode_skill_revision_cursor,
)

_STAMP = datetime.fromisoformat("2024-01-02T03:04:05.123456+05:30")
_UTC_STAMP = datetime.fromisoformat("2024-01-01T21:34:05.123456+00:00")
_SCOPE: dict[str, object] = {
    "workspace_id": "ws_1234567890abcdef",
    "query": "中",
    "enabled": True,
}

type _Decoder = Callable[[str, dict[str, object]], object]
_INVALID_CURSOR = r"^invalid cursor$"
_MISMATCHED_CURSOR = r"^cursor does not match this query$"


def _assert_public_errors(error_type: type[ValueError], decoder: _Decoder, cursor: str) -> None:
    with pytest.raises(error_type, match=_MISMATCHED_CURSOR):
        decoder(cursor, {**_SCOPE, "enabled": False})
    with pytest.raises(error_type, match=_MISMATCHED_CURSOR):
        decoder(_replace_payload_field(cursor, "v", "2"), _SCOPE)
    with pytest.raises(error_type, match=_INVALID_CURSOR):
        decoder("x" * 2049, _SCOPE)


def _replace_payload_field(cursor: str, field: str, value: object) -> str:
    padded = cursor + "=" * (-len(cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    assert isinstance(payload, dict)
    payload[field] = value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def test_agent_cursors_preserve_legacy_wire() -> None:
    agent = (
        "eyJpZCI6ImFwXzEyMzQ1Njc4OTBhYmNkZWYiLCJraW5kIjoiYWdlbnQiLCJzY29wZSI6ImQ3ZjU2OTJhNTMxNjdkN2Yw"
        "YTU0MWVmNDg0OTdmYzE5YWY1Zjg4NTVjZGJkNzdmZjZmYmJkNDU5ZTA5Zjg2NDgiLCJ0aW1lIjoiMjAyNC0wMS0wMVQy"
        "MTozNDowNS4xMjM0NTZaIiwidiI6IjEifQ"
    )
    revision = (
        "eyJpZCI6ImFwcl8xMjM0NTY3ODkwYWJjZGVmIiwia2luZCI6InJldmlzaW9uIiwibnVtYmVyIjo3LCJzY29wZSI6ImQ3"
        "ZjU2OTJhNTMxNjdkN2YwYTU0MWVmNDg0OTdmYzE5YWY1Zjg4NTVjZGJkNzdmZjZmYmJkNDU5ZTA5Zjg2NDgiLCJ2IjoiMSJ9"
    )

    assert encode_agent_cursor(updated_at=_STAMP, agent_id="ap_1234567890abcdef", scope=_SCOPE) == agent
    assert decode_agent_cursor(agent, scope=_SCOPE) == (_UTC_STAMP, "ap_1234567890abcdef")
    assert encode_agent_revision_cursor(version=7, revision_id="apr_1234567890abcdef", scope=_SCOPE) == revision
    assert decode_agent_revision_cursor(revision, scope=_SCOPE) == (7, "apr_1234567890abcdef")
    _assert_public_errors(AgentCursorError, lambda value, scope: decode_agent_cursor(value, scope=scope), agent)
    with pytest.raises(AgentCursorError, match=_MISMATCHED_CURSOR):
        decode_agent_cursor(_replace_payload_field(agent, "kind", "revision"), scope=_SCOPE)
    with pytest.raises(AgentCursorError, match=_INVALID_CURSOR):
        decode_agent_cursor(_replace_payload_field(agent, "id", "wrong"), scope=_SCOPE)
    with pytest.raises(AgentCursorError, match=_INVALID_CURSOR):
        decode_agent_revision_cursor(_replace_payload_field(revision, "number", 0), scope=_SCOPE)


def test_skill_cursors_preserve_legacy_wire() -> None:
    skill = (
        "eyJpZCI6InNrXzEyMzQ1Njc4OTBhYmNkZWYiLCJraW5kIjoic2tpbGxzIiwibmFtZSI6IkRlcGxveSBcdTRlMmQiLCJz"
        "Y29wZSI6ImQ3ZjU2OTJhNTMxNjdkN2YwYTU0MWVmNDg0OTdmYzE5YWY1Zjg4NTVjZGJkNzdmZjZmYmJkNDU5ZTA5Zjg2NDgi"
        "LCJ2IjoiMSJ9"
    )
    revision = (
        "eyJpZCI6InNrcl8xMjM0NTY3ODkwYWJjZGVmIiwia2luZCI6InJldmlzaW9ucyIsInNjb3BlIjoiZDdmNTY5MmE1MzE2"
        "N2Q3ZjBhNTQxZWY0ODQ5N2ZjMTlhZjVmODg1NWNkYmQ3N2ZmNmZiYmQ0NTllMDlmODY0OCIsInYiOiIxIiwidmVyc2lvbiI6N30"
    )
    reference = (
        "eyJpZCI6ImFwXzEyMzQ1Njc4OTBhYmNkZWYiLCJraW5kIjoic2tpbGwtcmVmZXJlbmNlcyIsIm5hbWUiOiJBZ2VudCBc"
        "dTRlMmQiLCJzY29wZSI6ImQ3ZjU2OTJhNTMxNjdkN2YwYTU0MWVmNDg0OTdmYzE5YWY1Zjg4NTVjZGJkNzdmZjZmYmJkNDU5"
        "ZTA5Zjg2NDgiLCJ2IjoiMSJ9"
    )

    assert encode_skill_cursor(name="Deploy 中", skill_id="sk_1234567890abcdef", scope=_SCOPE) == skill
    assert decode_skill_cursor(skill, scope=_SCOPE) == ("Deploy 中", "sk_1234567890abcdef")
    assert encode_skill_revision_cursor(version=7, revision_id="skr_1234567890abcdef", scope=_SCOPE) == revision
    assert decode_skill_revision_cursor(revision, scope=_SCOPE) == (7, "skr_1234567890abcdef")
    assert encode_reference_cursor(agent_name="Agent 中", agent_id="ap_1234567890abcdef", scope=_SCOPE) == reference
    assert decode_reference_cursor(reference, scope=_SCOPE) == ("Agent 中", "ap_1234567890abcdef")
    _assert_public_errors(SkillCursorError, lambda value, scope: decode_skill_cursor(value, scope=scope), skill)
    with pytest.raises(SkillCursorError, match=_MISMATCHED_CURSOR):
        decode_skill_cursor(_replace_payload_field(skill, "kind", "revisions"), scope=_SCOPE)
    with pytest.raises(SkillCursorError, match=_INVALID_CURSOR):
        decode_skill_cursor(_replace_payload_field(skill, "id", "wrong"), scope=_SCOPE)
    with pytest.raises(SkillCursorError, match=_INVALID_CURSOR):
        decode_skill_revision_cursor(_replace_payload_field(revision, "version", True), scope=_SCOPE)
    with pytest.raises(SkillCursorError, match=_INVALID_CURSOR):
        decode_reference_cursor(_replace_payload_field(reference, "name", 1), scope=_SCOPE)


def test_asset_cursor_preserves_legacy_wire() -> None:
    cursor = (
        "eyJjcmVhdGVkX2F0IjoiMjAyNC0wMS0wMVQyMTozNDowNS4xMjM0NTZaIiwiaWQiOiJhc3RfMTIzNDU2Nzg5MGFiY2Rl"
        "ZiIsInNjb3BlIjoiZDdmNTY5MmE1MzE2N2Q3ZjBhNTQxZWY0ODQ5N2ZjMTlhZjVmODg1NWNkYmQ3N2ZmNmZiYmQ0NTllMDlm"
        "ODY0OCIsInYiOiIxIn0"
    )

    assert encode_asset_cursor(created_at=_STAMP, asset_id="ast_1234567890abcdef", scope=_SCOPE) == cursor
    assert decode_asset_cursor(cursor, scope=_SCOPE) == (_UTC_STAMP, "ast_1234567890abcdef")
    _assert_public_errors(AssetCursorError, lambda value, scope: decode_asset_cursor(value, scope=scope), cursor)
    with pytest.raises(AssetCursorError, match=_INVALID_CURSOR):
        decode_asset_cursor(_replace_payload_field(cursor, "id", "wrong"), scope=_SCOPE)


def test_model_cursor_preserves_legacy_wire() -> None:
    cursor = (
        "eyJpZCI6Im1kbF8xMjM0NTY3ODkwYWJjZGVmIiwic2NvcGUiOiJkN2Y1NjkyYTUzMTY3ZDdmMGE1NDFlZjQ4NDk3ZmMx"
        "OWFmNWY4ODU1Y2RiZDc3ZmY2ZmJiZDQ1OWUwOWY4NjQ4IiwidXBkYXRlZF9hdCI6IjIwMjQtMDEtMDFUMjE6MzQ6MDUuMTIz"
        "NDU2WiIsInYiOiIxIn0"
    )

    assert encode_model_cursor(updated_at=_STAMP, item_id="mdl_1234567890abcdef", scope=_SCOPE) == cursor
    assert decode_model_cursor(cursor, scope=_SCOPE) == (_UTC_STAMP, "mdl_1234567890abcdef")
    _assert_public_errors(ModelCursorError, lambda value, scope: decode_model_cursor(value, scope=scope), cursor)
    with pytest.raises(ModelCursorError, match=_INVALID_CURSOR):
        decode_model_cursor(_replace_payload_field(cursor, "id", "wrong"), scope=_SCOPE)


def test_connectivity_cursor_preserves_legacy_wire() -> None:
    cursor = (
        "eyJpZCI6ImluZ18xMjM0NTY3ODkwYWJjZGVmIiwic2NvcGUiOiJkN2Y1NjkyYTUzMTY3ZDdmMGE1NDFlZjQ4NDk3ZmMx"
        "OWFmNWY4ODU1Y2RiZDc3ZmY2ZmJiZDQ1OWUwOWY4NjQ4IiwidXBkYXRlZF9hdCI6IjIwMjQtMDEtMDFUMjE6MzQ6MDUuMTIz"
        "NDU2WiIsInYiOiIxIn0"
    )

    assert encode_connectivity_cursor(updated_at=_STAMP, object_id="ing_1234567890abcdef", scope=_SCOPE) == cursor
    assert decode_connectivity_cursor(cursor, scope=_SCOPE, id_prefix="ing") == (
        _UTC_STAMP,
        "ing_1234567890abcdef",
    )
    _assert_public_errors(
        ConnectivityCursorError,
        lambda value, scope: decode_connectivity_cursor(value, scope=scope, id_prefix="ing"),
        cursor,
    )
    with pytest.raises(ConnectivityCursorError, match=_INVALID_CURSOR):
        decode_connectivity_cursor(cursor, scope=_SCOPE, id_prefix="conn")
