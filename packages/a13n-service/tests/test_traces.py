"""Trace export and query: attempt correlation on exported spans, authorization before any lookup, the
post-fetch attribute filter, selectors, redaction, the backend descriptor and backend failures, against fake
Langfuse and Logfire backends."""

import base64
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from urllib.parse import urljoin

import httpx2
import pytest
from a13n_harness import HarnessInstrumentation, HarnessTraceContent
from a13n_service.infra import cursors
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.telemetry import correlation_attributes, open_instrumentation
from a13n_service.providers.traces import MAX_RESPONSE_BYTES, TraceProvider
from a13n_service.providers.traces.langfuse import Langfuse
from a13n_service.providers.traces.logfire import Logfire
from a13n_service.runs import traces
from a13n_service.runs.claim import claim, start
from a13n_service.runs.schemas import NewThread
from a13n_service.runs.submit import create_thread
from a13n_service.settings import Telemetry
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from a13n_service.tenancy.tables import WorkspaceRow
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

pytestmark = pytest.mark.anyio

TRACE = "0af7651916cd43dd8448eb211c80319c"
THREAD, SESSION, OTHER_SESSION = "thread_" + "1" * 32, "sess_" + "1" * 24, "sess_" + "2" * 24
ATTEMPT = "a13n.observation.metadata.run_attempt_id"
ORGANIZATION = "a13n.observation.metadata.organization_id"
# Answers a request only after the querying client's deadline has passed.
SLOW = "slow"
# Closes the connection without answering.
DROP = "drop"


class Backend:
    """A fake trace backend on a loopback port: it records every request and answers each with the next queued
    answer, or an empty 200 when none is queued."""

    def __init__(self, url: str):
        self.url, self.answers, self.requests = url, list[httpx2.Response | str](), list[httpx2.Request]()

    def answer(self, *answers: httpx2.Response | str) -> "Backend":
        self.answers.extend(answers)
        return self

    def logfire(self, *, timeout: float = 5) -> Logfire:
        return Logfire(self.url, "write-token", "read-token", timeout)

    def langfuse(self) -> Langfuse:
        return Langfuse(self.url, "pk-lf-1", "sk-lf-1", 5)

    def respond(self, handler: BaseHTTPRequestHandler) -> None:
        body = handler.rfile.read(int(handler.headers.get("content-length", "0")))
        self.requests.append(
            httpx2.Request(
                handler.command, urljoin(self.url, handler.path), headers=handler.headers.items(), content=body
            )
        )
        answer = self.answers.pop(0) if self.answers else httpx2.Response(200)
        if answer == DROP:
            return
        if answer == SLOW:
            time.sleep(0.5)
            answer = httpx2.Response(200, json={"data": []})
        assert isinstance(answer, httpx2.Response)
        handler.send_response(answer.status_code)
        for name, value in answer.headers.items():
            handler.send_header(name, value)
        handler.end_headers()
        try:
            handler.wfile.write(answer.content)
        except OSError:
            pass  # the client stopped reading at its byte limit


@pytest.fixture
def backend() -> Iterator[Backend]:
    fake: Backend

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            fake.respond(self)

        do_POST = do_GET

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake = Backend(f"http://127.0.0.1:{server.server_port}")
    Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()


def logfire_row(span_id: str, attributes: dict, *, parent: str | None = None, start: str = "") -> dict:
    started = start or "2026-09-23T10:00:00.123456Z"
    return {
        "trace_id": TRACE,
        "span_id": span_id,
        "parent_span_id": parent,
        "span_name": "harness.run" if parent is None else "chat gpt-5.5",
        "start_timestamp": started,
        "end_timestamp": started,
        "otel_status_code": "UNSET",
        "otel_status_message": None,
        "attributes": attributes,
    }


def langfuse_row(observation_id: str, metadata: dict, *, trace_id: str = TRACE, parent: str | None = None) -> dict:
    return {
        "id": observation_id,
        "traceId": trace_id,
        "parentObservationId": parent,
        "type": "AGENT" if parent is None else "GENERATION",
        "name": "harness.run",
        "startTime": "2026-09-23T10:00:00.000Z",
        "endTime": "2026-09-23T10:00:02.000Z",
        "level": "DEFAULT" if parent is None else "ERROR",
        "statusMessage": None,
        "providedModelName": None if parent is None else "gpt-5.5",
        "usageDetails": {"input": 3, "output": 5},
        "totalCost": 0.0012,
        "input": json.dumps({"prompt": "hi"}),
        "output": None,
        "metadata": metadata,
    }


