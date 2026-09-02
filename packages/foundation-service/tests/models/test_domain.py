from datetime import UTC, datetime

import pytest
from a13n_service.iam import PrincipalRef
from a13n_service.models.domain import (
    ModelCapabilities,
    ModelExecutionSnapshot,
    ModelRevision,
    UpdateModelRequest,
    new_model_id,
    new_model_revision_id,
)
from a13n_service.secrets import InvokingUserSecretCredential, WorkspaceSecretCredential
from pydantic import ValidationError


def revision() -> ModelRevision:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    actor = PrincipalRef(principal_type="user", principal_id="usr_1234567890abcdef")
    return ModelRevision(
        id="mdlr_1234567890abcdef",
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        model_id="mdl_1234567890abcdef",
        version=1,
        provider_type="openai",
        model_name="gpt-5.6-terra",
        base_url="https://api.openai.com/v1",
        credential=WorkspaceSecretCredential(secret_id="sec_1234567890abcdef"),
        provider_config={},
        capabilities=ModelCapabilities(input_modalities=("text", "image"), reasoning=True),
        capability_source="catalog",
        content_digest="0" * 64,
        created_by=actor,
        created_at=now,
    )


def test_model_and_revision_ids_are_prefixed_random_values() -> None:
    model_ids = {new_model_id() for _ in range(100)}
    revision_ids = {new_model_revision_id() for _ in range(100)}

    assert len(model_ids) == len(revision_ids) == 100
    assert all(identifier.startswith("mdl_") for identifier in model_ids)
    assert all(identifier.startswith("mdlr_") for identifier in revision_ids)


def test_model_credential_is_a_closed_discriminated_union() -> None:
    assert InvokingUserSecretCredential(secret_key="openai_api_key").source == "invoking_user_secret"
    with pytest.raises(ValidationError):
        InvokingUserSecretCredential.model_validate({"source": "invoking_user_secret", "secret_key": "Bad Key"})


def test_update_distinguishes_omitted_description_from_explicit_clear() -> None:
    omitted = UpdateModelRequest(name="Renamed")
    cleared = UpdateModelRequest(description=None)

    assert "description" not in omitted.model_fields_set
    assert "description" in cleared.model_fields_set
    with pytest.raises(ValidationError, match="fields cannot be null: enabled"):
        UpdateModelRequest(enabled=None)


def test_execution_snapshot_is_bound_to_exact_revision_and_contains_no_secret_value() -> None:
    model_revision = revision()
    snapshot = ModelExecutionSnapshot.freeze(
        model_revision,
        adapter_key="a13n.model.openai",
        adapter_version="1",
    )

    assert snapshot.model_revision_id == model_revision.id
    assert snapshot.model_id == model_revision.model_id
    assert "secret_id" in snapshot.model_dump_json()
    assert "secret_value" not in snapshot.model_dump_json()
    assert snapshot.observation().model_revision_id == model_revision.id
