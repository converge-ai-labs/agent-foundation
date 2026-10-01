"""Validate emitted wire events against the frozen, closed AG-UI 1.0 contract."""

import json
from pathlib import Path

import pytest
from a13n_stream_protocol import HarnessAguiObserver
from jsonschema import Draft202012Validator


@pytest.fixture(scope="session")
def wire_validator() -> Draft202012Validator:
    schema = json.loads((Path(__file__).parent / "fixtures/agui-1.0/schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture(autouse=True)
def validate_emitted_wire(monkeypatch, wire_validator):
    observe = HarnessAguiObserver.observe

    def validated(self, item):
        events = observe(self, item)
        for event in events:
            wire_validator.validate(event.model_dump(mode="json", by_alias=True))
        return events

    monkeypatch.setattr(HarnessAguiObserver, "observe", validated)
