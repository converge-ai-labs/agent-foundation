from __future__ import annotations

import json
import subprocess
import sys

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.plugins import load_provider_plugins
from a13n_harness.providers.web import SearchOptions, WebProviderTransport, WebSearchRequest
from pydantic import ValidationError


@pytest.fixture
async def transport():
    class Policy(EndpointPolicy):
        async def validate(self, endpoint, *, resolve_dns=True):
            assert endpoint == "https://search.acme.example/v1/search"
            return self.validate_syntax(endpoint)[0]

    def handle(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        payload = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "title": f"{payload['index']}: {payload['query']}",
                        "url": "https://docs.example.com/result",
                        "snippet": "Installed Provider package result",
                    }
                ]
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        yield WebProviderTransport(client=client, endpoint_policy=Policy())


@pytest.mark.anyio
async def test_installed_entry_point_and_direct_use_share_definition(transport) -> None:
    from acme_provider.plugin import acme_web

    plugins = load_provider_plugins(("acme",))
    assert plugins[0].distribution_name == "a13n-provider-acme-example"
    definition = plugins[0].manifest.web[0]
    assert definition is acme_web
    async with definition.open({"index": "guides"}, {"token": "test-token"}, transport=transport) as web:
        result = await web.search(WebSearchRequest(query="plugins", limit=2))
    assert result.results[0].title == "guides: plugins"
    assert "test-token" not in repr(web)
    async with definition.open(
        {}, {"token": "test-token"}, options=SearchOptions(deny_domains=("example.com",)), transport=transport
    ) as web:
        assert (await web.search(WebSearchRequest(query="plugins", limit=2))).results == ()
    with pytest.raises(ValidationError):
        async with definition.open({"index": "INVALID"}, {"token": "test-token"}):
            pytest.fail("invalid configuration accepted")
    with pytest.raises(ValidationError):
        async with definition.open({}, {"api_key": "wrong-field"}):
            pytest.fail("invalid credential accepted")


def test_public_import_and_installed_loading_do_not_import_service_or_agent_runtime() -> None:
    """Five domains load their metadata without Service, Agent runtime, or optional SDKs."""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("a13n_harness.providers.connector.composio.runtime", "a13n_service", "pydantic_ai", "mem0", "a13n_harness.execution", "a13n_harness.agent")):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
import httpx2
def no_client(*args, **kwargs):
    raise AssertionError("Metadata opened a client")
httpx2.AsyncClient = no_client
from a13n_harness.providers.plugins import load_provider_plugins
from a13n_harness.providers.web.builtins import built_in_web_providers
assert len(built_in_web_providers()) == 9
manifest = load_provider_plugins(("acme",))[0].manifest
assert manifest.web[0].type == "acme_web"
assert manifest.model[0].type == "acme_model"
assert manifest.memory[0].type == "acme_memory"
assert manifest.connector[0].type == "acme_connector"
assert manifest.environment[0].type == "acme_workspace"
from a13n_harness.providers.connector.builtins import BUILT_IN_CONNECTOR_PROVIDERS
assert len(BUILT_IN_CONNECTOR_PROVIDERS) == 1
from a13n_harness.providers.memory.builtins import BUILT_IN_MEMORY_PROVIDERS
assert len(BUILT_IN_MEMORY_PROVIDERS) == 3
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS
assert len(BUILT_IN_ENVIRONMENT_PROVIDERS) == 11
assert not {"docker", "e2b", "modal"} & {name.split(".")[0] for name in sys.modules}
""",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.anyio
async def test_installed_model_definition_calls_native_api():
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models import ModelRequestParameters

    class Policy(EndpointPolicy):
        async def validate(self, endpoint, *, resolve_dns=True):
            assert endpoint.rstrip("/") == "https://models.acme.example/v1"
            return self.validate_syntax(endpoint)[0]

    calls = []

    def vendor(request):
        calls.append(
            (
                str(request.url),
                request.headers["authorization"],
                request.headers["x-acme-index"],
                request.headers["x-acme-revision"],
                json.loads(request.content),
            )
        )
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Native fixture result"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    definition = load_provider_plugins(("acme",))[0].manifest.model[0]
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(vendor)) as client:
        model = await definition.build(
            "fixture-model",
            configuration={"index": "guides"},
            credential={"authorization": {"token": "native-secret"}, "revision": 2},
            http_client=client,
            endpoint_policy=Policy(),
        )
        async with model:
            response = await model.request(
                [ModelRequest(parts=[UserPromptPart("hello")])], None, ModelRequestParameters()
            )
        assert response.text == "Native fixture result"
        assert not client.is_closed
    assert len(calls) == 1
    assert calls[0][:4] == ("https://models.acme.example/v1/chat/completions", "Bearer native-secret", "guides", "2")
    assert calls[0][4]["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.anyio
async def test_installed_memory_definition_direct_native_operation(monkeypatch):
    from a13n_harness.providers.memory.contracts import MemoryScope, MemorySubject

    observed = []

    async def remote(self, request):
        import json

        body = json.loads(request.content)
        observed.append((str(request.url), request.headers["x-api-key"], request.headers["x-acme-revision"], body))
        return httpx2.Response(200, json={"results": [{"id": "fact", "memory": "Direct fact", **body["filters"]}]})

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", remote)
    definition = load_provider_plugins(("acme",))[0].manifest.memory[0]
    subject = MemorySubject(MemoryScope.USER, "embedding-user")
    async with definition.open(
        {"collection": "notes"}, {"authorization": {"token": "direct-secret"}, "revision": 4}
    ) as backend:
        result = await backend.search("fact", subjects=(subject,), limit=3)
    assert result[0].text == "Direct fact" and result[0].subjects == (subject,)
    assert observed[0][:3] == ("https://memory.acme.example/notes/search", "direct-secret", "4")


@pytest.mark.anyio
async def test_installed_memory_cleanup_completes_under_cancellation(monkeypatch):
    import anyio

    completed = anyio.Event()
    calls = 0

    class Transport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            pytest.fail("No vendor I/O expected")

        async def aclose(self):
            nonlocal calls
            calls += 1
            await anyio.sleep(0)
            completed.set()

    client = httpx2.AsyncClient(transport=Transport())
    monkeypatch.setattr(httpx2, "AsyncClient", lambda **kwargs: client)
    definition = load_provider_plugins(("acme",))[0].manifest.memory[0]
    with anyio.CancelScope() as scope:
        async with definition.open({"collection": "notes", "access": "public"}):
            scope.cancel()
            await anyio.sleep_forever()
    assert scope.cancelled_caught
    assert completed.is_set() and calls == 1
