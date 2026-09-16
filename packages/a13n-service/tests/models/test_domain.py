from __future__ import annotations

import pytest
from a13n_service.models.domain import (
    CreateModelRequest,
    ModelDeclarations,
    ModelExecutionSnapshot,
    new_model_provider_id,
)
from pydantic import ValidationError


def test_model_key_is_normalized_and_one_api_is_required() -> None:
    request = CreateModelRequest.model_validate(
        {
            "key": " Team/GPT ",
            "provider_id": "mprov_1234567890abcdef",
            "name": "Team GPT",
            "upstream_model": "gpt-next",
            "model_api": "openai.responses",
        }
    )

    assert request.key == "team/gpt"
    with pytest.raises(ValidationError, match="Extra inputs"):
        CreateModelRequest.model_validate(
            request.model_dump(mode="python")
            | {"model_apis": [{"api": "openai.responses"}, {"api": "openai.responses"}]}
        )


def test_execution_snapshot_contains_only_request_selection_fields() -> None:
    fields = set(ModelExecutionSnapshot.model_fields)

    assert fields == {
        "schema_version",
        "model_id",
        "model_key",
        "upstream_model",
        "catalog_ref",
        "model_api",
        "pricing",
    }
    assert new_model_provider_id().startswith("mprov_")


def test_model_declarations_have_one_consistent_typed_default_shape() -> None:
    declarations = ModelDeclarations()
    assert declarations.model_dump(mode="json") == {
        "supports_tools": None,
        "capabilities": [],
        "context_window_tokens": None,
        "structured_output": None,
        "pricing": None,
    }
    with pytest.raises(ValidationError):
        ModelDeclarations(context_window_tokens=0)
    with pytest.raises(ValidationError):
        ModelDeclarations.model_validate({"thinking_efforts": ["high"]})
