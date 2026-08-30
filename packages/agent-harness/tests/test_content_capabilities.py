from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from a13n_environment_provider import (
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness import (
    DocumentAsset,
    DocumentConversionRequest,
    DocumentConversionResult,
    DocumentsCapability,
    DocumentsConfiguration,
    DocumentsRunCapability,
    EnvironmentAction,
    EnvironmentPermissionSet,
    HarnessBuilder,
    MediaCapability,
    MediaConfiguration,
    MediaReadRequest,
    MediaResource,
    MediaRunCapability,
    ProviderUsage,
    ProviderUsageRecord,
    RunBindings,
    UsageMeasure,
    WebCapability,
    WebConfiguration,
    WebProviderError,
    WebRequest,
    WebResponse,
    WebRunCapability,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchConfiguration,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from a13n_harness.environment.advanced import (
    EnvironmentRuntimeMount,
    create_environment_runtime,
)
from a13n_harness.environment.local.binding import DirectLocalEnvironmentProviderBinding
from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from a13n_harness.toolsets.documents import DocumentsToolset
from a13n_harness.toolsets.media import MediaToolset
from a13n_harness.toolsets.web import WebToolset
from pydantic_ai import BinaryContent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


class _MediaReader:
    def __init__(self, resource: MediaResource) -> None:
        self.resource = resource
        self.requests: list[MediaReadRequest] = []

    async def read(self, request: MediaReadRequest) -> MediaResource:
        self.requests.append(request)
        return self.resource


class _DocumentConverter:
    def __init__(self, result: DocumentConversionResult) -> None:
        self.result = result
        self.requests: list[DocumentConversionRequest] = []

    async def convert(self, request: DocumentConversionRequest) -> DocumentConversionResult:
        self.requests.append(request)
        return self.result


class _WebPolicy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def authorize(self, url: str, *, purpose: str) -> None:
        self.calls.append((url, purpose))
        if "private.invalid" in url:
            raise WebProviderError("web_forbidden")


class _WebClient:
    def __init__(self, responses: Sequence[WebResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[WebRequest] = []

    async def request(self, request: WebRequest, *, policy) -> WebResponse:
        del policy
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected Web request")
        return self.responses.pop(0)


class _SearchProvider:
    def __init__(
        self,
        usage: tuple[ProviderUsage, ...] = (),
        *,
        credential_key: str = "token",
    ) -> None:
        self.requests: list[WebSearchRequest] = []
        self.usage = usage
        self.credential_key = credential_key

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        self.requests.append(request)
        return WebSearchResponse(
            results=(
                WebSearchResult(
                    title="Example",
                    url=(f"https://example.com/result?q=agent&{self.credential_key}=provider-secret"),
                    snippet="A bounded result.",
                ),
            ),
            usage=self.usage,
        )


class _ScrapeProvider:
    def __init__(self, result: WebScrapeResult) -> None:
        self.result = result
        self.requests: list[WebScrapeRequest] = []

    async def scrape(self, request: WebScrapeRequest, *, policy) -> WebScrapeResult:
        del policy
        self.requests.append(request)
        return self.result


def _binding(root: Path):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="content-capabilities-test",
            root=DirectLocalRootConfiguration(path=root),
        )
    )
    return create_environment_runtime(
        mounts={
            "local": EnvironmentRuntimeMount(
                binding=provider,
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                working_directory="/",
            )
        },
        default_mount="local",
    )


def _replacement_mount(root: Path) -> EnvironmentRuntimeMount:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="content-capabilities-test",
            root=DirectLocalRootConfiguration(path=root),
        )
    )
    return EnvironmentRuntimeMount(
        binding=provider,
        permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
        working_directory="/",
    )


def _policy() -> InvocationPolicyCapability:
    return InvocationPolicyCapability(evaluator=_Allow(), max_dispatch_retries=0)


