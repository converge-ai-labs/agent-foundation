"""Offline coverage of the example's public declaration and matching policy."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from a13n_harness_ui.connectors import ConnectorRule
from a13n_harness_ui.connectors.forms import validate_form
from pydantic import ValidationError

spec = spec_from_file_location("example_connector", Path(__file__).with_name("example_connector.py"))
assert spec is not None and spec.loader is not None
example = module_from_spec(spec)
spec.loader.exec_module(example)
Periodic, matches = example.Periodic, example.matches


def test_declaration_and_configuration_share_validators():
    definition = Periodic()
    declaration = definition.declaration()
    validate_form(declaration.configuration_schema)
    assert declaration.events[0].conditions_schema is not None
    validate_form(declaration.events[0].conditions_schema)
    rules = (ConnectorRule(id="even", event="tick", conditions={"every": 2}),)
    assert definition.validate({"label": "Example"}, rules) == {"label": "Example", "interval_seconds": 60}
    with pytest.raises(ValidationError):
        definition.validate({}, (ConnectorRule(id="invalid", event="tick", conditions={"every": 0}),))


def test_matching_is_plugin_owned_and_respects_disabled_rules():
    rules = (ConnectorRule(id="even", event="tick", conditions={"every": 2}),)
    assert not matches(rules, 1)
    assert matches(rules, 2)
    assert not matches((rules[0].model_copy(update={"enabled": False}),), 2)
    assert not matches((), 2)
