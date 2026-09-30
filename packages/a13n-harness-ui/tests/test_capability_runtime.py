from __future__ import annotations

import socket
from io import BytesIO

import pytest
from a13n_harness import RunBindings, RunConfiguration
from a13n_harness.capabilities import (
    DocumentConversionRequest,
    WebBinding,
    WebProviderError,
)
from a13n_harness_ui.capability_runtime import (
    HttpWebPolicy,
    LocalDocumentConverter,
    production_run_bindings,
)
from openpyxl import Workbook

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "https://[::1]/",
        "http://10.0.0.8/",
        "http://169.254.169.254/",
        "http://localhost./",
        "http://app.localhost/",
    ],
)
async def test_web_host_authorization_is_run_scoped(url: str, enabled: bool) -> None:
    policy = HttpWebPolicy(RunConfiguration(allowed_hosts={"proxy-only.test", "93.184.216.34"} if enabled else None))
    if enabled:
        with pytest.raises(WebProviderError) as denied:
            await policy.authorize(url, purpose="fetch")
        assert denied.value.code == "web_destination_denied"
    else:
        await policy.authorize(url, purpose="fetch")


@pytest.mark.parametrize("enabled", [False, True])
async def test_web_policy_never_resolves_hostnames(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    def unexpected_dns(*args, **kwargs):
        pytest.fail("URL authorization must not depend on local DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)
    await HttpWebPolicy(
        RunConfiguration(allowed_hosts={"proxy-only.test", "93.184.216.34"} if enabled else None)
    ).authorize("https://proxy-only.test/page", purpose="fetch")
    await HttpWebPolicy(
        RunConfiguration(allowed_hosts={"proxy-only.test", "93.184.216.34"} if enabled else None)
    ).authorize("https://93.184.216.34/page", purpose="fetch")


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "https:///page", "https://example.com:65536", "https://[bad]/", "http://example.com:0/"],
)
async def test_web_url_validation_remains_enabled(url: str, enabled: bool) -> None:
    with pytest.raises(WebProviderError) as invalid:
        await HttpWebPolicy(
            RunConfiguration(allowed_hosts={"proxy-only.test", "93.184.216.34"} if enabled else None)
        ).authorize(url, purpose="fetch")
    assert invalid.value.code == "web_url_invalid"


async def test_local_document_converter_converts_a_real_workbook() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Metrics"
    sheet.append(["name", "value"])
    sheet.append(["latency", 42])
    source = BytesIO()
    workbook.save(source)
    workbook.close()

    result = await LocalDocumentConverter().convert(
        DocumentConversionRequest(
            kind="office",
            source_name="metrics.xlsx",
            source_bytes=source.getvalue(),
            max_markdown_bytes=1024 * 1024,
            max_asset_bytes=1024 * 1024,
            max_total_asset_bytes=1024 * 1024,
            max_assets=10,
            deadline_seconds=30,
        )
    )

    assert "## Metrics" in result.markdown
    assert "| latency | 42 |" in result.markdown


def test_production_capabilities_bind_only_required_fresh_collaborators() -> None:
    baseline = RunBindings.embedded()
    none = production_run_bindings(baseline, frozenset())
    web = production_run_bindings(baseline, frozenset({"a13n.web"}))
    documents = production_run_bindings(baseline, frozenset({"a13n.documents"}))

    assert none is baseline
    assert isinstance(web.web, WebBinding) and web.document_converter is None
    assert isinstance(documents.document_converter, LocalDocumentConverter) and documents.web is None
    assert web.web is not production_run_bindings(baseline, frozenset({"a13n.web"})).web
    assert web.instance is baseline.instance and documents.instance is baseline.instance
    assert isinstance(web.web.policy, HttpWebPolicy) and web.web.policy.configuration == baseline.configuration
    restricted = RunBindings.embedded(configuration=RunConfiguration(allowed_hosts={"allowed.test"}))
    guarded = production_run_bindings(restricted, frozenset({"a13n.web"}))
    assert guarded.web is not None
    assert (
        isinstance(guarded.web.policy, HttpWebPolicy) and guarded.web.policy.configuration == restricted.configuration
    )


async def test_existing_web_yaml_runs_host_scrape_with_search_off(monkeypatch: pytest.MonkeyPatch) -> None:
    import yaml
    from a13n_harness import AgentSpec, HarnessBuilder
    from a13n_harness.capabilities import WebCapability, WebResponse
    from a13n_harness_ui.capability_runtime import HttpxWebClient
    from a13n_harness_ui.configuration.models import AgentResource
    from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    selections = yaml.safe_load("""
- capability: web
  configuration:
    search:
      mode: 'off'
    scrape:
      mode: host
""")
    resource = AgentResource.model_validate(
        {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-legacy-web",
            "name": "Legacy Web",
            "capabilities": selections,
        }
    )
    selected = HarnessUiExtensionCatalog().capabilities(
        tuple((item.capability, item.configuration) for item in resource.capabilities)
    )
    capability = selected[0].capability
    assert isinstance(capability, WebCapability)
    assert capability.configuration.search.mode == "off"
    assert capability.configuration.scrape.mode == "host"
    assert resource.model_dump(mode="json")["capabilities"] == selections
    requests = []
    closed = []

    async def body():
        yield b"<html><body><h1>Legacy configuration works</h1></body></html>"

    async def close():
        closed.append(True)

    async def request(self, request, *, policy):
        requests.append(request)
        return WebResponse(
            status_code=200,
            final_url=request.url,
            canonical_url=request.url,
            headers={"content-type": "text/html"},
            body=body(),
            _close=close,
        )

    monkeypatch.setattr(HttpxWebClient, "request", request)

    async def model(messages, info):
        assert {tool.name for tool in info.function_tools} == {"scrape", "fetch", "download"}
        assert not info.model_request_parameters.native_tools
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="scrape", json_args='{"url":"https://example.com/article"}', tool_call_id="scrape-1"
                )
            }
        else:
            assert "Legacy configuration works" in str(returns[-1].content)
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(capability,)
    )
    result = await executable.run(
        "Read the page",
        bindings=production_run_bindings(RunBindings.embedded(), frozenset({"a13n.web"})),
    )
    assert result.output_or_raise() == "done"
    assert len(requests) == 1 and requests[0].purpose == "scrape"
    assert closed == [True]