def _one_tool_model(
    name: str,
    arguments: dict[str, object],
    *,
    seen: list[list[ModelMessage]],
    infos: list[AgentInfo] | None = None,
):
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        seen.append(messages)
        if infos is not None:
            infos.append(info)
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name=name,
                    json_args=json.dumps(arguments),
                    tool_call_id=f"{name}-1",
                )
            }
        else:
            yield "done"

    return FunctionModel(stream_function=stream)


def _tool_contents(messages: list[list[ModelMessage]]) -> list[Any]:
    return [
        part.content
        for call in messages
        for message in call
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _native_binary(messages: list[list[ModelMessage]]) -> list[BinaryContent]:
    output: list[BinaryContent] = []
    for call in messages:
        for message in call:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, UserPromptPart) or not isinstance(part.content, list):
                    continue
                output.extend(item for item in part.content if isinstance(item, BinaryContent))
    return output


async def _body(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


async def test_content_toolsets_compose_directly_over_natural_provider_ports(tmp_path: Path) -> None:
    media = MediaToolset(
        _MediaReader(
            MediaResource(
                kind="image",
                source_url="https://example.com/image.png",
                media_type="image/png",
                data=b"png",
            )
        ),
        MediaConfiguration(),
    )
    binding = _binding(tmp_path)
    run_bindings = RunBindings.embedded(environment=binding)
    async with binding.bind(run_id="direct-content-toolsets", instance=run_bindings.instance) as environment:
        documents = DocumentsToolset(
            _DocumentConverter(DocumentConversionResult(markdown="# Document")),
            files=environment.files,
            file_scopes=environment,
        )
        web = WebToolset(
            client=_WebClient(()),
            policy=_WebPolicy(),
            search_provider=_SearchProvider(),
            scrape_provider=_ScrapeProvider(
                WebScrapeResult(
                    markdown="# Page",
                    final_url="https://example.com/page",
                    canonical_url="https://example.com/page",
                )
            ),
            files=environment.files,
            file_scopes=environment,
        )

        assert set(documents.get_toolset().tools) == {"pdf_convert", "office_to_markdown"}
        assert set(web.get_toolset().tools) == {"search", "scrape", "fetch", "download"}
    assert set(media.get_toolset().tools) == {"read_media"}


async def test_media_capability_returns_native_binary_with_run_scoped_reader() -> None:
    usage = ProviderUsage(
        usage_id="media-1",
        provider="media-provider",
        product="image-read",
        timestamp=datetime.now(UTC),
        measures=(UsageMeasure(unit="request", quantity=Decimal(1)),),
    )
    reader = _MediaReader(
        MediaResource(
            kind="image",
            source_url="https://example.com/image.png?variant=large",
            media_type="image/png",
            data=b"\x89PNG",
            usage=(usage,),
        )
    )
    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model(
            "read_media",
            {"url": "https://example.com/image.png?variant=large", "instructions": "Read visible text."},
            seen=seen,
        ),
        capabilities=(MediaCapability(MediaConfiguration(max_image_bytes=1024)),),
    )

    result = await executable.run(
        "Inspect media",
        bindings=RunBindings.embedded(
            capabilities=(
                _policy(),
                MediaRunCapability(reader=reader),
            )
        ),
    )

    assert result.output_or_raise() == "done"
    assert len(reader.requests) == 1
    assert reader.requests[0].url == "https://example.com/image.png?variant=large"
    assert reader.requests[0].instructions == "Read visible text."
    assert reader.requests[0].max_image_bytes == 1024
    assert reader.requests[0].max_video_bytes == 64 * 1024 * 1024
    assert reader.requests[0].max_audio_bytes == 64 * 1024 * 1024
    binaries = _native_binary(seen)
    assert len(binaries) == 1
    assert binaries[0].data == b"\x89PNG"
    assert binaries[0].media_type == "image/png"
    provider_record = next(record for record in result.usage_records if isinstance(record, ProviderUsageRecord))
    assert provider_record.source == "media.reader"
    assert provider_record.usage.usage_id == "media-1"


