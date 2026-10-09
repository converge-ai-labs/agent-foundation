"""Reusable document-conversion Toolset over a Host-selected converter."""

from __future__ import annotations

import asyncio
import posixpath
from collections.abc import AsyncIterable
from pathlib import PurePosixPath
from typing import Annotated, Literal, NotRequired, Protocol, TypedDict, runtime_checkable
from uuid import uuid4

from a13n_environment.files import FileOperator
from a13n_environment.models import EnvironmentError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness.context import AgentContext
from a13n_harness.environment.providers import FileScopeProvider
from a13n_harness.errors import RunError
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from a13n_harness.usage import ProviderUsage

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolFailure, environment_failure, tool_failure
from ._scoped_files import ScopedFileAccess

_DOCUMENT_INSTRUCTIONS = (
    tool_instruction("pdf_convert"),
    tool_instruction("office_to_markdown"),
)

_SUPPORTED_OFFICE_EXTENSIONS = frozenset({".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".epub"})

type DocumentKind = Literal["pdf", "office"]


class DocumentConversionRequest(BaseModel):
    """Detached, bounded converter input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: DocumentKind
    source_name: str = Field(min_length=1, max_length=1024)
    source_bytes: bytes
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = None
    max_markdown_bytes: int = Field(gt=0)
    max_asset_bytes: int = Field(gt=0)
    max_total_asset_bytes: int = Field(gt=0)
    max_assets: int = Field(gt=0)
    deadline_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_pages(self) -> DocumentConversionRequest:
        if "\x00" in self.source_name:
            raise ValueError("document source name must not contain NUL")
        if self.kind == "office" and (self.page_start is not None or self.page_end is not None):
            raise ValueError("office conversion does not accept page ranges")
        if self.page_end is not None and self.page_end != -1 and self.page_end < 1:
            raise ValueError("page_end must be positive or -1")
        if self.page_end not in (None, -1) and self.page_start is not None and self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class DocumentAsset(BaseModel):
    """One relative exported asset."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
    )

    path: str = Field(min_length=1, max_length=2048)
    data: bytes
    media_type: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _validate_path(self) -> DocumentAsset:
        _validate_relative_asset_path(self.path)
        if self.media_type is not None and "\x00" in self.media_type:
            raise ValueError("asset media type must not contain NUL")
        return self


class DocumentConversionResult(BaseModel):
    """Provider result before Environment publication."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    markdown: str
    assets: tuple[DocumentAsset, ...] = ()
    total_pages: int | None = Field(default=None, ge=1)
    converted_pages: int | None = Field(default=None, ge=1)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    usage: tuple[ProviderUsage, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def _validate_page_result(self) -> DocumentConversionResult:
        if "\x00" in self.markdown:
            raise ValueError("converted markdown must not contain NUL")
        fields = (self.total_pages, self.converted_pages, self.page_start, self.page_end)
        if any(value is not None for value in fields) and not all(value is not None for value in fields):
            raise ValueError("PDF page metadata must be complete")
        if self.page_start is not None:
            assert self.total_pages is not None and self.converted_pages is not None and self.page_end is not None
            if self.page_end < self.page_start or self.page_end > self.total_pages:
                raise ValueError("PDF page metadata is inconsistent")
            if self.converted_pages != self.page_end - self.page_start + 1:
                raise ValueError("converted page count is inconsistent")
        paths = [asset.path for asset in self.assets]
        if len(set(paths)) != len(paths):
            raise ValueError("document asset paths must be unique")
        return self


class DocumentConversionError(Exception):
    """Stable provider failure safe to project to the model."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not code.strip() or len(code) > 128:
            raise ValueError("document error code must be a short non-blank string")
        self.code = code
        super().__init__(code)


@runtime_checkable
class DocumentConverter(Protocol):
    """Host-selected converter; native temp files and optional libraries stay behind this port."""

    async def convert(self, request: DocumentConversionRequest) -> DocumentConversionResult: ...


class DocumentsConfiguration(BaseModel):
    """Definition-owned source, result, and publication bounds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_source_bytes: int = Field(default=100 * 1024 * 1024, gt=0, le=1024 * 1024 * 1024)
    max_markdown_bytes: int = Field(default=16 * 1024 * 1024, gt=0, le=128 * 1024 * 1024)
    max_asset_bytes: int = Field(default=32 * 1024 * 1024, gt=0, le=256 * 1024 * 1024)
    max_total_asset_bytes: int = Field(default=128 * 1024 * 1024, gt=0, le=1024 * 1024 * 1024)
    max_assets: int = Field(default=512, gt=0, le=10_000)
    default_pdf_pages: int = Field(default=20, gt=0, le=10_000)
    deadline_seconds: float = Field(default=120.0, gt=0, le=600, allow_inf_nan=False)

    def model_post_init(self, __context: object) -> None:
        del __context
        if self.max_asset_bytes > self.max_total_asset_bytes:
            raise ValueError("max_asset_bytes cannot exceed max_total_asset_bytes")


class DocumentConversionSuccess(TypedDict):
    ok: Literal[True]
    export_path: str
    markdown_path: str
    asset_count: int
    total_pages: NotRequired[int]
    converted_pages: NotRequired[int]
    page_range: NotRequired[str]


type DocumentToolResult = DocumentConversionSuccess | ToolFailure


class DocumentsToolset:
    """Standard conversion and publication semantics reusable with any converter."""

    def __init__(
        self,
        converter: DocumentConverter,
        configuration: DocumentsConfiguration | None = None,
        *,
        files: FileOperator,
        file_scopes: FileScopeProvider | None = None,
    ) -> None:
        self._converter = converter
        self._file_access = ScopedFileAccess(files, file_scopes)
        self.configuration = (configuration or DocumentsConfiguration()).model_copy(deep=True)

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        common = {
            "effects": frozenset({"read", "write"}),
            "credential_audiences": (),
            "idempotency": "none",
            "output_policy": ToolOutputPolicy(
                max_inline_bytes=64 * 1024,
                max_output_bytes=256 * 1024,
                overflow="truncate",
                redact=True,
            ),
            "resource_resolver": self._file_access.resource_resolver("file_path", include_parent=True),
        }
        return InstructionFunctionToolset(
            tools=[
                HarnessTool(
                    self.pdf_convert,
                    harness_metadata=HarnessToolMetadata(tool_id="document.pdf_convert", **common),
                    name="pdf_convert",
                    description="Convert a bounded PDF page range to Markdown and publish extracted assets.",
                ),
                HarnessTool(
                    self.office_to_markdown,
                    harness_metadata=HarnessToolMetadata(tool_id="document.office_to_markdown", **common),
                    name="office_to_markdown",
                    description="Convert a bounded Office document or EPUB to Markdown and publish extracted assets.",
                ),
            ],
            id="a13n-document-tool-functions",
            instructions=_DOCUMENT_INSTRUCTIONS,
        )

    async def pdf_convert(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[str, Field(description="Logical Environment path to a PDF")],
        page_start: Annotated[int | None, Field(default=None, ge=1)] = None,
        page_end: Annotated[
            int | None, Field(default=None, description="Inclusive end page; -1 selects all pages")
        ] = None,
    ) -> DocumentToolResult:
        start = page_start or 1
        end = page_end if page_end is not None else start + self.configuration.default_pdf_pages - 1
        return await self._convert(ctx, file_path, kind="pdf", page_start=start, page_end=end)

    async def office_to_markdown(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[str, Field(description="Logical Environment path to an Office document or EPUB")],
    ) -> DocumentToolResult:
        return await self._convert(ctx, file_path, kind="office", page_start=None, page_end=None)

    async def _convert(
        self,
        ctx: RunContext[AgentContext],
        file_path: str,
        *,
        kind: DocumentKind,
        page_start: int | None,
        page_end: int | None,
    ) -> DocumentToolResult:
        converter = self._converter
        try:
            extension, stem, parent = _document_path(file_path)
            if kind == "pdf" and extension != ".pdf":
                return _document_error("document_format_invalid")
            if kind == "office" and extension not in _SUPPORTED_OFFICE_EXTENSIONS:
                return _document_error("document_format_unsupported")
            async with self._file_access.scope(file_path) as files:
                metadata = await files.stat(file_path)
                if metadata.kind != "file":
                    return _document_error("document_source_invalid")
                if metadata.size is not None and metadata.size > self.configuration.max_source_bytes:
                    return _document_error("document_source_too_large", max_bytes=self.configuration.max_source_bytes)
                source = await _read_bounded(
                    files.read_bytes_stream(file_path),
                    self.configuration.max_source_bytes,
                )
                request = DocumentConversionRequest(
                    kind=kind,
                    source_name=f"{stem}{extension}",
                    source_bytes=source,
                    page_start=page_start,
                    page_end=page_end,
                    max_markdown_bytes=self.configuration.max_markdown_bytes,
                    max_asset_bytes=self.configuration.max_asset_bytes,
                    max_total_asset_bytes=self.configuration.max_total_asset_bytes,
                    max_assets=self.configuration.max_assets,
                    deadline_seconds=self.configuration.deadline_seconds,
                )
                async with asyncio.timeout(self.configuration.deadline_seconds):
                    raw_result = await converter.convert(request)
                result = DocumentConversionResult.model_validate(raw_result)
                for usage in result.usage:
                    await ctx.deps.record_provider_usage(
                        usage,
                        source="documents.converter",
                        tool_id=f"document.{kind}_convert" if kind == "pdf" else "document.office_to_markdown",
                        tool_call_id=ctx.tool_call_id,
                    )
                self._validate_result(
                    result,
                    kind=kind,
                    requested_page_start=page_start,
                    requested_page_end=page_end,
                )
                return await self._publish(files, stem=stem, parent=parent, kind=kind, result=result)
        except TimeoutError:
            return _document_error("document_timeout", retry_hint="retry")
        except RunError:
            raise
        except DocumentConversionError as exc:
            return _document_error(exc.code)
        except EnvironmentError as exc:
            return environment_failure(exc)
        except (TypeError, ValueError):
            return _document_error("document_response_invalid")
        except Exception:
            return _document_error("document_conversion_failed")

    async def _publish(
        self,
        files: FileOperator,
        *,
        stem: str,
        parent: str,
        kind: DocumentKind,
        result: DocumentConversionResult,
    ) -> DocumentConversionSuccess:
        export_name = _export_name(stem, kind=kind, result=result)
        target = _join(parent, export_name)
        staging: str | None = _join(parent, f".{export_name}.a13n-{uuid4().hex}")
        try:
            await files.mkdir(staging, parents=False, exist_ok=False)
            markdown_path = _join(staging, f"{stem}.md")
            await files.write_text(markdown_path, result.markdown, mode="create")
            for asset in result.assets:
                destination = _join(staging, asset.path)
                asset_parent = posixpath.dirname(destination)
                if asset_parent != staging:
                    await files.mkdir(asset_parent, parents=True, exist_ok=True)
                await files.write_bytes_stream(
                    destination,
                    _one_chunk(asset.data),
                    mode="create",
                )
            await files.move(staging, target, replace=False)
            staging = None
            response: DocumentConversionSuccess = {
                "ok": True,
                "export_path": target,
                "markdown_path": _join(target, f"{stem}.md"),
                "asset_count": len(result.assets),
            }
            if result.total_pages is not None:
                assert result.converted_pages is not None
                assert result.page_start is not None and result.page_end is not None
                response["total_pages"] = result.total_pages
                response["converted_pages"] = result.converted_pages
                response["page_range"] = f"{result.page_start}-{result.page_end}"
            return response
        finally:
            if staging is not None:
                try:
                    await files.remove(staging, recursive=True)
                except EnvironmentError as exc:
                    if exc.code != "environment_not_found":
                        raise

    def _validate_result(
        self,
        result: DocumentConversionResult,
        *,
        kind: DocumentKind,
        requested_page_start: int | None,
        requested_page_end: int | None,
    ) -> None:
        if len(result.markdown.encode("utf-8")) > self.configuration.max_markdown_bytes:
            raise DocumentConversionError("document_markdown_too_large")
        if len(result.assets) > self.configuration.max_assets:
            raise DocumentConversionError("document_assets_too_many")
        total = 0
        for asset in result.assets:
            if len(asset.data) > self.configuration.max_asset_bytes:
                raise DocumentConversionError("document_asset_too_large")
            total += len(asset.data)
            if total > self.configuration.max_total_asset_bytes:
                raise DocumentConversionError("document_assets_too_large")
        if kind == "pdf":
            if result.total_pages is None:
                raise DocumentConversionError("document_page_metadata_missing")
            assert result.page_start is not None and result.page_end is not None
            assert requested_page_start is not None and requested_page_end is not None
            expected_end = (
                result.total_pages if requested_page_end == -1 else min(requested_page_end, result.total_pages)
            )
            if result.page_start != requested_page_start or result.page_end != expected_end:
                raise DocumentConversionError("document_page_metadata_invalid")
        elif result.total_pages is not None:
            raise DocumentConversionError("document_page_metadata_unexpected")


def _document_path(path: str) -> tuple[str, str, str]:
    if not isinstance(path, str) or not path.strip() or "\x00" in path or path.endswith("/"):
        raise ValueError("invalid document path")
    name = posixpath.basename(path)
    stem, extension = posixpath.splitext(name)
    if not stem or not extension:
        raise ValueError("document path must have a file extension")
    parent = posixpath.dirname(path)
    return extension.lower(), stem, parent


def _export_name(stem: str, *, kind: DocumentKind, result: DocumentConversionResult) -> str:
    if kind == "office":
        return f"export_{stem}"
    assert result.page_start is not None and result.page_end is not None
    return f"export_{stem}_pages_{result.page_start}_{result.page_end}"


def _validate_relative_asset_path(path: str) -> None:
    if "\x00" in path or "\\" in path:
        raise ValueError("asset path is invalid")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("asset path must be a confined relative path")


def _join(parent: str, child: str) -> str:
    return f"{parent.rstrip('/')}/{child.lstrip('/')}" if parent else child.lstrip("/")


async def _read_bounded(stream: AsyncIterable[bytes], limit: int) -> bytes:
    output = bytearray()
    async for chunk in stream:
        if not isinstance(chunk, bytes):
            raise DocumentConversionError("document_source_invalid")
        output.extend(chunk)
        if len(output) > limit:
            raise DocumentConversionError("document_source_too_large")
    return bytes(output)


async def _one_chunk(data: bytes):
    yield data


def _document_error(
    code: str,
    *,
    max_bytes: int | None = None,
    retry_hint: str = "dependency_change",
) -> ToolFailure:
    message = {
        "document_source_invalid": "Document source is invalid; select a supported regular document file.",
        "document_source_too_large": "Document source exceeds the configured byte limit.",
        "document_format_invalid": "The document format is invalid.",
        "document_format_unsupported": "The converter does not support this document format.",
        "document_asset_too_large": "A converted document asset exceeds the byte limit.",
        "document_assets_too_large": "Converted document assets exceed the total byte limit.",
        "document_assets_too_many": "The converter returned too many document assets.",
        "document_markdown_too_large": "Converted Markdown exceeds the output limit; select fewer pages.",
        "document_page_metadata_missing": "The converter omitted required page metadata.",
        "document_page_metadata_invalid": "The converter returned invalid page metadata.",
        "document_page_metadata_unexpected": "The converter returned page metadata for a non-paged format.",
        "document_response_invalid": "The document converter returned an invalid result.",
        "document_timeout": "Document conversion timed out; check the converter and requested page range.",
    }.get(code, "Document conversion failed; check the source format and configured converter.")
    result = tool_failure(code, message, retry_hint=retry_hint)
    if max_bytes is not None:
        result["error"]["max_bytes"] = max_bytes
    return result


__all__ = [
    "DocumentAsset",
    "DocumentConversionError",
    "DocumentConversionRequest",
    "DocumentConversionResult",
    "DocumentConversionSuccess",
    "DocumentConverter",
    "DocumentKind",
    "DocumentToolResult",
    "DocumentsConfiguration",
    "DocumentsToolset",
]
