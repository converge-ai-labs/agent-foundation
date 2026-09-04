"""Provider-neutral default mapping for one bounded event batch."""

from a13n_service.connectivity.domain import JsonObject


def default_event_mapping() -> JsonObject:
    return {
        "op": "object",
        "fields": {
            "schema_version": {"op": "static", "value": "2"},
            "content": {"op": "static", "value": []},
            "structured_content": {
                "op": "object",
                "fields": {"events": {"op": "select", "path": ["events"]}},
            },
        },
    }