async def test_media_capability_enforces_kind_specific_actual_byte_limit() -> None:
    reader = _MediaReader(
        MediaResource(
            kind="image",
            source_url="https://example.com/image.png",
            media_type="image/png",
            data=b"12345",
        )
    )
    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model("read_media", {"url": "https://example.com/image.png"}, seen=seen),
        capabilities=(MediaCapability(MediaConfiguration(max_image_bytes=4)),),
    )

    result = await executable.run(
        "Inspect media",
        bindings=RunBindings.embedded(capabilities=(_policy(), MediaRunCapability(reader=reader))),
    )

    assert result.output_or_raise() == "done"
    error = next(item for item in _tool_contents(seen) if isinstance(item, dict))
    assert error["error"]["code"] == "media_too_large"
    assert _native_binary(seen) == []


@pytest.mark.parametrize(
    "credential_key",
    ["apikey", "access_key", "auth", "client_secret", "sig", "X-Amz-Signature"],
)
async def test_media_capability_rejects_credential_provider_url_before_model_history(
    credential_key: str,
) -> None:
    resource = MediaResource.model_construct(
        kind="video",
        source_url="https://example.com/video.mp4",
        media_type="video/mp4",
        data=None,
        direct_url=f"https://cdn.example.com/video.mp4?{credential_key}=provider-secret",
        usage=(),
    )
    reader = _MediaReader(resource)
    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model("read_media", {"url": "https://example.com/video.mp4"}, seen=seen),
        capabilities=(MediaCapability(),),
    )

    result = await executable.run(
        "Inspect media",
        bindings=RunBindings.embedded(capabilities=(_policy(), MediaRunCapability(reader=reader))),
    )

    assert result.output_or_raise() == "done"
    error = next(item for item in _tool_contents(seen) if isinstance(item, dict))
    assert error["error"]["code"] == "media_response_invalid"
    assert "provider-secret" not in repr(seen)


@pytest.mark.parametrize(
    "credential_key",
    ["apikey", "access_key", "auth", "client_secret", "sig", "X-Amz-Signature"],
)
@pytest.mark.parametrize("url_field", ["source_url", "direct_url"])
def test_media_resource_fails_closed_for_credential_url_aliases(
    credential_key: str,
    url_field: str,
) -> None:
    values: dict[str, object] = {
        "kind": "video",
        "source_url": "https://example.com/video.mp4?variant=source",
        "media_type": "video/mp4",
        "data": None,
        "direct_url": "https://cdn.example.com/video.mp4?variant=large",
    }
    values[url_field] = f"https://example.com/video.mp4?{credential_key}=provider-secret"

    with pytest.raises(ValueError):
        MediaResource.model_validate(values)


async def test_documents_capability_publishes_one_complete_environment_tree(tmp_path: Path) -> None:
    (tmp_path / "report.pdf").write_bytes(b"pdf-source")
    converter = _DocumentConverter(
        DocumentConversionResult(
            markdown="# Report\n\n![page](images/page.png)\n",
            assets=(DocumentAsset(path="images/page.png", data=b"png", media_type="image/png"),),
            total_pages=3,
            converted_pages=3,
            page_start=1,
            page_end=3,
        )
    )
    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model("pdf_convert", {"file_path": "/workspace/report.pdf"}, seen=seen),
        capabilities=(DocumentsCapability(),),
    )

    result = await executable.run(
        "Convert",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            capabilities=(_policy(), DocumentsRunCapability(converter=converter)),
        ),
    )

    assert result.output_or_raise() == "done"
    assert converter.requests[0].source_bytes == b"pdf-source"
    assert converter.requests[0].page_start == 1
    assert converter.requests[0].page_end == 20
    tool_result = next(item for item in _tool_contents(seen) if isinstance(item, dict))
    assert tool_result["ok"] is True
    assert tool_result["page_range"] == "1-3"
    export = tmp_path / "export_report_pages_1_3"
    assert tool_result["export_path"] == "/workspace/export_report_pages_1_3"
    assert (export / "report.md").read_text() == converter.result.markdown
    assert (export / "images" / "page.png").read_bytes() == b"png"
    assert not list(tmp_path.glob(".export_report_pages_1_3.a13n-*"))


