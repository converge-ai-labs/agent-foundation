from datetime import UTC, datetime

import pytest
from a13n_service.connectivity.ingress.mapping import (
    EventInputBatch,
    EventInputView,
    MappingError,
    compile_mapping,
)


def batch() -> EventInputBatch:
    return EventInputBatch(
        events=(
            EventInputView(
                type="message",
                occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
                text="hello",
                actor={"display_name": "Ada"},
                context={"channel_name": "support"},
                data={"priority": 2},
            ),
        )
    )


def test_mapping_compiles_canonical_ast_and_produces_agent_input() -> None:
    mapping = compile_mapping(
        {
            "op": "object",
            "fields": {
                "structured_content": {
                    "op": "object",
                    "fields": {
                        "priority": {"op": "select", "path": ["events", 0, "data", "priority"]},
                        "missing": {
                            "op": "select",
                            "path": ["events", 0, "data", "missing"],
                            "default": None,
                        },
                    },
                },
                "content": {
                    "op": "array",
                    "items": [
                        {
                            "op": "object",
                            "fields": {
                                "type": {"op": "static", "value": "text"},
                                "text": {"op": "select", "path": ["events", 0, "text"]},
                            },
                        }
                    ],
                },
                "schema_version": {"op": "static", "value": "2"},
            },
        }
    )

    output = mapping.evaluate(batch())

    assert output.content[0].type == "text"
    assert output.structured_content == {"missing": None, "priority": 2}
    assert len(mapping.digest) == 64
    fields = mapping.value["fields"]
    assert isinstance(fields, dict)
    assert tuple(fields) == ("content", "schema_version", "structured_content")


@pytest.mark.parametrize(
    "value",
    [
        {"op": "select", "path": ["raw_ref"]},
        {"op": "select", "path": ["ingress_id"]},
        {"op": "template", "value": "{{ events[0].text }}"},
        {"op": "static", "value": float("nan")},
        {"op": "array", "items": [], "extra": True},
    ],
)
def test_mapping_rejects_protected_dynamic_and_non_json_forms(value: object) -> None:
    with pytest.raises(MappingError):
        compile_mapping(value)


def test_mapping_missing_path_and_invalid_agent_input_fail_deterministically() -> None:
    missing = compile_mapping({"op": "select", "path": ["events", 0, "data", "absent"]})
    invalid_output = compile_mapping({"op": "static", "value": {"schema_version": "99"}})

    with pytest.raises(MappingError, match="missing"):
        missing.evaluate(batch())
    with pytest.raises(MappingError, match="canonical AgentInput"):
        invalid_output.evaluate(batch())
