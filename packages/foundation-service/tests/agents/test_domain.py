from __future__ import annotations

import pytest
from a13n_service.agents.domain import (
    AgentModel,
    AgentRunOverride,
    ConnectorConnectionToolSelection,
    MCPConnectionToolSelection,
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
                "model_key": config.model.model_key,
                "model_api": config.model.model_api,
                "settings": {"made_up": True},
                "characteristics": {},
            }
        )


@pytest.mark.parametrize(
    ("selection_type", "id_field", "identifier"),
    [
        (ConnectorConnectionToolSelection, "connector_connection_id", "cconn_1234567890abcdef"),
        (MCPConnectionToolSelection, "mcp_connection_id", "mcpc_1234567890abcdef"),
    ],
)
def test_connection_tool_selections_are_bounded_and_unique(selection_type, id_field: str, identifier: str) -> None:
    selection = selection_type.model_validate({id_field: identifier, "tools": ["orders.lookup"], "defer_loading": True})

    assert selection.tools == ("orders.lookup",)
    assert selection.defer_loading is True
    with pytest.raises(ValidationError, match="tool names must be unique"):
        selection_type.model_validate({id_field: identifier, "tools": ["orders.lookup", "orders.lookup"]})