async def test_pdf_page_ranges_publish_to_distinct_agent_usable_exports(tmp_path: Path) -> None:
    (tmp_path / "report.pdf").write_bytes(b"pdf-source")

    class RangeConverter:
        def __init__(self) -> None:
            self.requests: list[DocumentConversionRequest] = []

        async def convert(self, request: DocumentConversionRequest) -> DocumentConversionResult:
            self.requests.append(request)
            assert request.page_start is not None and request.page_end is not None
            return DocumentConversionResult(
                markdown=f"# Pages {request.page_start}-{request.page_end}",
                total_pages=4,
                converted_pages=request.page_end - request.page_start + 1,
                page_start=request.page_start,
                page_end=request.page_end,
            )

    converter = RangeConverter()
    observed: list[dict[str, Any]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        observed[:] = returns
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="pdf_convert",
                    json_args=json.dumps({"file_path": "/workspace/report.pdf", "page_start": 1, "page_end": 2}),
                    tool_call_id="pdf-range-1",
                )
            }
        elif len(returns) == 1:
            yield {
                0: DeltaToolCall(
                    name="pdf_convert",
                    json_args=json.dumps({"file_path": "/workspace/report.pdf", "page_start": 3, "page_end": 4}),
                    tool_call_id="pdf-range-2",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DocumentsCapability(),),
    )
    result = await executable.run(
        "Convert ranges",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            capabilities=(_policy(), DocumentsRunCapability(converter=converter)),
        ),
    )

    assert result.output_or_raise() == "done"
    assert [item["export_path"] for item in observed] == [
        "/workspace/export_report_pages_1_2",
        "/workspace/export_report_pages_3_4",
    ]
    assert (tmp_path / "export_report_pages_1_2" / "report.md").read_text() == "# Pages 1-2"
    assert (tmp_path / "export_report_pages_3_4" / "report.md").read_text() == "# Pages 3-4"


async def test_documents_capability_removes_partial_staging_tree(tmp_path: Path) -> None:
    (tmp_path / "broken.docx").write_bytes(b"office-source")
    converter = _DocumentConverter(
        DocumentConversionResult(
            markdown="# Broken",
            assets=(
                DocumentAsset(path="images", data=b"file"),
                DocumentAsset(path="images/nested.png", data=b"cannot-write"),
            ),
        )
    )
    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model(
            "office_to_markdown",
            {"file_path": "/workspace/broken.docx"},
            seen=seen,
        ),
        capabilities=(DocumentsCapability(DocumentsConfiguration(max_asset_bytes=1024)),),
    )

    result = await executable.run(
        "Convert",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            capabilities=(_policy(), DocumentsRunCapability(converter=converter)),
        ),
    )

    assert result.output_or_raise() == "done"
    tool_result = next(item for item in _tool_contents(seen) if isinstance(item, dict))
    assert tool_result["ok"] is False
    assert not (tmp_path / "export_broken").exists()
    assert not list(tmp_path.glob(".export_broken.a13n-*"))


