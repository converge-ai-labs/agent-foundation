import pytest
from a13n_service.connectivity.ingress.input import project_events
from a13n_service.connectivity.ingress.provider import InboundEvent


def event(text):
    return {
        "type": "message",
        "occurred_at": "2026-09-03T00:00:00Z",
        "text": text,
        "actor": {"name": "Ada"},
        "context": {"channel": "support"},
        "data": {"priority": 2},
    }


def test_fixed_projection_preserves_order_content_and_canonical_schema():
    result = project_events((event("first"), event("second")), max_bytes=4096)
    assert result.schema_version == "2"
    assert result.content == ()
    assert [value["text"] for value in result.structured_content["events"]] == ["first", "second"]
    assert result.structured_content["events"][0]["data"] == {"priority": 2}
    assert "retain_raw" not in InboundEvent.model_fields


def test_projection_rejects_invalid_and_oversized_input():
    with pytest.raises(ValueError):
        project_events(({**event("hello"), "occurred_at": "invalid"},), max_bytes=4096)
    with pytest.raises(ValueError, match="input_too_large"):
        project_events((event("x" * 100),), max_bytes=50)
