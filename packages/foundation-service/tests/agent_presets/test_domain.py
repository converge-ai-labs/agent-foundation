from __future__ import annotations

import pytest
from a13n_service.agent_presets.domain import (
    AgentModelConfig,
    AgentRunOverride,
    ConnectorConnectionToolSelection,
    MCPConnectionToolSelection,
    OutputSpec,
    new_agent_preset_id,
    new_agent_preset_revision_id,
)
from pydantic import ValidationError

from .conftest import preset_config


def test_agent_preset_identifiers_are_kind_prefixed_and_unpredictable() -> None:
    preset_ids = {new_agent_preset_id() for _ in range(100)}
    revision_ids = {new_agent_preset_revision_id() for _ in range(100)}

    assert len(preset_ids) == len(revision_ids) == 100
    assert all(value.startswith("ap_") for value in preset_ids)
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
    config = preset_config()

    assert config.model.settings == {"temperature": 0.2}
    with pytest.raises(ValidationError, match="unsupported ModelSettings"):
        AgentModelConfig.model_validate(
            {
                "model_config_id": config.model.model_config_id,
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
    selection = selection_type.model_validate({id_field: identifier, "tools": ["orders.lookup"], "exposure": "catalog"})

    assert selection.tools == ("orders.lookup",)
    assert selection.exposure == "catalog"
    with pytest.raises(ValidationError, match="tool names must be unique"):
        selection_type.model_validate({id_field: identifier, "tools": ["orders.lookup", "orders.lookup"]})
