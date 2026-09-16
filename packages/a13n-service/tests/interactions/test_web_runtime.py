import json
import socket

import httpx2
import pytest
from a13n_harness import AgentDefinition, AgentSpec, EnvironmentAccess, EnvironmentMount, HarnessBuilder, RunBindings
from a13n_harness.errors import RunError
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
from a13n_service.agents.domain import AgentConfig, EffectiveAgentConfig
from a13n_service.agents.models import AgentRecord
from a13n_service.iam.models import UserRecord
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.provider_plugins import load_provider_catalogs
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import short_session, transaction
from a13n_service.web.domain import (
    CreateWebProviderRequest,
    DownloadSelection,
    FetchSelection,
    ScrapeSelection,
    SearchSelection,
    WebSelection,
)
from a13n_service.web.models import WebProviderRecord
from a13n_service.web.registry import WebProviderRegistry, built_in_web_provider_registry
from a13n_service.web.runtime import WebRuntime, web_capability
from a13n_service.web.service import WebProviderService
from a13n_service.web.web import WebTransport
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tests.hooks.support import hook_actor, seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer
from tests.web.test_adapters import transport

from .conftest import NOW, USER_ID, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_environment_runtime import template_config
from .test_harness_runtime import _environment
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


def _effective_with_web(selection: WebSelection) -> EffectiveAgentConfig:
    from tests.agents.conftest import agent_config
    from tests.agents.test_reconstruction import _effective

    tools = {
        operation: {"permission": "allow", "config": selected.model_dump(mode="python")}
        for operation in ("search", "scrape", "fetch", "download")
        if (selected := getattr(selection, operation)) is not None
    }
    payload = agent_config().model_dump(mode="python", by_alias=True)
    payload["toolsets"] = {
        **payload["toolsets"],
        "web": {"enabled": True, "tools": tools},
    }
    return _effective(AgentConfig.model_validate(payload))


async def fixture(
    interaction_sessions, interaction_object_store, handler, tmp_path, *, with_environment=True, web_transport=None
):
    if with_environment:
        await template_config(interaction_sessions, tmp_path / "workspace", "on_use")
    else:
        await seed_hook_actor_access(interaction_sessions)
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    async with short_session(interaction_sessions) as session:
        run = (await session.get(RunRecord, run.id)).to_resource()
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    attempt = _authority(claim)
    await prepare_permissions(interaction_sessions, run, attempt)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    preparation = await execution.commit_preparation_success(attempt)
    await execution.enter_harness(attempt, preparation=preparation, harness_run_id="search-test")
    protector = SecretProtector(key=b"k" * 32, encryption_key_id="test")
    registry = WebProviderRegistry(load_provider_catalogs(()).web)
    service = WebProviderService(interaction_sessions, protector, registry)
    account = await service.create(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateWebProviderRequest(type="exa", name="Search", credential={"api_key": "first-key"}),
    )
    selection = WebSelection(
        search=SearchSelection(provider_id=account.id, max_results=2),
        fetch=FetchSelection(),
        download=DownloadSelection(),
    )
    runtime = WebRuntime(
        interaction_sessions,
        protector,
        built_in_web_provider_registry(transport=transport(handler)),
        web_transport=web_transport,
        clock=lambda: NOW,
    )
    binding = runtime.binding(
        run=run, workspace_id=WORKSPACE_ID, agent_id=run.agent_id, selection=selection, current_context=lambda: attempt
    )
    environment = _environment(tmp_path / "workspace", run.environment_id) if with_environment else None
    return run, attempt, account, selection, runtime, binding, environment