def admin(service: SimpleNamespace) -> Principal:
    return Principal(
        service.tenant.principal_id, "user", (Grant(service.tenant.organization_id, None, BUILT_IN_ROLES["admin"]),)
    )


def scope(service: SimpleNamespace, workspace_id: str | None = None, **identities: str) -> dict[str, str]:
    return correlation_attributes(
        service.tenant.organization_id, workspace_id or service.tenant.workspace_id, **identities
    )


async def post(service: SimpleNamespace, path: str, body: dict) -> dict:
    response = await service.client.post(path, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def started_attempt(service: SimpleNamespace) -> tuple[str, str]:
    """A run taken through submission, claim and start, the path the worker takes."""
    provider = await post(
        service,
        f"{service.api}/model-providers",
        {"type": "openai", "name": "OpenAI", "credential": {"api_key": "sk-model"}},
    )
    model = await post(
        service,
        f"{service.api}/models",
        {
            "provider_id": provider["id"],
            "key": "gpt",
            "name": "GPT",
            "config": {"model_name": "gpt-5.5", "model_api": "openai.responses"},
        },
    )
    agent = await post(service, f"{service.api}/agents", {"name": "Traced", "config": {"model": model["key"]}})
    message = NewThread.model_validate(
        {"payload": {"content": [{"type": "text", "text": "hi"}]}, "agent_id": agent["id"]}
    )
    await create_thread(service.runtime, admin(service), service.tenant.workspace_id, message, request_key="trace-1")
    [lease] = await claim(service.runtime, worker_id="worker", worker_build="test", limit=1)
    await start(service.runtime, lease, harness_run_id="harness-run-1")
    return lease.run_id, lease.attempt_id


async def add_workspace(service: SimpleNamespace) -> str:
    workspace_id = new_object_id("ws")
    async with transaction(service.runtime.storage) as session:
        session.add(WorkspaceRow(id=workspace_id, organization_id=service.tenant.organization_id, name="Other"))
    return workspace_id


def query_through(api: SimpleNamespace, provider: TraceProvider | None) -> None:
    """Send the API's trace queries to `provider`."""
    api.app.state.runtime = replace(api.runtime, traces=provider)


@pytest.fixture
def api(service: SimpleNamespace) -> SimpleNamespace:
    query_through(service, None)
    return service


async def test_every_span_an_attempt_exports_carries_its_correlation(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    traced = replace(service.runtime, instrumentation=HarnessInstrumentation(tracer_provider=provider))
    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.say("done")
    run_id = (await runs_kit.start_thread(service, agent, "hello"))["run"]["id"]
    await (await runs_kit.attempt(service, runtime=traced))
    run = await runs_kit.sealed(service, run_id)
    [attempt] = (await service.client.get(f"{service.api}/runs/{run_id}/attempts")).json()["items"]

    expected = scope(
        service,
        session_id=run["session_id"],
        thread_id=run["thread_id"],
        run_id=run_id,
        run_attempt_id=attempt["id"],
    )
    spans = exporter.get_finished_spans()
    assert len(spans) > 1
    for span in spans:
        assert expected.items() <= dict(span.attributes or {}).items(), span.name


@pytest.mark.parametrize(
    ("configure", "path", "authorization"),
    [
        (Backend.langfuse, "/api/public/otel/v1/traces", "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-1").decode()),
        (Backend.logfire, "/v1/traces", "write-token"),
    ],
)
async def test_tracing_exports_to_the_configured_backend(
    backend: Backend, configure: Callable[[Backend], TraceProvider], path: str, authorization: str
) -> None:
    async with open_instrumentation(
        configure(backend), metered=False, content=HarnessTraceContent.NONE
    ) as instrumentation:
        assert instrumentation is not None and instrumentation.trace_content is HarnessTraceContent.NONE
        with instrumentation.get_tracer("test").start_as_current_span("probe"):
            pass
    # Exit flushes the queued span.
    assert [(request.url.path, request.headers.get("authorization")) for request in backend.requests] == [
        (path, authorization)
    ]

    async with open_instrumentation(None, metered=False, content=HarnessTraceContent.STANDARD) as disabled:
        assert disabled is None


def test_telemetry_settings_select_one_complete_backend() -> None:
    assert Telemetry().trace_config() is None
    with pytest.raises(ValidationError):
        Telemetry.model_validate({"trace_backend": "langfuse", "trace_url": "https://cloud.langfuse.com"})
    with pytest.raises(ValidationError):
        Telemetry.model_validate({"trace_backend": "logfire", "trace_url": "https://user:pw@logfire.test"})
    selected = Telemetry.model_validate(
        {
            "trace_backend": "langfuse",
            "trace_url": "https://cloud.langfuse.com/",
            "langfuse_public_key": "pk-lf-1",
            "langfuse_secret_key": "sk-lf-1",
        }
    ).trace_config()
    assert selected == Langfuse("https://cloud.langfuse.com", "pk-lf-1", "sk-lf-1", 10)
    assert "sk-lf-1" not in repr(selected)


async def test_attempt_trace_returns_only_the_attempts_spans_redacted(api: SimpleNamespace, backend: Backend) -> None:
    run_id, attempt_id = await started_attempt(api)
    own = scope(api, run_id=run_id, run_attempt_id=attempt_id)
    rows = [
        logfire_row("a" * 16, {**own, "a13n.input": json.dumps({"prompt": "hi", "api_key": "sk-live"})}),
        logfire_row(
            "b" * 16,
            {
                **own,
                "langfuse.observation.type": "generation",
                "gen_ai.response.model": "gpt-5.5",
                "gen_ai.usage.input_tokens": 12,
                "http.request.header.authorization": "Bearer abc",
                "a13n.output": json.dumps("use Bearer xyz now"),
            },
            parent="a" * 16,
        ),
        logfire_row("c" * 16, {**own, ORGANIZATION: "org_foreign"}),
        logfire_row("d" * 16, {**own, ATTEMPT: "rat_other"}),
        logfire_row("e" * 16, {key: value for key, value in own.items() if key != ATTEMPT}),
    ]
    query_through(api, backend.answer(httpx2.Response(200, json={"data": rows})).logfire())

    response = await api.client.get(f"{api.api}/runs/{run_id}/attempts/{attempt_id}/trace")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == ["a" * 16, "b" * 16]
    assert body["next_cursor"] is None
    root, child = body["items"]
    assert root["input"] == {"prompt": "hi", "api_key": "[REDACTED]"}
    assert child["attributes"]["http.request.header.authorization"] == "[REDACTED]"
    assert child["output"] == "use Bearer [REDACTED] now"
    assert (child["kind"], child["model"], child["usage"]) == ("generation", "gpt-5.5", {"input_tokens": 12})

    [request] = backend.requests
    assert request.headers["authorization"] == "read-token"
    sql = json.loads(request.content)["sql"]
    assert all(f"attributes->>'{key}' = '{value}'" in sql for key, value in own.items())


async def test_trace_queries_authorize_the_workspace_before_resolving_ids(
    api: SimpleNamespace, backend: Backend
) -> None:
    run_id, attempt_id = await started_attempt(api)
    other = await add_workspace(api)
    provider = backend.logfire()
    storage, workspace = api.runtime.storage, api.tenant.workspace_id
    stranger = Principal("usr_stranger", "user", (Grant(api.tenant.organization_id, other, BUILT_IN_ROLES["admin"]),))

    denied = [
        traces.list_attempt_spans(storage, provider, stranger, workspace, run_id, attempt_id, limit=10, cursor=None),
        traces.list_traces(
            storage,
            provider,
            stranger,
            workspace,
            session_id=None,
            thread_id=None,
            run_id=None,
            attributes=(),
            started_after=None,
            started_before=None,
            limit=10,
            cursor=None,
        ),
        traces.get_trace(storage, provider, stranger, workspace, TRACE),
        traces.list_trace_spans(storage, provider, stranger, workspace, TRACE, limit=10, cursor=None),
        traces.describe_backend(storage, provider, stranger, workspace),
    ]
    for call in denied:
        with pytest.raises(ServiceError) as error:
            await call
        assert error.value.code == "forbidden"

    # The run exists, but not in the workspace the caller named and may read.
    with pytest.raises(ServiceError) as error:
        await traces.list_attempt_spans(storage, provider, admin(api), other, run_id, attempt_id, limit=10, cursor=None)
    assert (error.value.code, error.value.details["kind"]) == ("not_found", "run")
    with pytest.raises(ServiceError) as error:
        await traces.list_attempt_spans(
            storage, provider, admin(api), workspace, run_id, "rat_x", limit=10, cursor=None
        )
    assert (error.value.code, error.value.details["kind"]) == ("not_found", "attempt")
    assert backend.requests == []


async def test_workspace_traces_page_through_langfuse_within_scope(api: SimpleNamespace, backend: Backend) -> None:
    own, foreign = scope(api), scope(api, "ws_foreign")
    root = langfuse_row("obs-root", {"attributes": {**own, "a13n.observation.session.id": THREAD}})
    # Langfuse may also flatten attribute keys into the metadata object.
    child = langfuse_row("obs-child", {f"attributes.{key}": value for key, value in own.items()}, parent="obs-root")
    backend.answer(
        httpx2.Response(
            200, json={"data": [root, langfuse_row("obs-x", {"attributes": foreign})], "meta": {"cursor": "p2"}}
        ),
        httpx2.Response(200, json={"data": [], "meta": {}}),
        httpx2.Response(200, json={"data": [root], "meta": {}}),
        httpx2.Response(200, json={"data": [], "meta": {}}),
        httpx2.Response(200, json={"data": [root, child, langfuse_row("obs-y", {"attributes": foreign})], "meta": {}}),
    )
    query_through(api, backend.langfuse())
    listing = f"{api.api}/traces"

    first = (await api.client.get(listing, params={"thread_id": THREAD})).json()
    assert [item["id"] for item in first["items"]] == ["obs-root"]
    assert (first["items"][0]["kind"], first["items"][0]["input"]) == ("agent", {"prompt": "hi"})
    request = backend.requests[0]
    assert request.headers["authorization"] == backend.langfuse().otlp_headers["Authorization"]
    filters = json.loads(request.url.params["filter"])
    assert {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True} in filters
    matched = {item["key"]: item["value"] for item in filters if item["type"] == "stringObject"}
    assert matched == {f"attributes.{key}": value for key, value in scope(api, thread_id=THREAD).items()}

    second = (await api.client.get(listing, params={"thread_id": THREAD, "cursor": first["next_cursor"]})).json()
    assert second == {"items": [], "next_cursor": None}
    assert backend.requests[1].url.params["cursor"] == "p2"
    assert json.loads(backend.requests[1].url.params["filter"]) == filters

    # A cursor is bound to the query it continues.
    reused = await api.client.get(listing, params={"cursor": first["next_cursor"]})
    assert (reused.status_code, reused.json()["error"]["code"]) == (400, "invalid_cursor")
    window = await api.client.get(
        listing, params={"started_after": "2026-09-23T10:00:00Z", "started_before": "2026-09-01T00:00:00Z"}
    )
    assert window.status_code == 400
    assert len(backend.requests) == 2

    trace = await api.client.get(f"{api.api}/traces/{TRACE}")
    assert (trace.status_code, trace.json()["id"]) == (200, "obs-root")
    assert backend.requests[2].url.params["traceId"] == TRACE
    missing = await api.client.get(f"{api.api}/traces/{'f' * 32}")
    assert (missing.status_code, missing.json()["error"]["details"]["kind"]) == (404, "trace")
    spans = (await api.client.get(f"{api.api}/traces/{TRACE}/spans")).json()
    assert [item["id"] for item in spans["items"]] == ["obs-root", "obs-child"]
    assert (spans["items"][1]["status"], spans["items"][1]["model"]) == ("error", "gpt-5.5")
    assert (await api.client.get(f"{api.api}/traces/not-a-trace")).status_code == 400


async def test_workspace_traces_select_a_session_and_root_attributes(api: SimpleNamespace, backend: Backend) -> None:
    selected = {"gen_ai.agent.name": "support", "deployment": "blue:green"}
    own = scope(api, session_id=SESSION)
    rows = [
        langfuse_row("obs-a", {"attributes": {**own, **selected}}),
        langfuse_row("obs-b", {"attributes": {**own, "gen_ai.agent.name": "support"}}),
        langfuse_row("obs-c", {"attributes": {**scope(api, session_id=OTHER_SESSION), **selected}}),
    ]
    backend.answer(httpx2.Response(200, json={"data": rows, "meta": {"cursor": "p2"}}))
    query_through(api, backend.langfuse())
    listing = f"{api.api}/traces"
    params = {"session_id": SESSION, "attribute": ["gen_ai.agent.name:support", "deployment:blue:green"]}

    response = await api.client.get(listing, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == ["obs-a"]
    filters = json.loads(backend.requests[0].url.params["filter"])
    matched = {item["key"]: item["value"] for item in filters if item["type"] == "stringObject"}
    assert matched == {f"attributes.{key}": value for key, value in {**selected, **own}.items()}

    # A cursor continues only the selection and window it was issued for.
    narrower = {**params, "attribute": ["gen_ai.agent.name:support"], "cursor": body["next_cursor"]}
    reused = await api.client.get(listing, params=narrower)
    assert (reused.status_code, reused.json()["error"]["code"]) == (400, "invalid_cursor")
    later = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    moved = await api.client.get(listing, params={**params, "started_after": later, "cursor": body["next_cursor"]})
    assert (moved.status_code, moved.json()["error"]["code"]) == (400, "invalid_cursor")
    assert len(backend.requests) == 1


async def test_numeric_and_boolean_attributes_match_as_the_backend_selected_them(
    api: SimpleNamespace, backend: Backend
) -> None:
    own = scope(api)
    rows = [
        logfire_row("a" * 16, {**own, "http.status_code": 200, "retry.enabled": True}),
        logfire_row("b" * 16, {**own, "http.status_code": "200", "retry.enabled": "true"}),
        logfire_row("c" * 16, {**own, "http.status_code": 200.5, "retry.enabled": True}),
        logfire_row("d" * 16, {**own, "http.status_code": 200, "retry.enabled": False}),
        logfire_row("e" * 16, {**own, "http.status_code": [200], "retry.enabled": True}),
    ]
    query_through(api, backend.answer(httpx2.Response(200, json={"data": rows})).logfire())

    response = await api.client.get(
        f"{api.api}/traces", params={"attribute": ["http.status_code:200", "retry.enabled:true"]}
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == ["a" * 16, "b" * 16]
    sql = json.loads(backend.requests[0].content)["sql"]
    assert "attributes->>'http.status_code' = '200'" in sql and "attributes->>'retry.enabled' = 'true'" in sql


async def test_attribute_selectors_never_name_correlation_or_credentials(
    api: SimpleNamespace, backend: Backend
) -> None:
    refused = {
        "count": [f"key{index}:value" for index in range(traces.MAX_ATTRIBUTE_SELECTORS + 1)],
        "separator": ["no-separator"],
        "empty-key": [":value"],
        "key-length": ["k" * 129 + ":value"],
        "value-length": ["key:" + "v" * 257],
        "duplicate": ["key:one", "key:two"],
        "correlation": [f"{ORGANIZATION}:org_foreign"],
        "reserved": ["a13n.thread.id:thread_1"],
        "credential-key": ["http.request.header.authorization:secret"],
        "credential-value": ["note:Bearer abc123"],
    }
    query_through(api, backend.logfire())
    for case, selectors in refused.items():
        response = await api.client.get(f"{api.api}/traces", params={"attribute": selectors})
        assert (response.status_code, response.json()["error"]["details"]["field"]) == (400, "attribute"), case
    assert backend.requests == []


async def test_logfire_spans_carry_level_scope_resource_events_and_links_redacted(
    api: SimpleNamespace, backend: Backend
) -> None:
    link = {"context": {"trace_id": "f" * 32, "span_id": "e" * 16}, "attributes": {"token": "t-1"}}
    row = {
        **logfire_row("a" * 16, scope(api)),
        "level": "warn",
        "otel_resource_attributes": json.dumps({"service.name": "a13n-service", "api_key": "sk-live"}),
        "otel_scope_name": "a13n-harness",
        "otel_scope_version": "1.2.0",
        "otel_events": [
            {
                "event_name": "exception",
                "event_timestamp": "2026-09-23T10:00:00.5Z",
                "attributes": {"exception.message": "refused Bearer abc", "password": "hunter2"},
            }
        ],
        "otel_links": json.dumps([link]),
    }
    backend.answer(httpx2.Response(200, json={"data": [row]}))
    query_through(api, backend.logfire())

    response = await api.client.get(f"{api.api}/traces/{TRACE}")
    assert response.status_code == 200, response.text
    span = response.json()
    assert (span["level"], span["source_url"]) == ("warn", None)
    assert span["scope"] == {"name": "a13n-harness", "version": "1.2.0"}
    assert span["resource_attributes"] == {"service.name": "a13n-service", "api_key": "[REDACTED]"}
    [event] = span["events"]
    assert (event["name"], datetime.fromisoformat(event["timestamp"])) == (
        "exception",
        datetime(2026, 9, 23, 10, 0, 0, 500000, tzinfo=UTC),
    )
    assert event["attributes"] == {"exception.message": "refused Bearer [REDACTED]", "password": "[REDACTED]"}
    assert span["links"] == [{"trace_id": "f" * 32, "span_id": "e" * 16, "attributes": {"token": "[REDACTED]"}}]
    sql = json.loads(backend.requests[0].content)["sql"]
    assert "level_name(level) AS level" in sql and "otel_links" in sql


async def test_langfuse_spans_carry_level_resource_scope_and_a_source_url(
    api: SimpleNamespace, backend: Backend
) -> None:
    metadata = {
        "attributes": scope(api),
        "resourceAttributes": {"service.name": "a13n-service", "client_secret": "s-1"},
        # Langfuse may also flatten a namespace into the metadata object.
        "scope.name": "a13n-harness",
        "scope.version": "1.2.0",
    }
    row = {**langfuse_row("obs-root", metadata), "projectId": "project/1", "level": "WARNING"}
    backend.answer(httpx2.Response(200, json={"data": [row], "meta": {}}))
    query_through(api, backend.langfuse())

    response = await api.client.get(f"{api.api}/traces/{TRACE}")
    assert response.status_code == 200, response.text
    span = response.json()
    assert (span["level"], span["status"]) == ("warning", "ok")
    assert span["resource_attributes"] == {"service.name": "a13n-service", "client_secret": "[REDACTED]"}
    assert span["scope"] == {"name": "a13n-harness", "version": "1.2.0"}
    assert (span["events"], span["links"]) == ([], [])
    # The UI link names the project and trace only; the key pair stays in the query's header.
    assert span["source_url"] == f"{backend.url}/project/project%2F1/traces/{TRACE}"
    assert backend.requests[0].url.params["expandMetadata"] == "attributes,resourceAttributes,scope"


async def test_trace_backend_names_the_configured_backend(api: SimpleNamespace, backend: Backend) -> None:
    unconfigured = await api.client.get(f"{api.api}/trace-backend")
    assert (unconfigured.status_code, unconfigured.json()) == (200, {"type": None, "queryable_since": None})

    query_through(api, backend.langfuse())
    response = await api.client.get(f"{api.api}/trace-backend")
    assert response.status_code == 200, response.text
    described = response.json()
    assert described["type"] == "langfuse"
    since = datetime.fromisoformat(described["queryable_since"])
    assert abs(since - (datetime.now(UTC) - traces.MAX_WINDOW)) < timedelta(minutes=1)
    assert backend.requests == []


async def test_logfire_pages_continue_after_the_last_row(api: SimpleNamespace, backend: Backend) -> None:
    own = scope(api)
    rows = [
        logfire_row("a" * 16, own, start="2026-09-23T10:00:02.000001Z"),
        logfire_row("b" * 16, own, start="2026-09-23T10:00:01.000001Z"),
    ]
    backend.answer(httpx2.Response(200, json={"data": rows}), httpx2.Response(200, json={"data": rows[1:]}))
    provider, storage = backend.logfire(), api.runtime.storage

    first = await traces.list_trace_spans(
        storage, provider, admin(api), api.tenant.workspace_id, TRACE, limit=1, cursor=None
    )
    assert [span.id for span in first.items] == ["a" * 16] and first.next_cursor is not None
    await traces.list_trace_spans(
        storage, provider, admin(api), api.tenant.workspace_id, TRACE, limit=1, cursor=first.next_cursor
    )
    sql = json.loads(backend.requests[1].content)["sql"]
    assert "start_timestamp < '2026-09-23T10:00:02.000001Z' OR" in sql

    # A service cursor whose backend position was altered is refused before any request.
    kind, owner, _, after, before = json.loads(base64.urlsafe_b64decode(first.next_cursor))
    tampered = cursors.encode(kind, owner, json.dumps(["x", "' OR 1=1 --", "y"]), after, before)
    with pytest.raises(ServiceError) as error:
        await traces.list_trace_spans(
            storage, provider, admin(api), api.tenant.workspace_id, TRACE, limit=1, cursor=tampered
        )
    assert error.value.code == "invalid_cursor"
    assert len(backend.requests) == 2


async def test_backend_failures_are_unavailable(api: SimpleNamespace, backend: Backend) -> None:
    failures: dict[str, httpx2.Response | str] = {
        "status": httpx2.Response(500, json={"detail": "down"}),
        "redirect": httpx2.Response(302, headers={"location": "http://127.0.0.1:9/elsewhere"}),
        "connection": DROP,
        "json": httpx2.Response(200, content=b"{"),
        "shape": httpx2.Response(200, json={"rows": []}),
        "row": httpx2.Response(200, json={"data": [{"trace_id": TRACE}]}),
        "size": httpx2.Response(200, content=b"[" + b" " * MAX_RESPONSE_BYTES + b"]"),
    }
    for sent, (case, answer) in enumerate(failures.items(), start=1):
        provider = backend.answer(answer).logfire()
        with pytest.raises(ServiceError) as error:
            await traces.list_trace_spans(
                api.runtime.storage, provider, admin(api), api.tenant.workspace_id, TRACE, limit=10, cursor=None
            )
        assert (error.value.code, error.value.details) == ("unavailable", {"dependency": "trace:logfire"}), case
        # One request each: a failure is never retried.
        assert len(backend.requests) == sent, case


async def test_slow_or_unconfigured_backends_are_unavailable(api: SimpleNamespace, backend: Backend) -> None:
    slow = backend.answer(SLOW).logfire(timeout=0.05)
    for provider, dependency in ((slow, "trace:logfire"), (None, "trace")):
        with pytest.raises(ServiceError) as error:
            await traces.get_trace(api.runtime.storage, provider, admin(api), api.tenant.workspace_id, TRACE)
        assert (error.value.code, error.value.details["dependency"]) == ("unavailable", dependency)

    response = await api.client.get(f"{api.api}/traces")
    assert (response.status_code, response.json()["error"]["details"]) == (503, {"dependency": "trace"})


@pytest.mark.parametrize("provider", ["langfuse", "logfire"])
async def test_trace_queries_use_host_owned_endpoint_and_proxy(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    from a13n_service.providers.traces import SpanQuery
    from a13n_service.settings import Telemetry

    telemetry = Telemetry.model_validate(
        {
            "trace_backend": provider,
            "trace_url": "http://169.254.169.254",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "logfire_write_token": "write-test",
            "logfire_read_token": "read-test",
        }
    )
    configured = telemetry.trace_config()
    assert configured is not None
    monkeypatch.setenv("HTTP_PROXY", backend.url)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    backend.answer(httpx2.Response(200, json={"data": [], "meta": {}}))
    query = SpanQuery(
        attributes={},
        started_after=datetime(2026, 9, 1, tzinfo=UTC),
        started_before=datetime(2026, 9, 2, tzinfo=UTC),
        limit=10,
    )
    page = await configured.query(query)
    assert page.items == []
    assert len(backend.requests) == 1
