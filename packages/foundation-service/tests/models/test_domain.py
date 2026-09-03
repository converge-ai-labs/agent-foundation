from __future__ import annotations

import pytest
from a13n_service.models.domain import CreateModelRequest, ModelExecutionSnapshot, new_model_provider_id
from pydantic import ValidationError


def test_model_key_is_normalized_and_apis_are_explicit_and_unique() -> None:
    request = CreateModelRequest.model_validate(
        {
            "key": " Team/GPT ",
            "provider_id": "mprov_1234567890abcdef",
            "name": "Team GPT",
            "upstream_model": "gpt-next",
            "model_apis": [{"api": "openai.responses"}],
        }
    )

    assert request.key == "team/gpt"
    with pytest.raises(ValidationError, match="unique"):
        CreateModelRequest.model_validate(
            request.model_dump(mode="python")
            | {"model_apis": [{"api": "openai.responses"}, {"api": "openai.responses"}]}
        )


def test_execution_snapshot_contains_no_provider_configuration() -> None:
    fields = set(ModelExecutionSnapshot.model_fields)

    assert fields == {"schema_version", "model_id", "model_key", "upstream_model", "model_api", "profile", "limits"}
    assert new_model_provider_id().startswith("mprov_")
