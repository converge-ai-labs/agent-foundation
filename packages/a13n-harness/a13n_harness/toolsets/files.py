"""Reusable model-facing file Toolset over the provider-neutral FileOperator."""

from __future__ import annotations

import asyncio
import fnmatch
import posixpath
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from pydantic_ai import BinaryContent, RunContext, ToolReturn
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._json import redact_json
from a13n_harness.context import AgentContext, ToolMetadataKey
from a13n_harness.environment._mount_path import normalize_operation_path
from a13n_harness.environment._resources import EnvironmentResources
from a13n_harness.environment.providers import BoundEnvironment, FileScopeProvider
from a13n_harness.errors import HarnessError
from a13n_harness.events import FileChangeProjection, FilesystemChangedValue, emit_tool_event
from a13n_harness.filters.cold_start import COLD_START_RETENTION_METADATA_KEY
from a13n_harness.metering import ModelUsageBinding
from a13n_harness.providers.environment.files import (
    FileMetadata,
    FileOperator,
    FileQueryRequest,
    FileTextResult,
    FileTextSearchRequest,
)
from a13n_harness.providers.environment.models import EnvironmentAction, EnvironmentError, EnvironmentPath
from a13n_harness.spec import ModelCapability
from a13n_harness.tools.metadata import (
    CanonicalResource,
    HarnessTool,
    HarnessToolMetadata,
    ToolEffect,
    ToolOutputPolicy,
    ToolResourceResolver,
)
from a13n_harness.usage import ProviderUsage

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolError, ToolFailure, environment_failure, tool_failure
from ._scoped_files import ScopedFileAccess
from .events import FileEditAppliedEvent
from .file_media import (
    MAX_MEDIA_UNDERSTANDING_BYTES,
    MediaUnderstandingError,
    MediaUnderstandingProvider,
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
    NativeInputMediaKind,
)
from .file_results import (
    FileCopyItem,
    FileCopyToolResult,
    FileDeleteResult,
    FileEditResult,
    FileGlobResult,
    FileGrepResult,
    FileListResult,
    FileMetadataProjection,
    FileMkdirResult,
    FileMoveResult,
    FileMutationItem,
    FilePathPairItem,
    FileViewResult,
    FileWriteResult,
)
from .output import (
    DEFAULT_TOOL_OUTPUT_CHARS,
    FINAL_TOOL_OUTPUT_HARD_CHARS,
    acknowledge_tool_output,
    continuation_disclosure,
    create_tool_output_disclosure,
    disclose_mapping_field,
    disclose_sequence_field,
    disclose_text_fields,
    fit_text_fields_to_limit,
    tool_output_size,
)

_MAX_MODEL_TEXT_BYTES = 256 * 1024
_MAX_MODEL_TEXT_PAGE_BYTES = 4 * 1024 * 1024
_MAX_MODEL_EDIT_BYTES = 16 * 1024 * 1024
_MAX_MODEL_RESULTS = 1_000
_MAX_MODEL_MEDIA_BYTES = MAX_MEDIA_UNDERSTANDING_BYTES
_MAX_MODEL_TEXT_RULE_PAGE_BYTES = 16 * 1024 * 1024
_SKILL_MARKDOWN_LINE_LIMIT = 800
_SKILL_MARKDOWN_MAX_LINE_LENGTH = 20_000

_FILE_TOOL_ACTIONS = {
    "write": EnvironmentAction.FILE_WRITE_TEXT,
    # Empty-old-string edits can create a file without reading it.
    "edit": EnvironmentAction.FILE_WRITE_TEXT,
    "multi_edit": EnvironmentAction.FILE_WRITE_TEXT,
    "mkdir": EnvironmentAction.FILE_MKDIR,
    "move": EnvironmentAction.FILE_MOVE,
    "delete": EnvironmentAction.FILE_REMOVE,
    "ls": EnvironmentAction.FILE_LIST,
    "glob": EnvironmentAction.FILE_QUERY,
    "grep": EnvironmentAction.FILE_SEARCH_TEXT,
}


type _UnlimitedOrPositiveResults = Literal[-1] | Annotated[int, Field(gt=0, le=_MAX_MODEL_RESULTS)]


@dataclass(frozen=True, slots=True)
class FileViewRule:
    """One typed run-scoped widening rule for text file views."""

    roots: tuple[EnvironmentPath, ...]
    suffixes: tuple[str, ...]
    initial_line_limit: int | None = None
    max_line_length: int | None = None
    page_bytes: int | None = None
    semantic_output_chars: int | None = None
    preserve_complete_lines: bool = False

    def __post_init__(self) -> None:
        roots = tuple(self.roots)
        suffixes = tuple(suffix.casefold() for suffix in self.suffixes)
        if not roots or not all(isinstance(root, EnvironmentPath) for root in roots):
            raise ValueError("FileViewRule roots must contain resolved EnvironmentPath values")
        if not suffixes or any(not suffix.startswith(".") or "/" in suffix or "\x00" in suffix for suffix in suffixes):
            raise ValueError("FileViewRule suffixes must contain file suffixes beginning with '.'")
        for name in ("initial_line_limit", "max_line_length", "page_bytes", "semantic_output_chars"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
                raise ValueError(f"FileViewRule {name} must be a positive integer")
        object.__setattr__(self, "roots", roots)
        object.__setattr__(self, "suffixes", suffixes)


FILE_VIEW_RULES = ToolMetadataKey("a13n.files.view-rules", FileViewRule)


@dataclass(frozen=True, slots=True)
class _FileViewProfile:
    preserve_on_cold_start: bool
    initial_line_limit: int
    max_line_length: int
    page_bytes: int
    semantic_output_chars: int
    preserve_complete_lines: bool


@dataclass(frozen=True, slots=True)
class _FileEditOutcome:
    path: str
    bytes_written: int
    before: str | None
    after: str

    @property
    def changed(self) -> bool:
        return self.before != self.after


_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
}


class FileTextEdit(BaseModel):
    """One exact replacement in a multi-edit call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    old_string: str = Field(description="Text to replace (must match exactly, including whitespace and indentation)")
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(default=False, description="Replace every occurrence instead of one unique match")


class FilePathPair(BaseModel):
    """One source and destination pair for a file mutation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    src: str = Field(min_length=1, description="Logical source path")
    dst: str = Field(min_length=1, description="Logical destination path")


