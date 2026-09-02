from __future__ import annotations

import pytest
from a13n_service.agents.domain import (
    AgentModel,
    AgentRunOverride,
    OutputSpec,
    new_agent_id,
    new_agent_revision_id,
)
from pydantic import ValidationError

from .conftest import agent_config


def test_agent_identifiers_are_kind_prefixed_and_unpredictable() -> None:
    agent_ids = {new_agent_id() for _ in range(100)}
    revision_ids = {new_agent_revision_id() for _ in range(100)}

    assert len(agent_ids) == len(revision_ids) == 100
    assert all(value.startswith("ap_") for value in agent_ids)
    assert all(value.startswith("apr_") for value in revision_ids)


def test_run_override_preserves_omitted_and_explicit_null() -> None:
    omitted = AgentRunOverride()
    cleared = AgentRunOverride.model_validate({"environment": None, "output_spec": None})

    assert "environment" not in omitted.model_fields_set
    assert {"environment", "output_spec"} <= cleared.model_fields_set


def test_output_spec_requires_one_schema_or_multiple_variants() -> None:
    assert OutputSpec.model_validate({"schema": {"type": "object"}}).schema_ == {"type": "object"}
    with pytest.raises(ValidationError, match="at least two"):
        OutputSpec.model_validate({"variants": [{"name": "only", "schema": {"type": "object"}, "resources": {}}]})


def test_model_settings_are_limited_to_native_pydantic_ai_fields() -> None:
    config = agent_config()

    assert config.model.settings == {"temperature": 0.2}
    with pytest.raises(ValidationError, match="unsupported ModelSettings"):
        AgentModel.model_validate(
            {
                "model_revision_id": config.model.model_revision_id,
                "settings": {"made_up": True},
                "characteristics": {},
            }
        )