async def test_documents_rejects_stale_revision_before_publication(tmp_path: Path) -> None:
    root_a = tmp_path / "revision-a"
    root_b = tmp_path / "revision-b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "report.pdf").write_bytes(b"revision-a")
    binding = _binding(root_a)

    class RefreshingConverter:
        async def convert(self, request: DocumentConversionRequest) -> DocumentConversionResult:
            assert request.source_bytes == b"revision-a"
            await binding.replace("local", _replacement_mount(root_b))
            return DocumentConversionResult(
                markdown="# Revision A",
                total_pages=1,
                converted_pages=1,
                page_start=1,
                page_end=1,
            )

    resources: list[Any] = []

    class CapturePolicy:
        async def __call__(self, invocation, metadata, *, context):
            del metadata, context
            resources.extend(invocation.resources)
            return InvocationPolicyDecision.allow()

    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model("pdf_convert", {"file_path": "/workspace/report.pdf"}, seen=seen),
        capabilities=(DocumentsCapability(),),
    )
    result = await executable.run(
        "Convert",
        bindings=RunBindings.embedded(
            environment=binding,
            capabilities=(
                InvocationPolicyCapability(evaluator=CapturePolicy(), max_dispatch_retries=0),
                DocumentsRunCapability(converter=RefreshingConverter()),
            ),
        ),
    )

    assert result.output_or_raise() == "done"
    tool_result = next(item for item in _tool_contents(seen) if isinstance(item, dict))
    assert tool_result["ok"] is False
    assert tool_result["error"]["code"] == "environment_stale_mount"
    assert len(resources) == 1
    assert resources[0].kind == "file"
    assert not list(root_a.glob("export_*"))
    assert not list(root_b.glob("export_*"))


async def test_web_download_rejects_stale_revision_before_writing(tmp_path: Path) -> None:
    root_a = tmp_path / "revision-a"
    root_b = tmp_path / "revision-b"
    root_a.mkdir()
    root_b.mkdir()
    binding = _binding(root_a)

    class RefreshingClient:
        async def request(self, request: WebRequest, *, policy) -> WebResponse:
            del request, policy
            await binding.replace("local", _replacement_mount(root_b))
            return WebResponse(
                status_code=200,
                final_url="https://example.com/file.txt",
                canonical_url="https://example.com/file.txt",
                headers={"content-type": "text/plain"},
                body=_body(b"payload"),
            )

    resources: list[Any] = []

    class CapturePolicy:
        async def __call__(self, invocation, metadata, *, context):
            del metadata, context
            resources.extend(invocation.resources)
            return InvocationPolicyDecision.allow()

    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model(
            "download",
            {"urls": ["https://example.com/file.txt"], "save_dir": "/workspace/downloads"},
            seen=seen,
        ),
        capabilities=(WebCapability(WebConfiguration(search=WebSearchConfiguration(mode="off"))),),
    )
    result = await executable.run(
        "Download",
        bindings=RunBindings.embedded(
            environment=binding,
            capabilities=(
                InvocationPolicyCapability(evaluator=CapturePolicy(), max_dispatch_retries=0),
                WebRunCapability(client=RefreshingClient(), policy=_WebPolicy()),
            ),
        ),
    )

    assert result.output_or_raise() == "done"
    tool_result = next(item for item in _tool_contents(seen) if isinstance(item, list))
    assert tool_result[0]["ok"] is False
    assert tool_result[0]["error"]["code"] == "environment_stale_mount"
    assert len(resources) == 1
    assert resources[0].kind == "file"
    assert list((root_a / "downloads").iterdir()) == []
    assert not (root_b / "downloads").exists()


async def test_web_capability_composes_search_and_scrape_providers() -> None:
    usage = ProviderUsage(
        usage_id="search-1",
        provider="search-provider",
        product="web-search",
        timestamp=datetime.now(UTC),
        cost=Decimal("0.003"),
        currency="USD",
    )
    search = _SearchProvider((usage,))
    scrape = _ScrapeProvider(
        WebScrapeResult(
            markdown="# Page",
            final_url="https://example.com/final?source=provider",
            canonical_url="https://example.com/final?source=provider",
        )
    )
    policy = _WebPolicy()
    client = _WebClient(())
    seen: list[list[ModelMessage]] = []
    infos: list[AgentInfo] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model("search", {"query": "agent", "num": 5}, seen=seen, infos=infos),
        capabilities=(WebCapability(WebConfiguration(search=WebSearchConfiguration(mode="host"))),),
    )

    result = await executable.run(
        "Search",
        bindings=RunBindings.embedded(
            capabilities=(
                _policy(),
                WebRunCapability(
                    client=client,
                    policy=policy,
                    search_provider=search,
                    scrape_provider=scrape,
                ),
            )
        ),
    )

    assert result.output_or_raise() == "done"
    assert {tool.name for tool in infos[0].function_tools} >= {"search", "scrape", "fetch", "download"}
    assert search.requests == [WebSearchRequest(query="agent", limit=5)]
    tool_result = next(item for item in _tool_contents(seen) if isinstance(item, dict))
    assert tool_result["results"][0]["url"] == "https://example.com/result?q=agent"
    assert "provider-secret" not in repr(seen)
    provider_record = next(record for record in result.usage_records if isinstance(record, ProviderUsageRecord))
    assert provider_record.source == "web.search"
    assert provider_record.usage.cost == Decimal("0.003")


