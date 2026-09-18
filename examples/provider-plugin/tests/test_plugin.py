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
        async def validate(self, endpoint):
            assert endpoint == "https://search.acme.example/v1/search"
            self.validate_syntax(endpoint)

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
        {}, {"token": "test-token"}, search_options=SearchOptions(deny_domains=("example.com",)), transport=transport
    ) as web:
        assert (await web.search(WebSearchRequest(query="plugins", limit=2))).results == ()
    with pytest.raises(ValidationError):
        async with definition.open({"index": "INVALID"}, {"token": "test-token"}):
            pytest.fail("invalid configuration accepted")
    with pytest.raises(ValidationError):
        async with definition.open({}, {"api_key": "wrong-field"}):
            pytest.fail("invalid credential accepted")


def test_public_import_and_installed_loading_do_not_import_service_or_agent_runtime() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("a13n_service", "pydantic_ai", "mem0", "a13n_harness.execution", "a13n_environment")):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from a13n_harness.providers.plugins import load_provider_plugins
from a13n_harness.providers.web.builtins import built_in_web_providers
assert len(built_in_web_providers()) == 9
assert load_provider_plugins(("acme",))[0].manifest.web[0].type == "acme_web"
""",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
