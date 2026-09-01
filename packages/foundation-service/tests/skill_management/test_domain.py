import unicodedata

import pytest
from a13n_service.skill_management.cursors import (
    SkillCursorError,
    decode_revision_cursor,
    decode_skill_cursor,
    encode_revision_cursor,
    encode_skill_cursor,
)
from a13n_service.skill_management.domain import (
    CreateSkillRequest,
    FoundationAgentSkillSelection,
    FoundationAgentSkillSelectionRequest,
    FoundationSkillRevisionLock,
    RunSkillSelectionRequest,
    UpdateSkillRequest,
)
from pydantic import ValidationError

WORKSPACE_ID = "ws_1234567890abcdef"


def test_display_name_normalizes_nfc_without_trimming_caller_input() -> None:
    decomposed = unicodedata.normalize("NFD", "Café")

    request = CreateSkillRequest(
        display_name=decomposed,
        source={"kind": "zip_upload", "upload_id": "sku_1234567890abcdef"},
    )

    assert request.display_name == "Café"
    for invalid in (" leading", "trailing ", "control\x00", "\ud800"):
        with pytest.raises(ValidationError):
            UpdateSkillRequest(expected_version=1, display_name=invalid)


def test_source_union_rejects_unknown_fields_and_malformed_ids() -> None:
    with pytest.raises(ValidationError):
        CreateSkillRequest.model_validate(
            {
                "display_name": "Deploy",
                "source": {
                    "kind": "zip_upload",
                    "upload_id": "sku_1234567890abcdef",
                    "object_key": "caller-selected",
                },
            }
        )
    with pytest.raises(ValidationError):
        CreateSkillRequest.model_validate(
            {"display_name": "Deploy", "source": {"kind": "zip_upload", "upload_id": "invalid"}}
        )


def test_skill_and_revision_cursors_are_query_bound() -> None:
    skill_scope = {"workspace_id": WORKSPACE_ID, "principal_id": "usr_1234567890abcdef"}
    skill_cursor = encode_skill_cursor(
        display_name="Deploy",
        skill_id="sk_1234567890abcdef",
        scope=skill_scope,
    )
    assert decode_skill_cursor(skill_cursor, scope=skill_scope) == ("Deploy", "sk_1234567890abcdef")
    with pytest.raises(SkillCursorError):
        decode_skill_cursor(skill_cursor, scope={"workspace_id": "ws_abcdef1234567890"})

    revision_scope = {**skill_scope, "skill_id": "sk_1234567890abcdef"}
    revision_cursor = encode_revision_cursor(
        revision_number=3,
        revision_id="skr_1234567890abcdef",
        scope=revision_scope,
    )
    assert decode_revision_cursor(revision_cursor, scope=revision_scope) == (3, "skr_1234567890abcdef")
    with pytest.raises(SkillCursorError):
        decode_revision_cursor("not-base64", scope=revision_scope)


def test_agent_skill_selection_request_enforces_mount_and_defaults() -> None:
    revision_id = "skr_1234567890abcdef"
    request = FoundationAgentSkillSelectionRequest(
        available_revision_ids=(revision_id,),
        materialization_mount="workspace",
        default_mode="exact",
        default_names=("deploy",),
    )
    assert request.available_revision_ids == (revision_id,)

    invalid_values = (
        {
            "available_revision_ids": (revision_id,),
            "materialization_mount": None,
            "default_mode": "all",
            "default_names": (),
        },
        {
            "available_revision_ids": (revision_id, revision_id),
            "materialization_mount": "workspace",
            "default_mode": "all",
            "default_names": (),
        },
        {
            "available_revision_ids": (),
            "materialization_mount": None,
            "default_mode": "exact",
            "default_names": ("deploy",),
        },
        {
            "available_revision_ids": (revision_id,),
            "materialization_mount": "workspace",
            "default_mode": "all",
            "default_names": ("deploy",),
        },
    )
    for value in invalid_values:
        with pytest.raises(ValidationError):
            FoundationAgentSkillSelectionRequest.model_validate(value)


def test_resolved_agent_selection_is_canonical_and_run_null_is_invalid() -> None:
    deploy = FoundationSkillRevisionLock(
        skill_revision_id="skr_1234567890abcdef",
        skill_name="deploy",
        content_digest="1" * 64,
    )
    review = FoundationSkillRevisionLock(
        skill_revision_id="skr_abcdef1234567890",
        skill_name="review",
        content_digest="2" * 64,
    )
    selection = FoundationAgentSkillSelection(
        available=(deploy, review),
        materialization_mount="workspace",
        default_mode="exact",
        default_names=("review",),
    )
    assert selection.default_names == ("review",)

    with pytest.raises(ValidationError):
        FoundationAgentSkillSelection(
            available=(deploy, review),
            materialization_mount="workspace",
            default_mode="exact",
            default_names=("review", "deploy"),
        )
    with pytest.raises(ValidationError):
        RunSkillSelectionRequest.model_validate({"selected_skill_names": None})
    with pytest.raises(ValidationError):
        RunSkillSelectionRequest(selected_skill_names=("deploy", "deploy"))