@pytest.mark.parametrize(
    "credential_key",
    ["apikey", "access_key", "auth", "client_secret", "sig", "X-Amz-Signature"],
)
async def test_web_search_strips_credential_aliases_from_model_history(credential_key: str) -> None:
    search = _SearchProvider(credential_key=credential_key)
    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model("search", {"query": "agent"}, seen=seen),
        capabilities=(WebCapability(WebConfiguration(search=WebSearchConfiguration(mode="host"))),),
    )

    result = await executable.run(
        "Search",
        bindings=RunBindings.embedded(
            capabilities=(
                _policy(),
                WebRunCapability(
                    client=_WebClient(()),
                    policy=_WebPolicy(),
                    search_provider=search,
                ),
            )
        ),
    )

    assert result.output_or_raise() == "done"
    tool_result = next(item for item in _tool_contents(seen) if isinstance(item, dict))
    assert tool_result["results"][0]["url"] == "https://example.com/result?q=agent"
    assert "provider-secret" not in repr(seen)


@pytest.mark.parametrize(
    "credential_key",
    ["apikey", "access_key", "auth", "client_secret", "sig", "X-Amz-Signature"],
)
def test_web_canonical_urls_fail_closed_for_credential_aliases(credential_key: str) -> None:
    with pytest.raises(ValueError):
        WebResponse(
            status_code=200,
            final_url="https://example.com/exact",
            canonical_url=f"https://example.com/model?{credential_key}=provider-secret",
            headers={},
            body=_body(b""),
        )


async def test_web_fetch_rechecks_final_url_and_returns_native_binary() -> None:
    policy = _WebPolicy()
    closed = 0

    async def close() -> None:
        nonlocal closed
        closed += 1

    client = _WebClient(
        (
            WebResponse(
                status_code=200,
                final_url="https://cdn.example.com/image.png?X-Amz-Signature=secret",
                canonical_url="https://cdn.example.com/image.png?variant=large",
                headers={"Content-Type": "image/png"},
                body=_body(b"\x89PNG"),
                redirect_count=1,
                _close=close,
            ),
        )
    )
    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model("fetch", {"url": "https://example.com/image"}, seen=seen),
        capabilities=(WebCapability(WebConfiguration(max_inline_binary_bytes=1024)),),
    )

    result = await executable.run(
        "Fetch",
        bindings=RunBindings.embedded(
            capabilities=(_policy(), WebRunCapability(client=client, policy=policy)),
        ),
    )

    assert result.output_or_raise() == "done"
    assert policy.calls == [
        ("https://example.com/image", "fetch"),
        ("https://cdn.example.com/image.png?X-Amz-Signature=secret", "fetch"),
    ]
    assert client.requests[0].max_redirects == 8
    assert closed == 1
    assert _native_binary(seen)[0].data == b"\x89PNG"
    tool_history = "\n".join(str(item) for item in _tool_contents(seen))
    assert "X-Amz-Signature" not in tool_history
    assert "secret" not in tool_history
    assert "https://cdn.example.com/image.png?variant=large" in tool_history


