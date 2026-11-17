from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from a13n_harness import RunConfiguration
from a13n_harness.capabilities.web import (
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from a13n_harness.providers import ProviderCatalog
from a13n_harness.providers.authentication import Authentication, AuthenticationCase, CredentialMode
from a13n_harness.providers.web import WebProviderDefinition
from a13n_harness.providers.web.configuration import ApiKeyCredential, EmptyConfiguration
from a13n_harness.toolsets.web import WebConfiguration, WebProviderError
from a13n_harness_ui import web_providers
from a13n_harness_ui.capability_runtime import web_run_binding
from a13n_harness_ui.composition import AgentCompositionResolver, AgentReconstructor, ThreadCompositionSelection
from a13n_harness_ui.composition.models import ResolvedRunComposition
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.models import WebProviderResource
from a13n_harness_ui.configuration.mutation import (
    ResourceMutationRequest,
    delete_configuration_source,
    mutate_configuration_source,
)
from a13n_harness_ui.errors import CompositionError, ConfigurationError
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStore
from anyio import CancelScope, create_task_group, sleep, sleep_forever
from pydantic import BaseModel, ConfigDict, SecretStr

pytestmark = pytest.mark.anyio


class _Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anonymous: bool = False
    region: str = "test-region"


class _Credential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_key: SecretStr
    secret_key: SecretStr
    session_token: SecretStr | None = None


def _account(**updates) -> WebProviderResource:
    return WebProviderResource.model_validate(
        {
            "schema_version": "1",
            "kind": "web_provider",
            "id": "web-provider-work",
            "name": "Work search",
            "type": "test_cloud",
            "configuration": {},
            "credential": {"access_key": {"env": "TEST_WEB_ACCESS"}, "secret_key": {"credential_ref": "key-web"}},
            **updates,
        }
    )


@pytest.fixture
def fake_provider(monkeypatch):
    calls = []

    async def search(configuration, credential, request, options, transport):
        calls.append(("search", configuration, credential, options))
        await sleep(0)
        return WebSearchResponse(
            results=tuple(
                WebSearchResult(title=f"Result {i}", url=f"https://result.test/{i}", snippet="Text")
                for i in range(request.limit)
            )
        )

    async def scrape(configuration, credential, request, options, transport, policy):
        calls.append(("scrape", configuration, credential, options))
        await policy.authorize(request.url, purpose="scrape")
        return WebScrapeResult(content="x" * 100, source_url=request.url, canonical_url=request.url)

    definition = WebProviderDefinition(
        type="test_cloud",
        display_name="Test cloud",
        configuration_model=_Configuration,
        credential_model=_Credential,
        authentication=Authentication(
            cases=(AuthenticationCase(field="anonymous", equals=True, mode=CredentialMode.forbidden),)
        ),
        search=search,
        scrape=scrape,
        supports_restricted_scrape=True,
    )
    catalog = ProviderCatalog((definition,))
    monkeypatch.setattr(web_providers, "web_provider_catalog", lambda: catalog)
    monkeypatch.setenv("TEST_WEB_ACCESS", "test-access-value")
    return definition, calls


async def test_dynamic_catalog_describes_multifield_inputs_without_credentials(fake_provider):
    descriptor = web_providers.describe_web_providers((_account(),))
    assert descriptor.types[0].type == "test_cloud"
    assert descriptor.types[0].operations == ("search", "scrape")
    assert descriptor.types[0].supports_restricted_scrape
    assert set(descriptor.types[0].credential_schema["properties"]) == {"access_key", "secret_key", "session_token"}
    encoded = descriptor.model_dump_json()
    assert "test-access-value" not in encoded
    assert "TEST_WEB_ACCESS" in encoded and "key-web" in encoded


@pytest.mark.parametrize(
    "updates",
    [
        {"credential": None},
        {"credential": {}},
        {"credential": {"unknown": {"env": "TEST"}}},
        {"configuration": {"anonymous": True}},
        {"configuration": {"unknown": "sensitive-input"}},
        {"type": "unknown"},
    ],
)
async def test_account_validation_rejects_invalid_recipes_without_resolving(updates, fake_provider):
    with pytest.raises(CompositionError) as caught:
        web_providers.validate_web_account(_account(**updates))
    assert caught.value.code == "web_provider_configuration_invalid"
    assert "sensitive-input" not in str(caught.value)
    assert caught.value.__suppress_context__


async def test_required_optional_and_configuration_dependent_presence(fake_provider, monkeypatch):
    definition, _ = fake_provider
    web_providers.validate_web_account(_account())
    web_providers.validate_web_account(_account(configuration={"anonymous": True}, credential=None))
    optional = replace(definition, authentication=Authentication(mode=CredentialMode.optional))
    monkeypatch.setattr(web_providers, "web_provider_catalog", lambda: ProviderCatalog((optional,)))
    web_providers.validate_web_account(_account(credential=None))
    binding = web_run_binding(
        RunConfiguration(),
        accounts=(_account(credential=None),),
        web_configuration=WebConfiguration(search={"backend": "web-provider-work"}),
    )
    assert (
        len((await binding.search_backends[1].provider.search(WebSearchRequest(query="query", limit=8))).results) == 8
    )


async def test_sources_are_lazy_shared_by_concurrent_operations_and_fresh_per_run(tmp_path, fake_provider):
    _, calls = fake_provider

    class Keys(ApiKeyStore):
        reads = 0

        async def load(self, reference, *, observation=False):
            self.reads += 1
            await sleep(0)
            return await super().load(reference, observation=observation)

    keys = Keys(tmp_path / "auth.json")
    await keys.put(ApiKeyInput(credential_ref="key-web", key=SecretStr("test-secret-value")))
    recipe = _account()
    configuration = WebConfiguration(search={"backend": recipe.id}, scrape={"backend": recipe.id})
    binding = web_run_binding(RunConfiguration(), accounts=(recipe,), web_configuration=configuration, keys=keys)
    assert keys.reads == 0 and not calls
    search, scrape = binding.search_backends[1].provider, binding.scrape_backends[1].provider
    assert search is scrape
    async with create_task_group() as group:
        group.start_soon(search.search, WebSearchRequest(query="query", limit=8))

        async def run_scrape():
            result = await scrape.scrape(
                WebScrapeRequest(
                    url="https://result.test/page", deadline_seconds=30, max_redirects=2, max_content_bytes=9
                ),
                policy=binding.policy,
            )
            assert result.content == "x" * 9 and result.truncated

        group.start_soon(run_scrape)
    assert keys.reads == 1 and {call[0] for call in calls} == {"search", "scrape"}
    assert next(call for call in calls if call[0] == "search")[3].max_results == 8
    assert next(call for call in calls if call[0] == "scrape")[3].max_content_bytes == 9
    assert all(call[2].secret_key.get_secret_value() == "test-secret-value" for call in calls)
    fresh = web_run_binding(RunConfiguration(), accounts=(recipe,), web_configuration=configuration, keys=keys)
    await fresh.search_backends[1].provider.search(WebSearchRequest(query="query", limit=8))
    assert keys.reads == 2


@pytest.mark.parametrize("configuration", [{}, {"search": {"mode": "native"}, "scrape": {"mode": "off"}}])
async def test_adding_account_does_not_bind_it_or_read_its_sources(configuration, fake_provider):
    binding = web_run_binding(
        RunConfiguration(), accounts=(_account(),), web_configuration=WebConfiguration.model_validate(configuration)
    )
    assert [item.backend_id for item in binding.search_backends] == ["default"]
    assert [item.backend_id for item in binding.scrape_backends] == ["default"]


async def test_explicit_missing_source_never_falls_back_to_anonymous(fake_provider, monkeypatch):
    definition, calls = fake_provider
    monkeypatch.setattr(
        web_providers,
        "web_provider_catalog",
        lambda: ProviderCatalog((replace(definition, authentication=Authentication(mode=CredentialMode.optional)),)),
    )
    binding = web_run_binding(
        RunConfiguration(),
        accounts=(_account(),),
        web_configuration=WebConfiguration(search={"backend": "web-provider-work"}),
    )
    with pytest.raises(WebProviderError, match="web_credential_unavailable") as caught:
        await binding.search_backends[1].provider.search(WebSearchRequest(query="query", limit=8))
    assert not calls and "test-access-value" not in str(caught.value)
    assert caught.value.__suppress_context__


async def test_account_operation_support_is_validated(fake_provider, monkeypatch):
    definition, _ = fake_provider
    monkeypatch.setattr(
        web_providers,
        "web_provider_catalog",
        lambda: ProviderCatalog((replace(definition, scrape=None, supports_restricted_scrape=False),)),
    )
    with pytest.raises(CompositionError, match="does not support"):
        web_providers.selected_web_accounts(WebConfiguration(scrape={"backend": "web-provider-work"}), (_account(),))


@pytest.mark.parametrize("cancel", [False, True])
async def test_vendor_transport_uses_run_policy_and_cleans_owned_client(monkeypatch, cancel):
    from a13n_harness.providers.web.transport import WebProviderTransport

    clients, sent = [], []

    async def handler(request):
        sent.append(request)
        if cancel:
            cancellation.cancel()
            await sleep_forever()
        return httpx2.Response(200, content=b"ok")

    def transport(*, endpoint_policy):
        def client():
            instance = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
            clients.append(instance)
            return instance

        return WebProviderTransport(client_factory=client, endpoint_policy=endpoint_policy)

    async def search(configuration, credential, request, options, transport):
        await transport.exchange(
            lambda client: client.build_request("GET", "https://vendor.test/search"),
            operation="search",
            max_response_bytes=100,
        )
        return WebSearchResponse(results=())

    definition = WebProviderDefinition(
        type="test_cloud",
        display_name="Test",
        configuration_model=EmptyConfiguration,
        credential_model=ApiKeyCredential,
        search=search,
    )
    monkeypatch.setattr(web_providers, "web_provider_catalog", lambda: ProviderCatalog((definition,)))
    monkeypatch.setattr(web_providers, "WebProviderTransport", transport)
    monkeypatch.setenv("TEST_WEB_KEY", "test-only-key")
    recipe = _account(credential={"api_key": {"env": "TEST_WEB_KEY"}})
    config = WebConfiguration(search={"backend": recipe.id})
    denied = web_run_binding(
        RunConfiguration(allowed_hosts={"result.test"}), accounts=(recipe,), web_configuration=config
    )
    with pytest.raises(WebProviderError, match="web_search_failed"):
        await denied.search_backends[1].provider.search(WebSearchRequest(query="query", limit=1))
    assert not sent and clients[-1].is_closed
    allowed = web_run_binding(
        RunConfiguration(allowed_hosts={"vendor.test"}), accounts=(recipe,), web_configuration=config
    )
    with CancelScope() as cancellation:
        await allowed.search_backends[1].provider.search(WebSearchRequest(query="query", limit=1))
    assert cancellation.cancelled_caught is cancel
    assert len(sent) == 1 and all(client.is_closed for client in clients)


async def test_resource_publication_capture_reconstruction_and_deletion(tmp_path: Path, fake_provider):
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "1"\n')
    for directory in ("models", "agents"):
        (tmp_path / directory).mkdir()
    (tmp_path / "models/main.yaml").write_text(
        'schema_version: "1"\nkind: model\nid: model-main\nname: Main\nroute: openai:test\nauthentication: {kind: api_key, env: TEST_MODEL_KEY}\n'
    )
    resolver = AgentCompositionResolver()
    result = await mutate_configuration_source(
        root,
        "web_providers/work.yaml",
        ResourceMutationRequest(content=_account().model_dump_json()),
        validate_candidate=resolver.validate_generation,
    )
    assert result.configuration.web_providers["web-provider-work"].type == "test_cloud"
    (tmp_path / "agents/main.yaml").write_text(
        'schema_version: "1"\nkind: agent\nid: agent-main\nname: Main\nmodel: model-main\ncapabilities:\n  - capability: web\n    configuration:\n      search: {mode: host, backend: web-provider-work}\n'
    )
    source = await load_harness_ui_configuration(root)
    selection = ThreadCompositionSelection(
        thread_id="thread-test",
        project_id=None,
        version=1,
        agent_source_kind="agent",
        agent_source_id="agent-main",
        environment_profile_id="environment-native",
        harness_plugin_ids=(),
        environment_run_extension_ids=(),
        mcp_server_ids=(),
    )
    captured = resolver.resolve_run(source, selection)
    assert captured.web_providers == (_account(),)
    assert "test-access-value" not in captured.model_dump_json()
    restored = ResolvedRunComposition.model_validate_json(captured.model_dump_json())
    reconstructed = AgentReconstructor().reconstruct(restored, subagent_operator=None)
    first, second = reconstructed.fresh_web_binding(), reconstructed.fresh_web_binding()
    assert first is not second and first.search_backends[1].provider is not second.search_backends[1].provider
    replacement = _account(name="Changed", configuration={"region": "other"})
    await mutate_configuration_source(
        root,
        "web_providers/work.yaml",
        ResourceMutationRequest(content=replacement.model_dump_json()),
        validate_candidate=resolver.validate_generation,
    )
    assert captured.web_providers[0].name == "Work search"
    assert captured.web_providers[0].configuration == {}
    with pytest.raises(ConfigurationError):
        await delete_configuration_source(
            root, "web_providers/work.yaml", validate_candidate=resolver.validate_generation
        )
    legacy = captured.model_dump(mode="json")
    del legacy["web_providers"]
    old = ResolvedRunComposition.model_validate_json(json.dumps(legacy))
    assert old.web_providers == () and "web_providers" not in old.model_dump(mode="json")


async def test_descriptor_api_and_source_crud_require_authentication_without_probing(tmp_path, fake_provider):
    import httpx

    from .test_app import _write_configuration
    from .test_interactive_protocol import listener_with_app

    root = _write_configuration(tmp_path)
    _, calls = fake_provider
    async with listener_with_app(tmp_path, configuration_path=root) as (http, _, _):
        async with httpx.AsyncClient(base_url=http, trust_env=False) as api:
            assert (await api.get("/api/web-providers")).status_code == 401
            api.headers["Authorization"] = "Bearer test-only-key"
            catalog = await api.get("/api/web-providers")
            assert catalog.status_code == 200 and catalog.json()["types"][0]["type"] == "test_cloud"
            path = "/api/configuration/sources/web_providers/work.yaml"
            body = {"content": _account().model_dump_json()}
            checked = await api.post(
                "/api/configuration/validate", params={"path": "web_providers/work.yaml"}, json=body
            )
            assert checked.status_code == 200, checked.text
            created = await api.put(path, json=body)
            assert created.status_code == 200, created.text
            safe = await api.get("/api/web-providers")
            assert safe.json()["accounts"][0]["credential"]["secret_key"] == {"credential_ref": "key-web"}
            assert "test-access-value" not in safe.text
            source = await api.get(path)
            assert source.status_code == 200 and json.loads(source.json()["content"])["id"] == "web-provider-work"
            bad = await api.put(path, json={"content": _account(credential=None).model_dump_json()})
            assert bad.status_code == 400, bad.text
            assert (await api.get("/api/web-providers")).json() == safe.json()
            removed = await api.delete(path)
            assert removed.status_code == 200, removed.text
            assert not (await api.get("/api/web-providers")).json()["accounts"]
            assert not calls


@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize("child_kind", ["declared", "dynamic"])
async def test_real_root_child_and_resume_keep_recipes_but_refresh_credentials(
    tmp_path, monkeypatch, fake_provider, child_kind
):
    import yaml
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall

    from .test_dynamic_subagents import new_thread, open_app, returned

    _, calls = fake_provider
    executions = []

    def tool(name, arguments):
        return {0: DeltaToolCall(name=name, json_args=json.dumps(arguments))}

    async def model(messages, info):
        child = "send_parent_message" in {item.name for item in info.function_tools}
        if child:
            if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
                yield "child done"
            else:
                yield tool("search", {"query": "child", "num": 8})
            return
        results = returned(messages)
        assert "search" in {item.name for item in info.function_tools}, [item.name for item in info.function_tools]
        if results:
            assert "Unknown tool" not in str(results[-1].content), results[-1].content
        count = len(results)
        if count < 2:
            yield tool("search", {"query": "root", "num": 8})
        elif count == 2:
            name, arguments = (
                ("delegate", {"subagent_name": "explorer", "prompt": "initial task"})
                if child_kind == "declared"
                else ("spawn_subagent", {"name": "worker", "instructions": "CHILD_ROLE", "prompt": "initial task"})
            )
            yield tool(name, arguments)
        elif count == 3:
            executions.append(results[-1].content["execution_id"])
            yield tool("wait_subagent", {"execution_id": executions[0], "timeout_seconds": 10})
        elif count == 4:
            # The child's retained recipe stays frozen, but a fresh segment reloads its source values.
            await app.mutate_configuration(
                relative_path="web_providers/work.yaml",
                request=ResourceMutationRequest(
                    content=_account(configuration={"region": "changed"}).model_dump_json()
                ),
            )
            await app.put_api_key(ApiKeyInput(credential_ref="key-web", key=SecretStr("second-secret")))
            yield tool("resume_subagent", {"execution_id": executions[0], "prompt": "new task"})
        elif count == 5:
            executions.append(results[-1].content["execution_id"])
            yield tool("wait_subagent", {"execution_id": executions[1], "timeout_seconds": 10})
        else:
            yield "done"

    context = await open_app(tmp_path, monkeypatch, model)
    (tmp_path / "web_providers").mkdir()
    (tmp_path / "web_providers/work.yaml").write_text(_account().model_dump_json())
    agent_path = tmp_path / "agents/main.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["capabilities"] = [
        {"capability": "web", "configuration": {"search": {"mode": "host", "backend": "web-provider-work"}}}
    ]
    agent_path.write_text(yaml.safe_dump(agent))
    async with context as app:
        await app.put_api_key(ApiKeyInput(credential_ref="key-web", key=SecretStr("first-secret")))
        thread = await new_thread(app)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        result = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=20)
        assert result.status is RootOperationStatus.completed, result
        assert len(executions) == 2
        assert len(calls) == 4
        assert [call[2].secret_key.get_secret_value() for call in calls] == [
            "first-secret",
            "first-secret",
            "first-secret",
            "second-secret",
        ]
        assert all(call[1].region == "test-region" and call[3].max_results == 8 for call in calls)
