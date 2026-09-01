from datetime import UTC, datetime

import pytest
from a13n_service.model_configs.domain import (
    ModelCapabilities,
    ModelConfig,
    ModelConfigPatch,
    ModelExecutionSnapshot,
    PrincipalRef,
    new_model_config_id,
)
from a13n_service.secrets import InvokingUserSecretCredential, WorkspaceSecretCredential
from pydantic import ValidationError


def resource() -> ModelConfig:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    actor = PrincipalRef(principal_type="user", principal_id="usr_1234567890abcdef")
    return ModelConfig(
        id="mdl_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        version=1,
        name="Primary model",
        description=None,
        provider_type="openai",
        model_name="gpt-5.6-terra",
        base_url="https://api.openai.com/v1",
        credential=WorkspaceSecretCredential(secret_id="sec_1234567890abcdef"),
        provider_config={},
        capabilities=ModelCapabilities(
            input_modalities=("text", "image"),
            context_window_tokens=1_050_000,
            max_output_tokens=128_000,
            tool_calling=True,
            structured_output=True,
            reasoning=True,
        ),
        capability_source="catalog",
        enabled=True,
        created_by=actor,
        updated_by=actor,
        created_at=now,
        updated_at=now,
    )


def test_model_ids_are_prefixed_random_values() -> None:
    identifiers = {new_model_config_id() for _ in range(100)}

    assert len(identifiers) == 100
    assert all(identifier.startswith("mdl_") and len(identifier) == 28 for identifier in identifiers)


def test_model_credential_is_a_closed_discriminated_union() -> None:
    assert InvokingUserSecretCredential(secret_key="openai_api_key").source == "invoking_user_secret"
    with pytest.raises(ValidationError):
        InvokingUserSecretCredential.model_validate({"source": "invoking_user_secret", "secret_key": "Bad Key"})


def test_patch_distinguishes_omitted_description_from_explicit_clear() -> None:
    omitted = ModelConfigPatch(expected_version=1, name="Renamed")
    cleared = ModelConfigPatch(expected_version=1, description=None)

    assert "description" not in omitted.model_fields_set
    assert "description" in cleared.model_fields_set
    with pytest.raises(ValidationError, match="fields cannot be null: enabled"):
        ModelConfigPatch(expected_version=1, enabled=None)


def test_execution_snapshot_is_deterministic_and_contains_no_secret_value() -> None:
    model = resource()
    snapshot = ModelExecutionSnapshot.freeze(
        model,
        adapter_key="a13n.model.openai",
        adapter_version="1",
    )

    assert snapshot == ModelExecutionSnapshot.freeze(
        model,
        adapter_key="a13n.model.openai",
        adapter_version="1",
    )
    assert "secret_id" in snapshot.model_dump_json()
    assert "secret_value" not in snapshot.model_dump_json()
    assert snapshot.observation().model_id == model.id
