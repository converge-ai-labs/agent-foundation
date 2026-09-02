import unicodedata

import pytest
from a13n_service.skills.cursors import (
    SkillCursorError,
    decode_revision_cursor,
    decode_skill_cursor,
    encode_revision_cursor,
    encode_skill_cursor,
)
from a13n_service.skills.domain import (
    CreateSkillRequest,
    SkillRevisionLock,
    UpdateSkillRequest,
)
from pydantic import ValidationError

WORKSPACE_ID = "ws_1234567890abcdef"


def test_name_normalizes_nfc_without_trimming_caller_input() -> None:
    decomposed = unicodedata.normalize("NFD", "Café")

    request = CreateSkillRequest(
        name=decomposed,
        source={"kind": "zip_upload", "upload_id": "sku_1234567890abcdef"},
    )

    assert request.name == "Café"
    for invalid in (" leading", "trailing ", "control\x00", "\ud800"):
        with pytest.raises(ValidationError):
            UpdateSkillRequest(expected_version=1, name=invalid)


def test_source_union_rejects_unknown_fields_and_malformed_ids() -> None:
    with pytest.raises(ValidationError):
        CreateSkillRequest.model_validate(
            {
                "name": "Deploy",
                "source": {
                    "kind": "zip_upload",
                    "upload_id": "sku_1234567890abcdef",
                    "object_key": "caller-selected",
                },
            }
        )
    with pytest.raises(ValidationError):
        CreateSkillRequest.model_validate({"name": "Deploy", "source": {"kind": "zip_upload", "upload_id": "invalid"}})


def test_skill_and_revision_cursors_are_query_bound() -> None:
    skill_scope = {"workspace_id": WORKSPACE_ID, "principal_id": "usr_1234567890abcdef"}
    skill_cursor = encode_skill_cursor(
        name="Deploy",
        skill_id="sk_1234567890abcdef",
        scope=skill_scope,
    )
    assert decode_skill_cursor(skill_cursor, scope=skill_scope) == ("Deploy", "sk_1234567890abcdef")
    with pytest.raises(SkillCursorError):
        decode_skill_cursor(skill_cursor, scope={"workspace_id": "ws_abcdef1234567890"})

    revision_scope = {**skill_scope, "skill_id": "sk_1234567890abcdef"}
    revision_cursor = encode_revision_cursor(
        version=3,
        revision_id="skr_1234567890abcdef",
        scope=revision_scope,
    )
    assert decode_revision_cursor(revision_cursor, scope=revision_scope) == (3, "skr_1234567890abcdef")
    with pytest.raises(SkillCursorError):
        decode_revision_cursor("not-base64", scope=revision_scope)


def test_skill_revision_lock_validates_exact_identity_and_name() -> None:
    lock = SkillRevisionLock(
        skill_revision_id="skr_1234567890abcdef",
        skill_name="deploy",
        content_digest="1" * 64,
    )
    assert lock.skill_name == "deploy"

    with pytest.raises(ValidationError):
        SkillRevisionLock(
            skill_revision_id="skr_1234567890abcdef",
            skill_name=" ",
            content_digest="1" * 64,
        )
