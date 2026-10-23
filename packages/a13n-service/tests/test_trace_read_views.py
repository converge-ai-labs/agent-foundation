"""Trace summary pages keep backend positions, filters, redaction and content authorization."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
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
    # Every read selects the one span, so no neighbour's content is fetched.
    assert all((query.span_id, query.limit) == (backend.values[0].id, 1) for query in backend.queries)
    refused = await service.client.get(path, headers={"X-Workspace-ID": "ws_" + "f" * 20})
    assert refused.status_code in {403, 404}


async def test_overview_preserves_display_fields_without_payloads_or_compact_truncation(service) -> None:  # type: ignore[no-untyped-def]
    own = correlation_attributes(service.tenant.organization_id, service.tenant.workspace_id)
    value = span(1, own).model_copy(
        update={"name": "long name " * 100, "model": "long model " * 100, "cost_usd": Decimal("0.123456789")}
    )
    backend = Backend([span(0, {**own, "a13n.observation.metadata.workspace_id": "ws_other"}), value, span(2, own)])
    service.app.state.runtime = replace(service.runtime, traces=backend)
    path = service.api + f"/traces/{TRACE}/spans"
    response = await service.client.get(path, params={"view": "overview", "limit": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    (item,) = body["items"]
    assert item == {
        "view": "overview",
        "trace_id": TRACE,
        "id": value.id,
        "parent_id": value.parent_id,
        "name": value.name,
        "model": value.model,
        "kind": value.kind,
        "status": value.status,
        "level": value.level,
        "started_at": value.model_dump(mode="json")["started_at"],
        "ended_at": None,
        "cost_usd": "0.123456789",
    }
    assert len(response.content) < 4000
    assert not backend.queries[0].include_content
    cursor = body["next_cursor"]
    assert cursor
    for view in ("detailed", "compact"):
        refused = await service.client.get(path, params={"view": view, "cursor": cursor})
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_cursor"
    following = await service.client.get(path, params={"view": "overview", "cursor": cursor, "limit": 2})
    assert [item["id"] for item in following.json()["items"]] == [f"{2:016x}"]
    assert len({(query.started_after, query.started_before) for query in backend.queries}) == 1


async def test_individual_span_preserves_full_redacted_content_and_scopes_before_lookup(service) -> None:  # type: ignore[no-untyped-def]
    own = correlation_attributes(service.tenant.organization_id, service.tenant.workspace_id)
    value = span(1, own)
    backend = Backend([value])
    service.app.state.runtime = replace(service.runtime, traces=backend)
    path = service.api + f"/traces/{TRACE}/spans/{value.id}"
    response = await service.client.get(path)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["input"] == {"text": "证据" * 7000, "api_key": "[REDACTED]"}
    assert body["status_message"] == value.status_message
    assert body["output"] == value.output and body["attributes"] == own
    assert backend.queries[0].span_id == value.id and backend.queries[0].trace_id == TRACE
    assert backend.queries[0].include_content and backend.queries[0].limit == 1
    forbidden = await service.client.get(path, headers={"X-Workspace-ID": "ws_" + "f" * 20})
    assert forbidden.status_code in {403, 404} and len(backend.queries) == 1


@pytest.mark.parametrize("mismatch", ["id", "trace_id", "workspace", "missing"])
async def test_individual_span_does_not_trust_backend_selection(service, mismatch) -> None:  # type: ignore[no-untyped-def]
    own = correlation_attributes(service.tenant.organization_id, service.tenant.workspace_id)
    value = span(1, own)
    if mismatch == "workspace":
        value = value.model_copy(
            update={"attributes": correlation_attributes(service.tenant.organization_id, "ws_other")}
        )
    elif mismatch != "missing":
        value = value.model_copy(update={mismatch: "f" * (16 if mismatch == "id" else 32)})
    backend = Backend([] if mismatch == "missing" else [value])
    service.app.state.runtime = replace(service.runtime, traces=backend)
    response = await service.client.get(service.api + f"/traces/{TRACE}/spans/{1:016x}")
    assert response.status_code == 404, response.text
