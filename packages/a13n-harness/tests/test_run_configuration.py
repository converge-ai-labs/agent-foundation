"""Run configuration normalization, immutability and transport authorization."""

import socket
from copy import deepcopy

import httpx2
import pytest
from a13n_harness import RunBindings, RunConfiguration
from a13n_harness.configuration import HostNotAllowedError
from a13n_harness.models.transport import create_model_http_client
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from pydantic import ValidationError


def test_configuration_snapshots_nested_extensions_and_normalizes_hosts():
    source = {"example.filter": {"images": ["keep"]}}
    config = RunConfiguration(allowed_hosts={"EXAMPLE.com.", "bücher.de", "2001:0db8::1"}, extensions=source)
    source["example.filter"]["images"].append("change")
    value = config.extensions["example.filter"]
    assert isinstance(value, dict)
    value["images"].append("change")
    assert config.extensions["example.filter"] == {"images": ["keep"]}
    assert config.allowed_hosts == {"example.com", "xn--bcher-kva.de", "2001:db8::1"}
    assert deepcopy(config) == config
    assert RunConfiguration.model_validate_json(config.model_dump_json()) == config
    assert config.model_dump(mode="json")["allowed_hosts"] == ["2001:db8::1", "example.com", "xn--bcher-kva.de"]
    with pytest.raises(ValidationError):
        config.allowed_hosts = None
    with pytest.raises(TypeError):
        config.extensions["new"] = True
    assert RunBindings.embedded(configuration=config).configuration is config


@pytest.mark.parametrize(
    "host", ["*.example.com", "https://example.com", "example.com:443", "10.0.0.0/8", "", "a b", "-bad.com"]
)
def test_allowed_hosts_reject_non_host_entries(host):
    with pytest.raises(ValidationError):
        RunConfiguration(allowed_hosts={host})


def test_none_empty_and_exact_hostname_semantics():
    RunConfiguration().authorize_url("http://127.0.0.1:8080")
    with pytest.raises(HostNotAllowedError):
        RunConfiguration(allowed_hosts=set()).authorize_url("https://example.com")
    config = RunConfiguration(allowed_hosts={"example.com", "::1"})
    config.authorize_url("https://EXAMPLE.com.:8443/path")
    config.authorize_url("http://[::1]/")
    for url in ("https://sub.example.com", "https://example.com.evil.test", "https://other.test"):
        with pytest.raises(HostNotAllowedError):
            config.authorize_url(url)


@pytest.mark.anyio
async def test_endpoint_policy_has_no_dns_precheck_and_checks_redirect(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("authorization must not resolve DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    policy = EndpointPolicy(configuration=RunConfiguration(allowed_hosts={"proxy-only.test"}))
    assert await policy.validate("https://proxy-only.test/api") == "https://proxy-only.test/api"
    with pytest.raises(EndpointPolicyError):
        await policy.validate_redirect("https://proxy-only.test", "https://other.test")
    assert await policy.validate_redirect("https://proxy-only.test/a", "https://proxy-only.test/b") == (
        "https://proxy-only.test/b",
        True,
    )


@pytest.mark.anyio
async def test_model_transport_checks_each_request_before_custom_transport():
    sent = []

    async def respond(request):
        sent.append(str(request.url))
        return httpx2.Response(200)

    async with create_model_http_client(
        transport=httpx2.MockTransport(respond),
        retry=None,
        configuration=RunConfiguration(allowed_hosts={"allowed.test"}),
    ) as client:
        await client.get("https://allowed.test")
        with pytest.raises(HostNotAllowedError):
            await client.get("https://denied.test")
    assert sent == ["https://allowed.test"]


@pytest.mark.anyio
async def test_an_opt_in_plugin_reads_the_accepted_snapshot():
    from a13n_harness import AbstractHarnessPlugin, AgentSpec, HarnessBuilder
    from pydantic_ai.models.function import FunctionModel

    observed = []

    class ReaderPlugin(AbstractHarnessPlugin):
        plugin_id = "example.reader"

        async def for_run(self, context):
            observed.append(context.configuration)
            assert context.configuration.extensions["example.reader"] == {"image": "keep"}
            return self

    async def answer(messages, info):
        yield "done"

    configuration = RunConfiguration(allowed_hosts={"allowed.test"}, extensions={"example.reader": {"image": "keep"}})
    agent = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=answer),
        plugins=(ReaderPlugin(),),
    )
    result = await agent.run("hello", bindings=RunBindings.embedded(configuration=configuration))
    assert result.output_or_raise() == "done"
    assert observed == [configuration]


@pytest.mark.anyio
async def test_restricted_web_run_does_not_expose_provider_native_navigation():
    from types import SimpleNamespace

    from a13n_harness.capabilities import WebCapability

    definition = WebCapability()
    assert definition.get_native_tools()
    context = SimpleNamespace(deps=SimpleNamespace(configuration=RunConfiguration(allowed_hosts={"allowed.test"})))
    owner = await definition.for_run(context)
    assert owner.get_native_tools() == []
    assert definition.get_native_tools()


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("host", ["allowed.test", "denied.test"])
async def test_native_media_urls_are_rejected_before_sdk_download(monkeypatch, stream, host):
    from a13n_harness.errors import RunError
    from a13n_harness.providers.model.credentials import ApiKeyCredential
    from a13n_harness.providers.model.routes import build_api_key_model
    from pydantic_ai import Agent
    from pydantic_ai.messages import ImageUrl

    async def unexpected(*args, **kwargs):
        pytest.fail("restricted media must never reach native download or HTTP")

    monkeypatch.setattr("pydantic_ai.models.openai.download_item", unexpected)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", unexpected)
    model = await build_api_key_model(
        "openai-chat:fixture",
        ApiKeyCredential(api_key="fixture"),
        base_url="https://allowed.test/v1",
        configuration=RunConfiguration(allowed_hosts={"allowed.test"}),
    )
    async with model:
        error = RunError if host == "allowed.test" else HostNotAllowedError
        with pytest.raises(error):
            agent = Agent(model)
            prompt = [ImageUrl(f"https://{host}/image.png", force_download=True)]
            if stream:
                async with agent.run_stream_events(prompt) as events:
                    async for _ in events:
                        pass
            else:
                await agent.run(prompt)