class FileToolset:
    """Standard file-tool semantics reusable with any FileOperator implementation."""

    def __init__(
        self,
        files: FileOperator,
        *,
        resource_resolver: Callable[[str], ToolResourceResolver] | None = None,
        execution_guard: Callable[[], None] | None = None,
        file_scopes: FileScopeProvider | None = None,
        media_understanding: (
            MediaUnderstandingProvider
            | Callable[[RunContext[AgentContext], NativeInputMediaKind], MediaUnderstandingProvider | None]
            | None
        ) = None,
    ) -> None:
        self._files = files
        self._file_access = ScopedFileAccess(files, file_scopes)
        self._has_file_scopes = file_scopes is not None
        self._resources = EnvironmentResources(file_scopes) if isinstance(file_scopes, BoundEnvironment) else None
        self._resource_resolver = resource_resolver
        self._execution_guard = execution_guard
        self._media_understanding = media_understanding
        self._mutation_lock = asyncio.Lock()

    @staticmethod
    def available_names(mount_actions: Sequence[frozenset[EnvironmentAction]]) -> frozenset[str]:
        """Expose tools with at least one valid branch; facades check actual arguments."""
        names = {
            name for name, action in _FILE_TOOL_ACTIONS.items() if any(action in actions for actions in mount_actions)
        }
        if any(
            EnvironmentAction.FILE_READ_TEXT in actions
            or {EnvironmentAction.FILE_STAT, EnvironmentAction.FILE_READ_BYTES} <= actions
            for actions in mount_actions
        ):
            names.add("view")
        if any(EnvironmentAction.FILE_COPY_SOURCE in actions for actions in mount_actions) and any(
            EnvironmentAction.FILE_COPY_DESTINATION in actions for actions in mount_actions
        ):
            names.add("copy")
        return frozenset(names)

    def get_toolset(
        self,
        *,
        shell_active: bool = False,
        include_mutations: bool = True,
        allowed_names: frozenset[str] | None = None,
    ) -> FunctionToolset[AgentContext]:
        tools = (
            self._tool(
                self.view,
                "filesystem.view",
                {"read"},
                "read_only",
                resources=self._path_resources(lambda arguments: (_string_argument(arguments, "file_path"),)),
                name="view",
                description="Read bounded text segments or attach common image, video, and audio files natively.",
                max_output_bytes=_MAX_MODEL_MEDIA_BYTES,
            ),
            self._tool(
                self.write,
                "filesystem.write",
                {"write"},
                "none",
                resources=self._path_resources(lambda arguments: (_string_argument(arguments, "file_path"),)),
                name="write",
                description="Write, overwrite, or append text to a file.",
            ),
            self._tool(
                self.edit,
                "filesystem.edit",
                {"read", "write"},
                "none",
                resources=self._path_resources(lambda arguments: (_string_argument(arguments, "file_path"),)),
                name="edit",
                description="Perform one exact string replacement; an empty old_string creates a file.",
            ),
            self._tool(
                self.multi_edit,
                "filesystem.multi_edit",
                {"read", "write"},
                "none",
                resources=self._path_resources(lambda arguments: (_string_argument(arguments, "file_path"),)),
                name="multi_edit",
                description="Apply multiple exact replacements to one file in sequence.",
            ),
            self._tool(
                self.mkdir,
                "filesystem.mkdir",
                {"write"},
                "none",
                resources=self._path_resources(
                    lambda arguments: _string_sequence_argument(arguments, "paths"), allow_unresolved=False
                ),
                name="mkdir",
                description="Create multiple directories in one bounded batch.",
            ),
            self._tool(
                self.move,
                "filesystem.move",
                {"read", "write", "delete"},
                "none",
                resources=self._path_resources(_pair_paths, allow_unresolved=False),
                name="move",
                description="Move files or directories using source and destination pairs.",
                superseded_by_tool_ids={"environment.shell_exec"} if shell_active else None,
            ),
            self._tool(
                self.copy,
                "filesystem.copy",
                {"read", "write"},
                "none",
                resources=self._path_resources(_pair_paths, allow_unresolved=False),
                name="copy",
                description="Copy files, including streaming copies across Environment mounts.",
                superseded_by_tool_ids={"environment.shell_exec"} if shell_active else None,
            ),
            self._tool(
                self.delete,
                "filesystem.remove",
                {"delete"},
                "none",
                resources=self._path_resources(
                    lambda arguments: _string_sequence_argument(arguments, "paths"), allow_unresolved=False
                ),
                name="delete",
                description="Delete files or directories with explicit recursive and force controls.",
                superseded_by_tool_ids={"environment.shell_exec"} if shell_active else None,
            ),
            self._tool(
                self.ls,
                "filesystem.ls",
                {"read"},
                "read_only",
                resources=self._path_resources(lambda arguments: (_string_argument(arguments, "path"),)),
                name="ls",
                description="List one directory with bounded file metadata.",
            ),
            self._tool(
                self.glob,
                "filesystem.glob",
                {"read"},
                "read_only",
                resources=self._path_resources(
                    lambda arguments: (_optional_string_argument(arguments, "root") or ".",)
                ),
                name="glob",
                description="Find paths by glob pattern; bare patterns match recursively.",
            ),
            self._tool(
                self.grep,
                "filesystem.grep",
                {"read"},
                "read_only",
                resources=self._path_resources(
                    lambda arguments: (_optional_string_argument(arguments, "root") or ".",)
                ),
                name="grep",
                description="Search text file contents with a regular expression.",
            ),
        )
        if not include_mutations:
            tools = tuple(tool for tool in tools if tool.name in {"view", "ls", "glob", "grep"})
        if allowed_names is not None:
            tools = tuple(tool for tool in tools if tool.name in allowed_names)
        instruction_names = {"view", "write", "edit", "multi_edit", "ls", "glob", "grep"}
        if not shell_active:
            instruction_names.update({"move", "copy", "delete"})
        instructions = [tool_instruction(tool.name) for tool in tools if tool.name in instruction_names]
        return InstructionFunctionToolset(
            tools=tools,
            id="a13n-file-tools",
            instructions=instructions,
        )

    def _tool(
        self,
        function: Callable[..., object],
        tool_id: str,
        effects: set[ToolEffect],
        idempotency: Literal["none", "read_only"],
        *,
        resources: ToolResourceResolver | None,
        name: str,
        description: str,
        max_output_bytes: int = 4 * 1024 * 1024,
        superseded_by_tool_ids: set[str] | None = None,
    ) -> HarnessTool:
        return HarnessTool(
            function,
            harness_metadata=HarnessToolMetadata(
                tool_id=tool_id,
                effects=frozenset(effects),
                credential_audiences=(),
                idempotency=idempotency,
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=256 * 1024,
                    max_output_bytes=max_output_bytes,
                    overflow="truncate",
                    redact=True,
                ),
                resource_resolver=(
                    self._resource_resolver(tool_id) if self._resource_resolver is not None else resources
                ),
                superseded_by_tool_ids=frozenset(superseded_by_tool_ids or ()),
            ),
            name=name,
            description=description,
        )

    def _path_resources(
        self,
        paths: Callable[[Mapping[str, object]], tuple[str, ...]],
        *,
        allow_unresolved: bool = True,
    ) -> ToolResourceResolver | None:
        resources = self._resources
        if resources is None:
            return None

        async def resolve(arguments: Mapping[str, object], *, context: AgentContext) -> tuple[CanonicalResource, ...]:
            return await resources.paths(paths(arguments))

        return resources.resolver(resolve, allow_unresolved=allow_unresolved)

    async def view(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[
            str,
            Field(
                description="Logical file path; use an advertised absolute mount root to select a non-default Environment."
            ),
        ],
        line_offset: Annotated[int | None, Field(default=None, ge=0)] = None,
        line_limit: Annotated[int, Field(default=300, gt=0, le=_MAX_MODEL_RESULTS)] = 300,
        max_line_length: Annotated[int, Field(default=2_000, gt=0, le=_MAX_MODEL_TEXT_BYTES)] = 2_000,
        instructions: Annotated[
            str | None,
            Field(default=None, max_length=64 * 1024, description="Focused media analysis instructions"),
        ] = None,
    ) -> FileViewResult:
        """Read bounded text or attach a common media file natively."""
        extension = posixpath.splitext(file_path)[1].casefold()
        if extension == ".pdf":
            return tool_failure(
                "document_conversion_required",
                "Convert the PDF before reading its text or page layout.",
                retry_hint="request_change",
                details={"tool": "pdf_convert"},
            )
        profile = _file_view_profile(ctx.deps, file_path)
        retention_metadata = {COLD_START_RETENTION_METADATA_KEY: "preserve"} if profile.preserve_on_cold_start else None
        media_type = _MEDIA_TYPES.get(extension)
        if media_type is not None:
            try:
                async with self._file_access.scope(file_path) as files:
                    self._guard_execution()
                    metadata = await files.stat(file_path)
                    if metadata.kind != "file":
                        raise EnvironmentError(
                            "Media view source is not a file.",
                            code="environment_request_invalid",
                        )
                    if metadata.size is not None and metadata.size > _MAX_MODEL_MEDIA_BYTES:
                        raise EnvironmentError(
                            "Media file exceeds the model view limit.",
                            code="environment_too_large",
                        )
                    self._guard_unscoped_step()
                    data = await files.read_bytes(
                        file_path,
                        length=_MAX_MODEL_MEDIA_BYTES + 1,
                    )
                    if len(data) > _MAX_MODEL_MEDIA_BYTES:
                        raise EnvironmentError(
                            "Media file exceeds the model view limit.",
                            code="environment_too_large",
                        )
                    source = self._file_access.resolved_path(file_path)
            except EnvironmentError as exc:
                return _environment_error_result(exc)

            kind = cast(NativeInputMediaKind, media_type.split("/", maxsplit=1)[0])
            if _model_supports_native_media(ctx, kind):
                message = f"The {media_type} file {file_path} is attached in the user message."
                return ToolReturn(
                    return_value=message,
                    metadata=retention_metadata,
                    # Native tool attachments may be stored in a UserPromptPart,
                    # but they are model content, not a new authored user turn.
                    content=[BinaryContent(data=data, media_type=media_type, vendor_metadata={"display": False})],
                )

            try:
                provider = self._resolve_media_understanding(ctx, kind)
            except (HarnessError, UsageLimitExceeded):
                raise
            except MediaUnderstandingError as exc:
                await _record_media_understanding_usage(ctx, exc.usage)
                return _media_understanding_error(exc.code)
            except Exception:
                return _media_understanding_error("media_understanding_failed")
            if provider is None:
                return _media_understanding_error("media_understanding_unavailable")

            request = MediaUnderstandingRequest(
                kind=kind,
                media_type=media_type,
                source=source,
                source_name=file_path,
                source_bytes=data,
                instructions=instructions,
            )
            try:
                raw = await provider.understand(
                    request,
                    usage=ModelUsageBinding.for_context(
                        ctx.deps,
                        source="files.media_understanding",
                        tool_id="filesystem.view",
                        tool_call_id=ctx.tool_call_id,
                    ),
                )
            except (HarnessError, UsageLimitExceeded):
                raise
            except MediaUnderstandingError as exc:
                await _record_media_understanding_usage(ctx, exc.usage)
                return _media_understanding_error(exc.code)
            except Exception:
                return _media_understanding_error("media_understanding_failed")
            try:
                result = MediaUnderstandingResult.model_validate(raw)
            except ValidationError:
                return _media_understanding_error("media_understanding_response_invalid")
            await _record_media_understanding_usage(ctx, result.usage)
            return ToolReturn(return_value=result.text, metadata=retention_metadata)

        effective_line_limit = line_limit
        if line_offset in {None, 0}:
            effective_line_limit = max(effective_line_limit, profile.initial_line_limit)
        effective_max_line_length = max(max_line_length, profile.max_line_length)

        async def disclose(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
            if (
                profile.preserve_complete_lines
                or bool(value.get("has_more"))
                or bool(value.get("truncated_lines"))
                or tool_output_size(value) > profile.semantic_output_chars
            ):
                return await _disclose_line_preserving_file_page(
                    ctx.deps,
                    value,
                    limit=profile.semantic_output_chars,
                )
            return await disclose_text_fields(
                ctx.deps,
                value,
                text_fields=("content",),
                content_complete=not bool(value.get("has_more")),
                noun="file page",
                limit=profile.semantic_output_chars,
            )

        viewed = await self._execute(
            file_path,
            lambda files: self._read_text_page(
                files,
                file_path,
                line_offset=line_offset or 0,
                line_limit=effective_line_limit,
                max_line_length=effective_max_line_length,
                page_bytes=profile.page_bytes,
            ),
            lambda result: {
                "file_path": result.path,
                "content": result.text,
                "line_offset": result.line_offset,
                "lines_read": result.lines_read,
                "has_more": result.has_more,
                "truncated_lines": list(result.truncated_lines),
            },
            disclose=disclose,
        )
        if viewed.get("ok") is True and retention_metadata is not None:
            return ToolReturn(return_value=viewed, metadata=retention_metadata)
        return viewed

    async def _read_text_page(
        self,
        files: FileOperator,
        path: str,
        *,
        line_offset: int,
        line_limit: int,
        max_line_length: int,
        page_bytes: int,
    ) -> FileTextResult:
        # Bound each provider call without turning worst-case line width into
        # the total row limit. Short lines can fill the requested page.
        lines: list[str] = []
        truncated_lines: list[int] = []
        remaining_bytes = page_bytes
        has_more = True
        result_path = path
        while len(lines) < line_limit and remaining_bytes > 0 and has_more:
            self._guard_unscoped_step()
            batch = await files.read_text(
                path,
                line_offset=line_offset + len(lines),
                line_limit=min(line_limit - len(lines), max(1, remaining_bytes // (4 * max_line_length + 1))),
                max_line_length=max_line_length,
            )
            result_path = batch.path
            has_more = batch.has_more
            if batch.lines_read == 0:
                break
            for line in _lf_lines(batch.text) or [""]:
                size = len(line.encode("utf-8"))
                if size > remaining_bytes:
                    has_more = True
                    remaining_bytes = 0
                    break
                lines.append(line)
                remaining_bytes -= size
            truncated_lines.extend(number for number in batch.truncated_lines if number <= line_offset + len(lines))
        return FileTextResult(
            path=result_path,
            text="".join(lines),
            line_offset=line_offset,
            lines_read=len(lines),
            has_more=has_more,
            truncated_lines=tuple(truncated_lines),
        )

    async def write(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[
            str,
            Field(description="Logical destination path; an advertised absolute mount root selects the Environment."),
        ],
        content: Annotated[str, Field(description="Complete text to write or text to append")],
        mode: Annotated[Literal["w", "a"], Field(default="w")] = "w",
    ) -> FileWriteResult:
        """Write or append text, creating parent directories when needed."""

        async def operation(files: FileOperator):
            async with self._mutation_lock:
                self._guard_execution()
                await self._ensure_parent(files, file_path)
                self._guard_unscoped_step()
                return await files.write_text(
                    file_path,
                    content,
                    mode="append" if mode == "a" else "upsert",
                )

        result = await self._execute(
            file_path,
            operation,
            lambda result: {"file_path": result.path, "bytes_written": result.bytes_written},
        )
        if result["ok"] and (mode != "a" or content):
            await _emit_filesystem_changed(
                ctx,
                tool_id="filesystem.write",
                changes=(FileChangeProjection(path=result["file_path"], action="written"),),
            )
        return result

    async def edit(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[
            str, Field(description="Logical file path; an advertised absolute mount root selects the Environment.")
        ],
        old_string: Annotated[
            str,
            Field(description="Exact text to replace; an empty value creates a new file"),
        ],
        new_string: Annotated[str, Field(description="Replacement text")],
        replace_all: Annotated[bool, Field(default=False)] = False,
    ) -> FileEditResult:
        """Apply one exact replacement without requiring a unified diff."""
        return await self._apply_edits(
            ctx,
            file_path,
            (FileTextEdit(old_string=old_string, new_string=new_string, replace_all=replace_all),),
            tool_id="filesystem.edit",
        )

    async def multi_edit(
        self,
        ctx: RunContext[AgentContext],
        file_path: Annotated[
            str, Field(description="Logical file path; an advertised absolute mount root selects the Environment.")
        ],
        edits: Annotated[
            Sequence[FileTextEdit],
            Field(description="Exact replacements applied in order", min_length=1, max_length=256),
        ],
    ) -> FileEditResult:
        """Validate all replacements in memory, then publish one final file write."""
        return await self._apply_edits(
            ctx,
            file_path,
            tuple(edits),
            tool_id="filesystem.multi_edit",
        )

    async def mkdir(
        self,
        ctx: RunContext[AgentContext],
        paths: Annotated[
            Sequence[str],
            Field(description="Directory paths to create", min_length=1, max_length=256),
        ],
        parents: Annotated[bool, Field(description="Create missing parent directories")] = False,
    ) -> FileMkdirResult:
        """Create a bounded batch of directories and report each outcome."""
        results: list[FileMutationItem] = []
        async with self._mutation_lock:
            for path in paths:
                try:
                    async with self._file_access.scope(path) as files:
                        self._guard_execution()
                        await files.mkdir(path, parents=parents, exist_ok=False)
                    results.append({"ok": True, "path": path})
                except EnvironmentError as exc:
                    results.append({"ok": False, "path": path, "error": _environment_tool_error(exc)})
        changes = tuple(FileChangeProjection(path=item["path"], action="created") for item in results if item["ok"])
        await _emit_filesystem_changed(ctx, tool_id="filesystem.mkdir", changes=changes)
        return {"ok": all(item["ok"] for item in results), "results": results, "count": len(results)}

    async def move(
        self,
        ctx: RunContext[AgentContext],
        pairs: Annotated[
            Sequence[FilePathPair],
            Field(description="Source and destination pairs to move", min_length=1, max_length=256),
        ],
        overwrite: Annotated[bool, Field(description="Replace existing destinations")] = False,
    ) -> FileMoveResult:
        """Move a bounded batch of files or directories within their mounts."""
        results: list[FilePathPairItem] = []
        async with self._mutation_lock:
            for pair in pairs:
                try:
                    await self._file_access.move(
                        pair.src,
                        pair.dst,
                        replace=overwrite,
                        guard=self._guard_execution,
                    )
                    results.append({"ok": True, "src": pair.src, "dst": pair.dst})
                except EnvironmentError as exc:
                    results.append(
                        {
                            "ok": False,
                            "src": pair.src,
                            "dst": pair.dst,
                            "error": _environment_tool_error(exc),
                        }
                    )
        changes = tuple(
            FileChangeProjection(path=item["src"], action="moved", destination=item["dst"])
            for item in results
            if item["ok"]
        )
        await _emit_filesystem_changed(ctx, tool_id="filesystem.move", changes=changes)
        return {"ok": all(item["ok"] for item in results), "results": results, "count": len(results)}

    async def copy(
        self,
        ctx: RunContext[AgentContext],
        pairs: Annotated[
            Sequence[FilePathPair],
            Field(description="Source and destination pairs to copy", min_length=1, max_length=256),
        ],
        overwrite: Annotated[bool, Field(description="Replace existing destinations")] = False,
    ) -> FileCopyToolResult:
        """Copy a bounded batch of files, including across Environment mounts."""
        results: list[FileCopyItem] = []
        async with self._mutation_lock:
            for pair in pairs:
                try:
                    result = await self._file_access.copy(
                        pair.src,
                        pair.dst,
                        replace=overwrite,
                        guard=self._guard_execution,
                    )
                    results.append(
                        {
                            "ok": True,
                            "src": pair.src,
                            "dst": pair.dst,
                            "bytes_copied": result.bytes_copied,
                        }
                    )
                except EnvironmentError as exc:
                    results.append(
                        {
                            "ok": False,
                            "src": pair.src,
                            "dst": pair.dst,
                            "error": _environment_tool_error(exc),
                        }
                    )
        changes = tuple(
            FileChangeProjection(path=item["src"], action="copied", destination=item["dst"])
            for item in results
            if item["ok"]
        )
        await _emit_filesystem_changed(ctx, tool_id="filesystem.copy", changes=changes)
        return {"ok": all(item["ok"] for item in results), "results": results, "count": len(results)}

    async def delete(
        self,
        ctx: RunContext[AgentContext],
        paths: Annotated[
            Sequence[str],
            Field(description="File or directory paths to delete", min_length=1, max_length=256),
        ],
        recursive: Annotated[bool, Field(description="Delete non-empty directories recursively")] = False,
        force: Annotated[bool, Field(description="Treat missing paths as successful no-ops")] = False,
    ) -> FileDeleteResult:
        """Delete a bounded batch while preserving provider root protections."""
        results: list[FileMutationItem] = []
        deleted_paths: list[str] = []
        async with self._mutation_lock:
            for path in paths:
                try:
                    async with self._file_access.scope(path) as files:
                        self._guard_execution()
                        await files.remove(path, recursive=recursive)
                    results.append({"ok": True, "path": path})
                    deleted_paths.append(path)
                except EnvironmentError as exc:
                    if force and exc.code == "environment_not_found":
                        results.append({"ok": True, "path": path})
                    else:
                        results.append({"ok": False, "path": path, "error": _environment_tool_error(exc)})
        changes = tuple(FileChangeProjection(path=path, action="deleted") for path in deleted_paths)
        await _emit_filesystem_changed(ctx, tool_id="filesystem.remove", changes=changes)
        return {"ok": all(item["ok"] for item in results), "results": results, "count": len(results)}

    async def ls(
        self,
        ctx: RunContext[AgentContext],
        path: Annotated[str, Field(description="Logical directory path")],
        ignore: Annotated[Sequence[str] | None, Field(default=None)] = None,
        offset: Annotated[int, Field(default=0, ge=0)] = 0,
        max_results: _UnlimitedOrPositiveResults = 500,
    ) -> FileListResult:
        """List one directory and optionally omit matching entry names."""
        limit = _MAX_MODEL_RESULTS if max_results == -1 else max_results

        async def disclose(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
            bounded, showing = await disclose_sequence_field(
                ctx.deps,
                value,
                field="entries",
                content_complete=not bool(value.get("has_more")),
                noun="directory page",
                continuation_hint="Call ls again with next_offset as offset to continue listing this directory.",
            )
            bounded["showing"] = showing
            entries = value.get("entries")
            _restart_unspilled_page(
                bounded,
                shown=showing,
                total=len(entries) if isinstance(entries, list) else showing,
                cursor_field="next_offset",
                restart_cursor=offset,
                hint="Call ls again with this next_offset as offset and a smaller max_results value.",
            )
            return bounded

        def project(result) -> Mapping[str, JsonValue]:
            entries = [
                self._project_metadata(entry)
                for entry in result.entries
                if not ignore or not any(fnmatch.fnmatch(posixpath.basename(entry.path), pattern) for pattern in ignore)
            ]
            next_offset = result.offset + len(result.entries) if result.has_more else None
            return {
                "path": path,
                "entries": cast(JsonValue, entries),
                "count": len(entries),
                "showing": len(entries),
                "has_more": result.has_more,
                "next_offset": next_offset,
            }

        return await self._execute(
            path,
            lambda files: files.list(
                path,
                offset=offset,
                max_results=limit,
                include_hidden=False,
            ),
            project,
            disclose=disclose,
        )

    async def glob(
        self,
        ctx: RunContext[AgentContext],
        pattern: Annotated[str, Field(description="Glob pattern; bare patterns match recursively")],
        root: Annotated[str, Field(default=".", description="Logical root to search from")] = ".",
        include_ignored: Annotated[
            bool,
            Field(default=False, description="If true, do not interpret repository ignore files"),
        ] = False,
        include_hidden: Annotated[bool, Field(default=False)] = False,
        offset: Annotated[int, Field(default=0, ge=0)] = 0,
        max_results: _UnlimitedOrPositiveResults = 500,
    ) -> FileGlobResult:
        """Find matching paths through one provider-neutral Environment query operation."""
        limit = _MAX_MODEL_RESULTS if max_results == -1 else max_results
        request = FileQueryRequest(
            root=root,
            pattern=pattern,
            recursive=True,
            include_hidden=include_hidden,
            ignore_mode="none" if include_ignored else "git",
            offset=offset,
            max_results=limit,
        )

        async def disclose(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
            bounded, showing = await disclose_sequence_field(
                ctx.deps,
                value,
                field="files",
                content_complete=not bool(value.get("has_more")),
                noun="glob page",
                continuation_hint="Call glob again with next_offset as offset to continue this query.",
            )
            bounded["showing"] = showing
            files = value.get("files")
            _restart_unspilled_page(
                bounded,
                shown=showing,
                total=len(files) if isinstance(files, list) else showing,
                cursor_field="next_offset",
                restart_cursor=offset,
                hint="Call glob again with this next_offset as offset and a smaller max_results value.",
            )
            return bounded

        return await self._execute(
            root,
            lambda files: files.query(request),
            lambda result: {
                "files": [entry.path for entry in result.entries],
                "count": len(result.entries),
                "showing": len(result.entries),
                "has_more": result.has_more,
                "next_offset": result.offset + len(result.entries) if result.has_more else None,
            },
            disclose=disclose,
        )

    async def grep(
        self,
        ctx: RunContext[AgentContext],
        pattern: Annotated[str, Field(description="Text to search for; regex by default, literal when regex=false")],
        root: Annotated[
            str,
            Field(default=".", description="Logical file or directory to search; directories are searched recursively"),
        ] = ".",
        include: Annotated[
            str, Field(default="**/*", description="Glob selecting relative paths, or the basename of an explicit file")
        ] = "**/*",
        include_ignored: Annotated[
            bool,
            Field(default=False, description="If true, do not interpret repository ignore files"),
        ] = False,
        include_hidden: Annotated[bool, Field(default=False)] = False,
        regex: Annotated[
            bool, Field(default=True, description="Use regular expressions; false searches literal text")
        ] = True,
        case_sensitive: Annotated[
            bool, Field(default=True, description="Match case exactly; false ignores case")
        ] = True,
        context_lines: Annotated[int, Field(default=2, ge=0, le=20)] = 2,
        offset: Annotated[int, Field(default=0, ge=0)] = 0,
        max_results: _UnlimitedOrPositiveResults = 100,
        max_matches_per_file: _UnlimitedOrPositiveResults = 20,
        max_files: _UnlimitedOrPositiveResults = -1,
    ) -> FileGrepResult:
        """Search bounded UTF-8 text through one provider-neutral Environment operation."""
        result_limit = _MAX_MODEL_RESULTS if max_results == -1 else max_results
        request = FileTextSearchRequest(
            root=root,
            pattern=pattern,
            regex=regex,
            case_sensitive=case_sensitive,
            include=include,
            include_hidden=include_hidden,
            ignore_mode="none" if include_ignored else "git",
            context_lines=context_lines,
            offset=offset,
            max_matches=result_limit,
            max_matches_per_file=None if max_matches_per_file == -1 else max_matches_per_file,
            max_files=None if max_files == -1 else max_files,
            max_line_length=2_000,
        )

        def project(result) -> Mapping[str, JsonValue]:
            matches: dict[str, JsonValue] = {}
            for match in result.matches:
                key = f"{match.path}:{match.line}"
                matches[key] = cast(
                    JsonValue,
                    {
                        "file_path": match.path,
                        "line_number": match.line,
                        "matching_line": match.text,
                        "text_truncated": match.text_truncated,
                        "context": match.context,
                        "context_start_line": match.context_start_line,
                    },
                )
            return {
                "matches": cast(JsonValue, matches),
                "count": len(matches),
                "showing": len(matches),
                "has_more": result.has_more,
                "next_offset": result.offset + len(result.matches) if result.has_more else None,
            }

        async def disclose(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
            bounded, showing = await disclose_mapping_field(
                ctx.deps,
                value,
                field="matches",
                content_complete=not bool(value.get("has_more")),
                noun="grep page",
                continuation_hint="Call grep again with next_offset as offset to continue this search.",
            )
            bounded["showing"] = showing
            matches = value.get("matches")
            _restart_unspilled_page(
                bounded,
                shown=showing,
                total=len(matches) if isinstance(matches, dict) else showing,
                cursor_field="next_offset",
                restart_cursor=offset,
                hint="Call grep again with this next_offset as offset and smaller result limits.",
            )
            return bounded

        return await self._execute(
            root,
            lambda files: files.search_text(request),
            project,
            disclose=disclose,
        )

    async def _apply_edits(
        self,
        ctx: RunContext[AgentContext],
        file_path: str,
        edits: tuple[FileTextEdit, ...],
        *,
        tool_id: Literal["filesystem.edit", "filesystem.multi_edit"],
    ) -> FileEditResult:
        async def operation(files: FileOperator) -> _FileEditOutcome:
            async with self._mutation_lock:
                self._guard_execution()
                create = edits[0].old_string == ""
                original_content: str | None = None
                if create:
                    content = edits[0].new_string
                    pending = edits[1:]
                    write_mode = "create"
                else:
                    data = await files.read_bytes(
                        file_path,
                        length=_MAX_MODEL_EDIT_BYTES + 1,
                    )
                    if len(data) > _MAX_MODEL_EDIT_BYTES:
                        raise EnvironmentError(
                            "Edit target exceeds the model edit limit.",
                            code="environment_too_large",
                        )
                    try:
                        content = data.decode("utf-8", errors="strict")
                    except UnicodeDecodeError as exc:
                        raise EnvironmentError(
                            "Edit target is not valid UTF-8 text.",
                            code="environment_unsupported",
                        ) from exc
                    original_content = content
                    pending = edits
                    write_mode = "replace"

                content = await asyncio.to_thread(
                    _apply_text_edits,
                    content,
                    pending,
                    2 if create else 1,
                )
                if original_content is not None and content == original_content:
                    return _FileEditOutcome(path=file_path, bytes_written=0, before=content, after=content)
                if create:
                    await self._ensure_parent(files, file_path)
                self._guard_unscoped_step()
                result = await files.write_text(file_path, content, mode=write_mode)
                return _FileEditOutcome(
                    path=result.path,
                    bytes_written=result.bytes_written,
                    before=original_content,
                    after=content,
                )

        async def observe(outcome: _FileEditOutcome) -> None:
            if not outcome.changed:
                return
            await _emit_filesystem_changed(
                ctx,
                tool_id=tool_id,
                changes=(
                    FileChangeProjection(
                        path=outcome.path,
                        action="created" if outcome.before is None else "modified",
                    ),
                ),
            )
            if isinstance(ctx, RunContext) and isinstance(ctx.deps, AgentContext):
                await ctx.emit(
                    FileEditAppliedEvent(
                        tool_call_id=ctx.tool_call_id,
                        file_path=outcome.path,
                        before=outcome.before or "",
                        after=outcome.after,
                    )
                )

        return await self._execute(
            file_path,
            operation,
            lambda outcome: {
                "file_path": outcome.path,
                "edits_applied": len(edits),
                "bytes_written": outcome.bytes_written,
                "created": outcome.before is None,
            },
            observe=observe,
        )

    async def _ensure_parent(self, files: FileOperator, file_path: str) -> None:
        # Derive the parent from dispatch spelling, but keep the original input
        # for exact scope matching so root-level writes need no mkdir permission.
        normalized_path = normalize_operation_path(file_path) if self._resources is not None else file_path
        parent = posixpath.dirname(normalized_path)
        parts = tuple(part for part in parent.split("/") if part)
        is_binding_root = (
            self._file_access.has_mount_root_parent(file_path)
            if self._has_file_scopes
            else parent == "/workspace" or (len(parts) == 2 and parts[0] == "environment")
        )
        if parent and parent != "." and not is_binding_root:
            await files.mkdir(parent, parents=True, exist_ok=True)

    @staticmethod
    def _project_metadata(metadata: FileMetadata) -> FileMetadataProjection:
        return {
            "path": metadata.path,
            "kind": metadata.kind,
            "size": metadata.size,
            "writable": metadata.writable,
        }

    async def _execute[T](
        self,
        path: str,
        operation: Callable[[FileOperator], Awaitable[T]],
        project: Callable[[T], Mapping[str, object]],
        *,
        disclose: Callable[[Mapping[str, JsonValue]], Awaitable[Mapping[str, JsonValue]]] | None = None,
        observe: Callable[[T], Awaitable[None]] | None = None,
    ) -> Any:
        try:
            async with self._file_access.scope(path) as files:
                self._guard_execution()
                result = await operation(files)
        except EnvironmentError as exc:
            return _environment_error_result(exc)
        # Observation cannot turn an already completed write into an execution failure.
        if observe is not None:
            await observe(result)
        projected = cast(dict[str, JsonValue], {"ok": True, **dict(project(result))})
        if disclose is not None:
            return await disclose(projected)
        return projected

    def _resolve_media_understanding(
        self,
        ctx: RunContext[AgentContext],
        kind: NativeInputMediaKind,
    ) -> MediaUnderstandingProvider | None:
        value = self._media_understanding
        if value is None:
            return None
        if isinstance(value, MediaUnderstandingProvider):
            return value
        provider = value(ctx, kind)
        if provider is not None and not isinstance(provider, MediaUnderstandingProvider):
            raise TypeError("media_understanding resolver returned an invalid provider")
        return provider

    def _guard_execution(self) -> None:
        if self._execution_guard is not None:
            self._execution_guard()

    def _guard_unscoped_step(self) -> None:
        if not self._has_file_scopes:
            self._guard_execution()


def _model_supports_native_media(
    ctx: RunContext[AgentContext],
    kind: NativeInputMediaKind,
) -> bool:
    configuration = ctx.deps.model_characteristics
    if configuration is None:
        return False
    capability = ModelCapability(f"{kind}_understanding")
    return capability in configuration.capabilities


async def _record_media_understanding_usage(
    ctx: RunContext[AgentContext],
    usage_receipts: Sequence[ProviderUsage],
) -> None:
    for usage in usage_receipts:
        await ctx.deps.record_provider_usage(
            usage,
            source="files.media_understanding",
            tool_id="filesystem.view",
            tool_call_id=ctx.tool_call_id,
        )


async def _emit_filesystem_changed(
    ctx: RunContext[AgentContext],
    *,
    tool_id: str,
    changes: tuple[FileChangeProjection, ...],
) -> None:
    if not changes or not isinstance(ctx, RunContext) or not isinstance(ctx.deps, AgentContext):
        return
    await emit_tool_event(
        ctx,
        tool_id=tool_id,
        name="filesystem.changed",
        value=FilesystemChangedValue(changes=changes),
    )


def _media_understanding_error(code: str) -> ToolFailure:
    return tool_failure(
        code,
        "Media understanding could not complete. Check the configured media model and provider.",
        retry_hint="dependency_change",
    )


def _restart_unspilled_page(
    result: dict[str, JsonValue],
    *,
    shown: int,
    total: int,
    cursor_field: str,
    restart_cursor: int,
    hint: str,
) -> None:
    if shown >= total:
        return
    disclosure = result.get("disclosure")
    if not isinstance(disclosure, dict) or disclosure.get("output_file_path") is not None:
        return
    result[cursor_field] = restart_cursor
    if "has_more" in result:
        result["has_more"] = True
    disclosure["hint"] = hint


def _file_view_profile(context: AgentContext, file_path: str) -> _FileViewProfile:
    initial_line_limit = 0
    max_line_length = 0
    page_bytes = _MAX_MODEL_TEXT_PAGE_BYTES
    semantic_output_chars = DEFAULT_TOOL_OUTPUT_CHARS
    preserve_complete_lines = False
    if not isinstance(context, AgentContext):
        return _FileViewProfile(
            preserve_on_cold_start=False,
            initial_line_limit=initial_line_limit,
            max_line_length=max_line_length,
            page_bytes=page_bytes,
            semantic_output_chars=semantic_output_chars,
            preserve_complete_lines=preserve_complete_lines,
        )
    try:
        candidate = context.environment.resolve_path(file_path)
    except EnvironmentError:
        return _FileViewProfile(
            preserve_on_cold_start=False,
            initial_line_limit=initial_line_limit,
            max_line_length=max_line_length,
            page_bytes=page_bytes,
            semantic_output_chars=semantic_output_chars,
            preserve_complete_lines=preserve_complete_lines,
        )

    is_skill_file = any(_is_within_environment_root(candidate, skill.directory) for skill in context.skill_paths.values)
    suffix = PurePosixPath(candidate.path).suffix.casefold()
    if suffix == ".md" and is_skill_file:
        initial_line_limit = _SKILL_MARKDOWN_LINE_LIMIT
        max_line_length = _SKILL_MARKDOWN_MAX_LINE_LENGTH
        page_bytes = _MAX_MODEL_TEXT_RULE_PAGE_BYTES
        semantic_output_chars = FINAL_TOOL_OUTPUT_HARD_CHARS
        preserve_complete_lines = True

    for rule in context.tool_metadata.values(FILE_VIEW_RULES):
        if suffix not in rule.suffixes or not any(_is_within_environment_root(candidate, root) for root in rule.roots):
            continue
        if rule.initial_line_limit is not None:
            initial_line_limit = max(initial_line_limit, min(rule.initial_line_limit, _MAX_MODEL_RESULTS))
        if rule.max_line_length is not None:
            max_line_length = max(max_line_length, min(rule.max_line_length, _MAX_MODEL_TEXT_BYTES))
        if rule.page_bytes is not None:
            page_bytes = max(page_bytes, min(rule.page_bytes, _MAX_MODEL_TEXT_RULE_PAGE_BYTES))
        if rule.semantic_output_chars is not None:
            semantic_output_chars = max(
                semantic_output_chars,
                min(rule.semantic_output_chars, FINAL_TOOL_OUTPUT_HARD_CHARS),
            )
        preserve_complete_lines = preserve_complete_lines or rule.preserve_complete_lines

    return _FileViewProfile(
        preserve_on_cold_start=is_skill_file,
        initial_line_limit=initial_line_limit,
        max_line_length=max_line_length,
        page_bytes=page_bytes,
        semantic_output_chars=semantic_output_chars,
        preserve_complete_lines=preserve_complete_lines,
    )


def _is_within_environment_root(candidate: EnvironmentPath, root: EnvironmentPath) -> bool:
    if candidate.mount_id != root.mount_id:
        return False
    normalized_root = root.path.rstrip("/")
    prefix = f"{normalized_root}/" if normalized_root else "/"
    return candidate.path == root.path or candidate.path.startswith(prefix)


async def _disclose_line_preserving_file_page(
    context: AgentContext,
    value: Mapping[str, JsonValue],
    *,
    limit: int,
) -> Mapping[str, JsonValue]:
    safe_value = redact_json(cast(JsonValue, dict(value)))
    assert isinstance(safe_value, dict)
    result = safe_value
    continuation_hint = "Call view again with next_line_offset as line_offset to continue reading this file."
    shortened = bool(result.get("truncated_lines"))
    if shortened:
        hint = (
            "Source lines listed in truncated_lines (one-based) were shortened; has_more only describes later lines. "
            "To inspect an omitted suffix, reread that line with line_offset set to its number minus one, "
            "line_limit=1, and a larger max_line_length within the tool limits. Provider byte limits still apply."
        )
        result["disclosure"] = cast(JsonValue, continuation_disclosure(result, hint=hint))
        continuation_hint += " " + hint
    result_fits = tool_output_size(result) <= limit
    if result_fits and not bool(result.get("has_more")):
        return acknowledge_tool_output(result)
    content = result.get("content")
    line_offset = result.get("line_offset")
    lines_read = result.get("lines_read")
    if not isinstance(content, str) or not isinstance(line_offset, int) or not isinstance(lines_read, int):
        return await disclose_text_fields(
            context,
            result,
            text_fields=("content",),
            content_complete=not bool(result.get("has_more")) and not shortened,
            noun="file page",
            limit=limit,
        )

    if result_fits:
        result["next_line_offset"] = line_offset + lines_read
        result["disclosure"] = cast(
            JsonValue,
            continuation_disclosure(
                result,
                hint=continuation_hint,
            ),
        )
        if tool_output_size(result) <= limit:
            return acknowledge_tool_output(result)

    lines = _lf_lines(content)
    if len(lines) != lines_read:
        return await disclose_text_fields(
            context,
            result,
            text_fields=("content",),
            content_complete=not bool(result.get("has_more")) and not shortened,
            noun="file page",
            limit=limit,
        )

    disclosure = continuation_disclosure(
        result,
        hint=continuation_hint,
    )
    preview: dict[str, JsonValue] = {
        **result,
        "content": "",
        "lines_read": 0,
        "has_more": True,
        "next_line_offset": line_offset,
        "truncated_lines": [],
        "disclosure": cast(JsonValue, disclosure),
    }
    shown = 0
    selected_content = ""
    for line in lines:
        candidate_content = f"{selected_content}{line}"
        preview["content"] = candidate_content
        preview["lines_read"] = shown + 1
        preview["next_line_offset"] = line_offset + shown + 1
        preview["truncated_lines"] = cast(
            JsonValue,
            [
                item
                for item in cast(list[JsonValue], result.get("truncated_lines", []))
                if isinstance(item, int) and line_offset < item <= line_offset + shown + 1
            ],
        )
        if tool_output_size(preview) > limit:
            preview["content"] = selected_content
            preview["lines_read"] = shown
            preview["next_line_offset"] = line_offset + shown
            break
        selected_content = candidate_content
        shown += 1

    if shown == 0:
        # A single source line exceeds the semantic budget. Spill the fuller
        # page, but advance only past the one line actually shown, not all
        # provider rows. Do not insert synthetic lines into source content.
        disclosure = await create_tool_output_disclosure(
            context,
            result,
            content_complete=not bool(result.get("has_more")) and not shortened,
            noun="file page",
        )
        disclosure["hint"] += (
            " Only a prefix of the source line in truncated_lines is shown because of the model output limit."
            " has_more describes later lines; use next_line_offset to continue those lines."
        )
        preview.update(
            content=lines[0],
            lines_read=1,
            has_more=bool(result.get("has_more")) or lines_read > 1,
            truncated_lines=[line_offset + 1],
            disclosure=cast(JsonValue, disclosure),
        )
        if preview["has_more"]:
            preview["next_line_offset"] = line_offset + 1
        else:
            preview.pop("next_line_offset", None)
        return acknowledge_tool_output(
            fit_text_fields_to_limit(preview, text_fields=("content",), limit=limit, suffix="")
        )
    preview["truncated_lines"] = cast(
        JsonValue,
        [
            item
            for item in cast(list[JsonValue], result.get("truncated_lines", []))
            if isinstance(item, int) and line_offset < item <= line_offset + shown
        ],
    )
    return acknowledge_tool_output(preview)


def _lf_lines(content: str) -> list[str]:
    if not content:
        return []
    parts = content.split("\n")
    lines = [f"{part}\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _environment_tool_error(exc: EnvironmentError) -> ToolError:
    return _environment_error_result(exc)["error"]


def _environment_error_result(exc: EnvironmentError) -> ToolFailure:
    if exc.code == "environment_not_found":
        exc = EnvironmentError(
            str(exc),
            code=exc.code,
            retry_hint=exc.retry_hint,
            details={"reason": "path_not_found", **exc.details},
        )
    return environment_failure(exc)


def _apply_text_edits(
    content: str,
    edits: tuple[FileTextEdit, ...],
    start_index: int = 1,
) -> str:
    if len(content.encode("utf-8")) > _MAX_MODEL_EDIT_BYTES:
        raise EnvironmentError("Edit target exceeds the model edit limit.", code="environment_too_large")
    for index, item in enumerate(edits, start=start_index):
        if item.old_string == "":
            raise EnvironmentError(
                "Only the first edit may use an empty old_string.",
                code="environment_request_invalid",
                details={"edit_index": index},
            )
        occurrences = content.count(item.old_string)
        if occurrences == 0:
            raise EnvironmentError(
                "Exact edit text was not found.",
                code="environment_edit_not_found",
                details={"edit_index": index},
            )
        if occurrences > 1 and not item.replace_all:
            raise EnvironmentError(
                "Exact edit text is not unique; add context or set replace_all.",
                code="environment_edit_ambiguous",
                details={"edit_index": index, "occurrences": occurrences},
            )
        content = content.replace(
            item.old_string,
            item.new_string,
            -1 if item.replace_all else 1,
        )
        if len(content.encode("utf-8")) > _MAX_MODEL_EDIT_BYTES:
            raise EnvironmentError("Edited file exceeds the model edit limit.", code="environment_too_large")
    return content


def _matches_file_glob(path: str, *, root: str, pattern: str) -> bool:
    normalized_path = path.strip("/")
    normalized_root = root.strip("/")
    if normalized_root not in {"", "."}:
        if normalized_path == normalized_root:
            normalized_path = ""
        elif normalized_path.startswith(f"{normalized_root}/"):
            normalized_path = normalized_path[len(normalized_root) + 1 :]
    if pattern in {"*", "**", "**/*"}:
        return True
    if "/" not in pattern:
        return fnmatch.fnmatchcase(posixpath.basename(normalized_path), pattern)
    return PurePosixPath(normalized_path).full_match(pattern)


def _string_argument(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    return value


def _optional_string_argument(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    return value


def _string_sequence_argument(arguments: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = arguments.get(name)
    if not isinstance(value, list | tuple) or not value or not all(isinstance(item, str) and item for item in value):
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    return tuple(value)


def _path_pair_sequence_argument(arguments: Mapping[str, object], name: str) -> tuple[FilePathPair, ...]:
    value = arguments.get(name)
    if not isinstance(value, list | tuple) or not value:
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    pairs: list[FilePathPair] = []
    for item in value:
        if isinstance(item, FilePathPair):
            pairs.append(item)
            continue
        if isinstance(item, Mapping):
            try:
                pairs.append(FilePathPair.model_validate(item, strict=True))
            except ValueError as exc:
                raise EnvironmentError(
                    f"Environment tool argument {name!r} is invalid.",
                    code="environment_request_invalid",
                ) from exc
            continue
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    return tuple(pairs)


def _pair_paths(arguments: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(path for pair in _path_pair_sequence_argument(arguments, "pairs") for path in (pair.src, pair.dst))


__all__ = ["FILE_VIEW_RULES", "FilePathPair", "FileTextEdit", "FileToolset", "FileViewRule"]
