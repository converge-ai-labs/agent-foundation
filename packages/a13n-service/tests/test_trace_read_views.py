"""Trace summary pages keep backend positions, filters, redaction and content authorization."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from a13n_service.infra.telemetry import correlation_attributes
from a13n_service.providers.traces import Span, SpanPage, SpanQuery, TraceBackendType

pytestmark = pytest.mark.anyio
TRACE = "1" * 32


class Backend:
    type: ClassVar[TraceBackendType] = "logfire"
    otlp_endpoint = "https://unused.invalid"
    otlp_headers: ClassVar[dict[str, str]] = {}

    def __init__(self, values: list[Span]):
        self.values, self.queries = values, list[SpanQuery]()

    async def query(self, query: SpanQuery) -> SpanPage:
        self.queries.append(query)
        offset = int(query.cursor or 0)
        selected = self.values[offset : offset + query.limit]
        end = offset + len(selected)
        # Deliberately ignores tenant/filter parameters: Service must enforce them itself.
        return SpanPage(items=selected, next_cursor=str(end) if end < len(self.values) else None)


def span(identity: int, attributes: dict[str, str]) -> Span:
    return Span(
        trace_id=TRACE,
        id=f"{identity:016x}",
        parent_id=None,
        name="tool_name",
        kind="tool",
        started_at=datetime.now(UTC),
        ended_at=None,
        status="error",
        status_message="failure " * 1000,
        level="error",
        model="model",
        usage={"input": 10},
        cost_usd=None,
        input={"text": "证据" * 7000, "api_key": "secret"},
        output="output",
        attributes=attributes,
        resource_attributes={},
        scope=None,
        events=[],
        links=[],
        source_url=None,
    )


async def test_compact_trace_budget_never_skips_spans_and_binds_filters(service) -> None:  # type: ignore[no-untyped-def]
    own = correlation_attributes(service.tenant.organization_id, service.tenant.workspace_id)
    other = correlation_attributes(service.tenant.organization_id, "ws_other")
    values = [span(0, other)] + [span(i, own) for i in range(1, 31)]
    backend = Backend(values)
    service.app.state.runtime = replace(service.runtime, traces=backend)
    path = service.api + f"/traces/{TRACE}/spans"
    cursor, found = None, []
    first_cursor = None
    while True:
        params = {"view": "compact", "status": "error", "kind": "tool", "tool_name": "tool_name", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        response = await service.client.get(path, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(response.text) < 12000 and all("attributes" not in item for item in body["items"])
        found.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        first_cursor = first_cursor or cursor
        if cursor is None:
            break
    assert found == [value.id for value in values[1:]]
    assert any(query.limit < 100 for query in backend.queries)
    assert len({(query.started_after, query.started_before) for query in backend.queries}) == 1
    invalid = await service.client.get(path, params={"view": "compact", "cursor": first_cursor, "status": "ok"})
    assert invalid.status_code == 400 and invalid.json()["error"]["code"] == "invalid_cursor"
    empty = await service.client.get(path, params={"view": "compact", "status": "ok", "limit": 5})
    assert empty.status_code == 200 and empty.json()["items"] == [] and empty.json()["next_cursor"]


async def test_selected_span_fragments_are_redacted_versioned_and_authorized(service) -> None:  # type: ignore[no-untyped-def]
    own = correlation_attributes(service.tenant.organization_id, service.tenant.workspace_id)
    backend = Backend([span(1, own)])
    service.app.state.runtime = replace(service.runtime, traces=backend)
    path = service.api + f"/traces/{TRACE}/spans/{backend.values[0].id}/content"
    first = await service.client.get(path, params={"section": "input", "limit": 100})
    assert first.status_code == 200 and "secret" not in first.text, first.text
    fragment = first.json()["items"][0]
    assert fragment["has_more"] and fragment["representation"] == "fragment"
    next_page = await service.client.get(
        path, params={"section": "input", "offset": fragment["next_offset"], "version": fragment["version"]}
    )
    assert next_page.status_code == 200 and "secret" not in next_page.text
    backend.values[0] = backend.values[0].model_copy(update={"input": "changed"})
    changed = await service.client.get(
        path, params={"section": "input", "offset": fragment["next_offset"], "version": fragment["version"]}
    )
    assert changed.status_code == 409 and changed.json()["error"]["details"]["reason"] == "content_changed"
    refused = await service.client.get(path, headers={"X-Workspace-ID": "ws_" + "f" * 20})
    assert refused.status_code in {403, 404}