async def run_search(
    selection,
    binding,
    environment,
    *,
    requests=1,
    after_first=None,
    operations=None,
    access="full",
    instrumentation=None,
):
    calls = 0
    if operations is not None:
        requests = len(operations)

    async def model(messages, info):
        nonlocal calls
        expected_tools = {
            name for name in ("search", "scrape", "fetch", "download") if getattr(selection, name) is not None
        }
        assert {tool.name for tool in info.function_tools} == expected_tools
        assert not info.model_request_parameters.native_tools
        if calls < requests:
            if calls and after_first:
                await after_first()
            name, arguments = (
                operations[calls] if operations is not None else ("search", {"query": "public query", "num": 10})
            )
            calls += 1
            yield {
                0: DeltaToolCall(
                    name=name,
                    json_args=json.dumps(arguments),
                    tool_call_id=f"search-{calls}",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder(instrumentation=instrumentation).build(
        AgentDefinition(
            agent=AgentSpec(name="Search"),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(
                web_capability(selection),
                ToolPermissionsCapability(ToolPermissions(default="allow")),
            ),
        )
    )
    return await executable.run(
        "Find sources",
        environment=EnvironmentMount(environment, access=EnvironmentAccess(access))
        if environment is not None
        else None,
        bindings=RunBindings.embedded(web=binding),
    )


async def test_current_credentials_are_acquired_each_time_with_environment(
    interaction_sessions, interaction_object_store, tmp_path
):
    credentials = []

    def handler(request):
        credentials.append(request.headers["x-api-key"])
        assert json.loads(request.content)["numResults"] == 2
        return httpx2.Response(
            200, json={"results": [{"title": "Source", "url": "https://example.com", "highlights": ["Evidence"]}]}
        )

    run, attempt, account, selection, runtime, binding, environment = await fixture(
        interaction_sessions, interaction_object_store, handler, tmp_path
    )

    async def rotate():
        async with transaction(interaction_sessions) as session:
            record = await session.get(WebProviderRecord, account.id)
            record.replace_credential(
                '{"api_key": "second-key"}', SecretProtector(key=b"k" * 32, encryption_key_id="test")
            )

    result = await run_search(selection, binding, environment, requests=2, after_first=rotate)
    assert result.output_or_raise() == "done"
    assert credentials == ["first-key", "second-key"]
    assert "first-key" not in repr(result.state) and "second-key" not in repr(result.state)
    replacement = runtime.binding(
        run=run, workspace_id=WORKSPACE_ID, agent_id=run.agent_id, selection=selection, current_context=lambda: attempt
    )
    assert (
        replacement is not binding
        and replacement.search_backends[0].provider is not binding.search_backends[0].provider
    )


@pytest.mark.parametrize("revoke", ["provider", "principal", "agent", "fence"])
async def test_revocation_during_response_prevents_disclosure(
    interaction_sessions, interaction_object_store, tmp_path, revoke
):
    account_id = None
    run_id = None

    async def handler(_):
        async with transaction(interaction_sessions) as session:
            if revoke == "provider":
                record = await session.get(WebProviderRecord, account_id)
                record.enabled = False
            elif revoke == "principal":
                record = await session.get(UserRecord, USER_ID)
                record.status = "disabled"
            elif revoke == "agent":
                record = await session.get(AgentRecord, run_id)
                record.enabled = False
            else:
                from a13n_service.interactions.models import RunAttemptRecord

                record = await session.get(RunAttemptRecord, attempt.run_attempt_id)
                record.lease_token_digest = "0" * 64
        return httpx2.Response(
            200, json={"results": [{"url": "https://example.com/private-result", "title": "must not disclose"}]}
        )

    run, attempt, account, selection, _runtime, binding, environment = await fixture(
        interaction_sessions, interaction_object_store, handler, tmp_path
    )
    account_id, run_id = account.id, run.agent_id
    if revoke == "principal":
        from a13n_service.iam.attempts import AttemptAuthorizationError

        assert (await run_search(selection, binding, environment)).output_or_raise() == "done"
        for _ in range(10):
            await attempt.authorization.admit_model_request()
        with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
            await attempt.authorization.admit_model_request()
        with pytest.raises(AttemptAuthorizationError):
            await _runtime.validate(
                run=run,
                workspace_id=WORKSPACE_ID,
                config=_effective_with_web(selection),
                current_context=lambda: attempt,
            )
        return
    with pytest.raises(RunError) as revoked:
        await run_search(selection, binding, environment)
    assert revoked.value.code == "web_provider_unavailable"
    assert "must not disclose" not in str(revoked.value)


async def test_explicit_retry_refreshes_key_and_transport_failure_is_not_replayed(
    interaction_sessions, interaction_object_store, tmp_path
):
    credentials = []
    account_id = None

    async def handler(request):
        credentials.append(request.headers["x-api-key"])
        if len(credentials) == 1:
            async with transaction(interaction_sessions) as session:
                record = await session.get(WebProviderRecord, account_id)
                record.replace_credential(
                    '{"api_key": "rotated-key"}', SecretProtector(key=b"k" * 32, encryption_key_id="test")
                )
            return httpx2.Response(429, headers={"Retry-After": "0"})
        raise httpx2.ConnectError("private transport diagnostic")

    _, _, account, selection, _, binding, environment = await fixture(
        interaction_sessions, interaction_object_store, handler, tmp_path
    )
    account_id = account.id
    result = await run_search(selection, binding, environment)
    assert result.output_or_raise() == "done"
    assert credentials == ["first-key", "rotated-key"]
    assert "web_search_failed" in repr(result.state)
    assert "private transport diagnostic" not in repr(result.state)


async def test_binding_cannot_be_used_outside_harness_tool_authority(
    interaction_sessions, interaction_object_store, tmp_path
):
    from a13n_harness.capabilities.web import WebSearchRequest

    *_, binding, _environment = await fixture(
        interaction_sessions, interaction_object_store, lambda _: pytest.fail("unexpected dispatch"), tmp_path
    )
    with pytest.raises(RunError):
        await binding.search_backends[0].provider.search(WebSearchRequest(query="query", limit=1))


async def test_search_does_not_require_environment(interaction_sessions, interaction_object_store, tmp_path):
    run, attempt, _, selection, runtime, binding, environment = await fixture(
        interaction_sessions,
        interaction_object_store,
        lambda _: httpx2.Response(200, json={"results": []}),
        tmp_path,
        with_environment=False,
    )
    config = _effective_with_web(selection)
    await runtime.validate(run=run, workspace_id=WORKSPACE_ID, config=config, current_context=lambda: attempt)
    assert (await run_search(selection, binding, environment)).output_or_raise() == "done"


async def test_scrape_does_not_require_environment(interaction_sessions, interaction_object_store, tmp_path):
    def handler(request):
        assert request.url == "https://api.exa.ai/contents"
        return httpx2.Response(
            200,
            json={"results": [{"url": "https://example.com/article", "text": "provider evidence"}]},
        )

    run, attempt, account, _, runtime, _, environment = await fixture(
        interaction_sessions,
        interaction_object_store,
        handler,
        tmp_path,
        with_environment=False,
    )
    selection = WebSelection(scrape=ScrapeSelection(provider_id=account.id))
    binding = runtime.binding(
        run=run,
        workspace_id=WORKSPACE_ID,
        agent_id=run.agent_id,
        selection=selection,
        current_context=lambda: attempt,
    )
    result = await run_search(
        selection,
        binding,
        environment,
        operations=[("scrape", {"url": "https://example.com/article"})],
    )
    assert result.output_or_raise() == "done"
    assert "provider evidence" in repr(result.state)


async def test_fetch_needs_no_provider_or_environment_but_download_needs_environment_on_call(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    outgoing = []

    async def resolve(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr("a13n_service.web.web.getaddrinfo", resolve)

    def web_handler(request):
        outgoing.append(request)
        return httpx2.Response(200, headers={"Content-Type": "text/plain"}, content=b"public evidence")

    run, attempt, _, _, runtime, _, environment = await fixture(
        interaction_sessions,
        interaction_object_store,
        lambda _: pytest.fail("unexpected Provider dispatch"),
        tmp_path,
        with_environment=False,
        web_transport=WebTransport(
            client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(web_handler))
        ),
    )
    selection = WebSelection(fetch=FetchSelection(), download=DownloadSelection())
    binding = runtime.binding(
        run=run,
        workspace_id=WORKSPACE_ID,
        agent_id=run.agent_id,
        selection=selection,
        current_context=lambda: attempt,
    )
    result = await run_search(
        selection,
        binding,
        environment,
        operations=[
            ("fetch", {"url": "https://example.com/page"}),
            ("download", {"urls": ["https://example.com/file.txt"]}),
        ],
    )
    assert result.output_or_raise() == "done"
    assert "public evidence" in repr(result.state)
    assert len(outgoing) == 1
    assert outgoing[0].url.path == "/page"
    assert outgoing[0].headers["host"] == "example.com"


@pytest.mark.parametrize("access", ["full", "read_only"])
@pytest.mark.parametrize("directory", ["", "downloads"])
async def test_web_tools_fetch_and_download_through_environment(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, access, directory
):
    outgoing = []

    async def resolve(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr("a13n_service.web.web.getaddrinfo", resolve)

    def web_handler(request):
        outgoing.append(request)
        assert "x-api-key" not in request.headers and "x-subscription-token" not in request.headers
        return httpx2.Response(200, headers={"Content-Type": "text/plain"}, content=b"page evidence")

    _, _, _, selection, _, binding, environment = await fixture(
        interaction_sessions,
        interaction_object_store,
        lambda _: pytest.fail("unexpected search dispatch"),
        tmp_path,
        web_transport=WebTransport(
            client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(web_handler))
        ),
    )
    result = await run_search(
        selection,
        binding,
        environment,
        access=access,
        operations=[
            ("fetch", {"url": "https://example.com/page"}),
            ("download", {"urls": ["https://example.com/file.txt"], "save_dir": f"/workspace/{directory}"}),
        ],
    )
    assert result.output_or_raise() == "done"
    assert "page evidence" in repr(result.state)
    files = list((tmp_path / "workspace" / directory).glob("*.txt"))
    if access == "full":
        assert len(outgoing) == 2
        assert len(files) == 1 and files[0].read_bytes() == b"page evidence"
    else:
        assert len(outgoing) == 1 and files == []


@pytest.mark.parametrize("mode", ["enabled", "disabled", "broken_attributes"])
async def test_search_trace_enrichment_is_scoped_and_failsoft(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, mode
):
    from a13n_harness import HarnessInstrumentation, HarnessTraceContent
    from opentelemetry.sdk.trace import Span, TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(200, json={"results": [{"title": "Source", "url": "https://example.com"}]})

    _, _, account, selection, _, binding, environment = await fixture(
        interaction_sessions, interaction_object_store, handler, tmp_path
    )
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrumentation = (
        None
        if mode == "disabled"
        else HarnessInstrumentation(tracer_provider=provider, trace_content=HarnessTraceContent.NONE)
    )
    original = Span.set_attributes
    attempts = []

    def set_attributes(span, attributes):
        if "a13n.web.provider.id" in attributes:
            attempts.append(True)
            if mode == "broken_attributes":
                raise RuntimeError("telemetry unavailable")
        original(span, attributes)

    monkeypatch.setattr(Span, "set_attributes", set_attributes)
    with provider.get_tracer("host").start_as_current_span("host"):
        result = await run_search(selection, binding, environment, instrumentation=instrumentation)
    assert result.output_or_raise() == "done" and len(calls) == 1
    spans = exporter.get_finished_spans()
    enriched = [span for span in spans if "a13n.web.provider.id" in span.attributes]
    assert bool(attempts) == (mode != "disabled")
    if mode == "enabled":
        assert len(enriched) == 1
        assert enriched[0].attributes["gen_ai.tool.name"] == "search"
        assert enriched[0].attributes["a13n.web.provider.id"] == account.id
        assert enriched[0].attributes["a13n.web.provider.type"] == "exa"
    else:
        assert not enriched
    if mode == "disabled":
        assert len(spans) == 1 and spans[0].name == "host"
    assert "first-key" not in repr([dict(span.attributes) for span in spans])