async def test_web_download_enforces_stream_limit_and_removes_partial_file(tmp_path: Path) -> None:
    policy = _WebPolicy()
    client = _WebClient(
        (
            WebResponse(
                status_code=200,
                final_url="https://cdn.example.com/file.bin",
                canonical_url="https://cdn.example.com/file.bin",
                headers={"Content-Type": "application/octet-stream"},
                body=_body(b"123", b"45"),
            ),
        )
    )
    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model(
            "download",
            {"urls": ["https://example.com/file.bin?token=secret"], "save_dir": "/workspace/downloads"},
            seen=seen,
        ),
        capabilities=(WebCapability(WebConfiguration(max_download_bytes=4)),),
    )

    result = await executable.run(
        "Download",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            capabilities=(_policy(), WebRunCapability(client=client, policy=policy)),
        ),
    )

    assert result.output_or_raise() == "done"
    contents = _tool_contents(seen)
    batch = next(item for item in contents if isinstance(item, list))
    assert batch[0]["error"]["code"] == "web_body_too_large"
    assert batch[0]["url"] == "https://example.com/file.bin"
    assert list((tmp_path / "downloads").iterdir()) == []


async def test_web_download_isolates_batch_failures_and_uses_response_media_type(tmp_path: Path) -> None:
    client = _WebClient(
        (
            WebResponse(
                status_code=200,
                final_url="https://cdn.example.com/looks-like.png",
                canonical_url="https://cdn.example.com/looks-like.png",
                headers={"Content-Type": "text/plain"},
                body=_body(b"saved"),
            ),
            WebResponse(
                status_code=503,
                final_url="https://example.com/unavailable.bin",
                canonical_url="https://example.com/unavailable.bin",
                headers={"Content-Type": "text/html"},
                body=_body(b"must-not-surface"),
                reason="Service Unavailable",
            ),
        )
    )
    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model(
            "download",
            {
                "urls": ["https://example.com/one.png", "https://example.com/unavailable.bin"],
                "save_dir": "/workspace/downloads",
            },
            seen=seen,
        ),
        capabilities=(WebCapability(WebConfiguration(search=WebSearchConfiguration(mode="off"))),),
    )

    result = await executable.run(
        "Download",
        bindings=RunBindings.embedded(
            environment=_binding(tmp_path),
            capabilities=(_policy(), WebRunCapability(client=client, policy=_WebPolicy())),
        ),
    )

    assert result.output_or_raise() == "done"
    batch = next(item for item in _tool_contents(seen) if isinstance(item, list))
    assert batch[0]["ok"] is True
    assert batch[0]["final_url"] == "https://cdn.example.com/looks-like.png"
    assert batch[0]["save_path"].endswith(".txt")
    assert (tmp_path / batch[0]["save_path"].removeprefix("/workspace/")).read_bytes() == b"saved"
    assert batch[1]["error"]["code"] == "web_http_status"
    assert batch[1]["error"]["status_code"] == 503
    assert "must-not-surface" not in json.dumps(batch[1])


async def test_web_fetch_body_deadline_is_finite() -> None:
    async def slow_body() -> AsyncIterator[bytes]:
        await asyncio.sleep(0.1)
        yield b"late"

    client = _WebClient(
        (
            WebResponse(
                status_code=200,
                final_url="https://example.com/slow.txt",
                canonical_url="https://example.com/slow.txt",
                headers={"Content-Type": "text/plain"},
                body=slow_body(),
            ),
        )
    )
    seen: list[list[ModelMessage]] = []
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_one_tool_model("fetch", {"url": "https://example.com/slow.txt"}, seen=seen),
        capabilities=(WebCapability(WebConfiguration(deadline_seconds=0.01)),),
    )

    result = await executable.run(
        "Fetch",
        bindings=RunBindings.embedded(
            capabilities=(_policy(), WebRunCapability(client=client, policy=_WebPolicy())),
        ),
    )

    assert result.output_or_raise() == "done"
    error = next(item for item in _tool_contents(seen) if isinstance(item, dict))
    assert error["error"]["code"] == "web_timeout"
