"""Read-only native Git observations and reviewed diff capture for human clients."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from anyio import CancelScope, create_task_group, fail_after, open_process, to_thread
from anyio.abc import ByteReceiveStream
from pydantic import Field, model_validator

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.file_context import GitContextSource
from a13n_harness_ui.host_files import NativePath, Revision
from a13n_harness_ui.surfaces import SurfaceModel

MAX_GIT_BYTES = 2 * 1024 * 1024
MAX_GIT_ENTRIES = 10_000
GIT_TIMEOUT_SECONDS = 15
GitPath = Annotated[str, Field(min_length=1, max_length=4096)]
Comparison = Literal["staged", "unstaged", "untracked"]


def _error(code: str, message: str) -> HarnessUiError:
    return HarnessUiError(message, code=f"host_git_{code}")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise _error("encoding_invalid", "Git metadata contains a path that cannot be represented as UTF-8.") from None


def _relative(value: str) -> str:
    # Git paths use slash separators; selections name one entry, not a pattern.
    if (
        "\x00" in value
        or PurePosixPath(value).is_absolute()
        or Path(value).is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or (os.name == "nt" and ("\\" in value or ":" in value))
    ):
        raise _error("path_invalid", "Select a repository-relative Git path without traversal.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _error("path_invalid", "Git paths must be representable as UTF-8.") from None
    return value


def _pathspec(path: str) -> str:
    # Git's literal pathspecs also match directory descendants. Escaped glob
    # matching selects exactly this path on both sides, including file/directory
    # replacements and renames into a directory at the original path.
    return ":(top,glob)" + "".join("/" if char == "/" else "\\" + char for char in path)


class GitRepository(SurfaceModel):
    root: NativePath
    git_dir: NativePath
    common_dir: NativePath
    head_oid: str | None
    branch: str | None


class GitDiscovery(SurfaceModel):
    path: NativePath
    state: Literal["repository", "not_repository", "bare"]
    repository: GitRepository | None = None


class GitChange(SurfaceModel):
    path: GitPath
    original_path: GitPath | None = None
    kind: Literal["tracked", "untracked", "ignored", "conflicted"]
    index_status: str
    worktree_status: str
    submodule: str | None = None


class GitStatus(SurfaceModel):
    repository: GitRepository
    revision: Revision
    entries: tuple[GitChange, ...]
    next_offset: int | None


class GitDiffRequest(SurfaceModel):
    repository_path: NativePath
    path: GitPath
    comparison: Comparison = "unstaged"
    expected_revision: Revision | None = None


class GitDiff(SurfaceModel):
    repository: GitRepository
    path: GitPath
    original_path: GitPath | None = None
    comparison: Comparison
    index_revision: Revision
    revision: Revision
    presentation: Literal["text", "binary", "unchanged"]
    text: str | None = None


class GitCaptureRequest(SurfaceModel):
    repository_path: NativePath
    path: GitPath
    comparison: Comparison = "unstaged"
    expected_revision: Revision
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_range(self) -> GitCaptureRequest:
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("Select both start_line and end_line, or neither.")
        if self.start_line is not None and self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line precedes start_line.")
        return self


@dataclass(frozen=True, slots=True)
class SelectedDiff:
    source: GitContextSource
    data: bytes


async def _git(directory: Path, *arguments: str, accepted: tuple[int, ...] = (0,)) -> tuple[int, bytes, bytes]:
    """Bound native Git output/time and reap it on completion or cancellation."""
    # Ambient Git targeting variables must not retarget the selected native path.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", LC_ALL="C")
    command = [
        "git",
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "color.ui=false",
        *arguments,
    ]
    output, errors = bytearray(), bytearray()
    overflow = False

    async def read(stream: ByteReceiveStream, target: bytearray, limit: int, scope: CancelScope) -> None:
        nonlocal overflow
        async for chunk in stream:
            if len(target) + len(chunk) > limit:
                overflow = True
                scope.cancel()
                return
            target.extend(chunk)

    try:
        with fail_after(GIT_TIMEOUT_SECONDS):
            async with await open_process(command, cwd=directory, env=env, stdin=subprocess.DEVNULL) as process:
                assert process.stdout is not None and process.stderr is not None
                async with create_task_group() as tasks:
                    tasks.start_soon(read, process.stdout, output, MAX_GIT_BYTES, tasks.cancel_scope)
                    tasks.start_soon(read, process.stderr, errors, 64 * 1024, tasks.cancel_scope)
                    await process.wait()
                if overflow:
                    if process.returncode is None:
                        process.kill()
                    raise _error("too_large", "Git output exceeds its bound; select a smaller repository or file.")
                code = process.returncode
    except FileNotFoundError:
        raise _error("unavailable", "Git is not installed or the selected directory disappeared.") from None
    except PermissionError:
        raise _error("permission_denied", "The server OS account cannot run Git at this location.") from None
    except TimeoutError:
        raise _error("timeout", "Git did not complete within 15 seconds; refresh or narrow the query.") from None
    except OSError as exc:
        raise _error("failed", f"Native Git failed (errno {exc.errno}).") from None
    assert code is not None
    if code not in accepted:
        # Git diagnostics explain missing objects, ownership, permissions and
        # repository corruption; they are bounded data, never rendered as HTML.
        detail = bytes(errors).decode("utf-8", errors="replace")[:4096].strip()
        raise _error("failed", detail or f"Git exited with status {code}.")
    return code, bytes(output), bytes(errors)


def _changes(raw: bytes) -> tuple[GitChange, ...]:
    records = iter(raw.split(b"\x00"))
    result: list[GitChange] = []
    for record in records:
        if not record:
            continue
        tag = record[:1]
        if tag in {b"?", b"!"}:
            entry = GitChange(
                path=_text(record[2:]),
                kind="untracked" if tag == b"?" else "ignored",
                index_status="?" if tag == b"?" else "!",
                worktree_status="?" if tag == b"?" else "!",
            )
        elif tag in {b"1", b"2", b"u"}:
            count = {b"1": 8, b"2": 9, b"u": 10}[tag]
            fields = record.split(b" ", count)
            original = next(records, b"") if tag == b"2" else None
            if len(fields) != count + 1 or len(fields[1]) != 2 or original == b"":
                raise _error("failed", "Git returned an incomplete status record.")
            xy = _text(fields[1])
            entry = GitChange(
                path=_text(fields[-1]),
                original_path=_text(original) if original is not None else None,
                kind="conflicted" if tag == b"u" else "tracked",
                index_status=xy[0],
                worktree_status=xy[1],
                submodule=None if fields[2] == b"N..." else _text(fields[2]),
            )
        else:
            raise _error("failed", "Git returned an unsupported status record.")
        result.append(entry)
        if len(result) > MAX_GIT_ENTRIES:
            raise _error("too_large", "Git status exceeds 10000 entries; use a smaller repository.")
    return tuple(sorted(result, key=lambda entry: entry.path))


class HostGit:
    """App-owned read-only queries; the checkout/index are the only Git state."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    @property
    def available(self) -> bool:
        return self.enabled and shutil.which("git") is not None

    def require_enabled(self) -> None:
        if not self.enabled:
            raise _error("disabled", "Native Git requires computer sharing on this instance.")

    async def discover(self, path: str) -> GitDiscovery:
        self.require_enabled()
        if not path or len(path) > 4096 or "\x00" in path or not Path(path).is_absolute():
            raise _error("path_invalid", "Use an absolute native path of at most 4096 characters.")

        try:
            path.encode("utf-8")
        except UnicodeEncodeError:
            raise _error("path_invalid", "Git paths must be representable as UTF-8.") from None

        def location() -> Path:
            target = Path(path).resolve(strict=True)
            return target if target.is_dir() else target.parent

        try:
            directory = await to_thread.run_sync(location)
        except FileNotFoundError:
            raise _error("not_found", "The selected native path does not exist.") from None
        except OSError:
            raise _error("permission_denied", "The selected native location cannot be inspected.") from None
        code, inside, errors = await _git(directory, "rev-parse", "--is-inside-work-tree", accepted=(0, 128))
        if code:
            if b"not a git repository" in errors:
                return GitDiscovery(path=str(directory), state="not_repository")
            raise _error("failed", errors.decode("utf-8", errors="replace")[:4096].strip())
        if inside.strip() != b"true":
            _, bare, _ = await _git(directory, "rev-parse", "--is-bare-repository")
            return GitDiscovery(path=str(directory), state="bare" if bare.strip() == b"true" else "not_repository")

        async def value(*args: str) -> str:
            _, data, _ = await _git(directory, *args)
            return _text(data).removesuffix("\n")

        root = await value("rev-parse", "--show-toplevel")
        git_dir = await value("rev-parse", "--absolute-git-dir")
        common_dir = await value("rev-parse", "--path-format=absolute", "--git-common-dir")
        _, head, _ = await _git(directory, "rev-parse", "--verify", "--quiet", "HEAD", accepted=(0, 1))
        _, branch, _ = await _git(directory, "symbolic-ref", "--quiet", "--short", "HEAD", accepted=(0, 1))
        return GitDiscovery(
            path=str(directory),
            state="repository",
            repository=GitRepository(
                root=root,
                git_dir=git_dir,
                common_dir=common_dir,
                head_oid=_text(head).strip() or None,
                branch=_text(branch).removesuffix("\n") or None,
            ),
        )

    async def _repository(self, path: str) -> GitRepository:
        found = await self.discover(path)
        if found.repository is None:
            raise _error("not_repository", "Select a non-bare Git worktree; ordinary Files remains available.")
        return found.repository

    async def _status(self, repository: GitRepository, include_ignored: bool) -> tuple[bytes, tuple[GitChange, ...]]:
        _, raw, _ = await _git(
            Path(repository.root),
            "status",
            "--porcelain=v2",
            "--renames",
            "-z",
            "--untracked-files=all",
            "--ignored=matching" if include_ignored else "--ignored=no",
            "--ignore-submodules=none",
        )
        return raw, _changes(raw)

    async def status(
        self,
        path: str,
        *,
        include_ignored: bool = False,
        offset: int = 0,
        limit: int = 200,
        expected_revision: str | None = None,
    ) -> GitStatus:
        self.require_enabled()
        if not 0 <= offset <= MAX_GIT_ENTRIES or not 1 <= limit <= 500:
            raise _error("range_invalid", "Git status offset or page size is outside its limit.")
        repository = await self._repository(path)
        raw, entries = await self._status(repository, include_ignored)
        revision = _digest(repository.model_dump_json().encode() + raw)
        if (offset and expected_revision is None) or (expected_revision is not None and expected_revision != revision):
            raise _error("conflict", "Git status changed; refresh from the first page.")
        end = offset + limit
        return GitStatus(
            repository=repository,
            revision=revision,
            entries=entries[offset:end],
            next_offset=end if end < len(entries) else None,
        )

    async def diff(self, request: GitDiffRequest) -> GitDiff:
        self.require_enabled()
        selected = _relative(request.path)
        repository = await self._repository(request.repository_path)
        directory = Path(repository.root)
        _, entries = await self._status(repository, False)
        change = next((entry for entry in entries if entry.path == selected), None)
        original = None
        if change is not None:
            axis = change.index_status if request.comparison == "staged" else change.worktree_status
            if axis in {"R", "C"}:
                original = change.original_path
        paths = [selected] if original is None else [selected, _relative(original)]
        pathspecs = [_pathspec(path) for path in paths]
        _, index, _ = await _git(directory, "ls-files", "--stage", "-z", "--", *pathspecs)
        if change is None and not index:
            raise _error("selection_invalid", "Select one changed or tracked file, not a directory or absent path.")
        options = [
            "diff",
            "--patch",
            "--full-index",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--no-relative",
            "--find-renames",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "--unified=3",
            "--inter-hunk-context=0",
            "--submodule=short",
            "--ignore-submodules=none",
        ]
        if request.comparison == "untracked":
            if change is None or change.kind != "untracked":
                raise _error("conflict", "The selected path is no longer an untracked file; refresh status.")
            # Status can become stale before diff; never deliberately recurse a
            # replaced directory or open a special object as an untracked file.
            try:
                mode = (await to_thread.run_sync((directory / selected).lstat)).st_mode
            except FileNotFoundError:
                raise _error("conflict", "The untracked file disappeared; refresh status.") from None
            if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                raise _error("selection_invalid", "Select a regular untracked file or symlink.")
            _, patch, _ = await _git(directory, *options, "--no-index", "--", os.devnull, selected, accepted=(0, 1))
        else:
            baseline: list[str] = []
            if request.comparison == "staged":
                head = repository.head_oid
                if head is None:
                    # Compute the repository's empty tree ID without writing an object.
                    _, empty, _ = await _git(directory, "hash-object", "-t", "tree", "--stdin")
                    head = _text(empty).strip()
                baseline = ["--cached", head]
            _, patch, _ = await _git(directory, *options, *baseline, "--", *pathspecs)
        _, after_index, _ = await _git(directory, "ls-files", "--stage", "-z", "--", *pathspecs)
        if after_index != index:
            raise _error("conflict", "The selected index entries changed while reading the diff; refresh.")
        index_revision = _digest(index)
        identity = [repository.model_dump_json(), selected, original, request.comparison, index_revision]
        revision = _digest(repr(identity).encode() + patch)
        if request.expected_revision is not None and request.expected_revision != revision:
            raise _error("conflict", "The reviewed comparison changed; refresh before selecting it.")
        presentation: Literal["text", "binary", "unchanged"] = "text" if patch else "unchanged"
        try:
            text = patch.decode("utf-8")
            if b"\x00" in patch or any(line.startswith(b"Binary files ") for line in patch.splitlines()):
                presentation = "binary"
                text = None
        except UnicodeDecodeError:
            presentation, text = "binary", None
        return GitDiff(
            repository=repository,
            path=selected,
            original_path=original,
            comparison=request.comparison,
            index_revision=index_revision,
            revision=revision,
            presentation=presentation,
            text=text if presentation == "text" else None,
        )

    async def capture(self, request: GitCaptureRequest) -> SelectedDiff:
        preview = await self.diff(
            GitDiffRequest(
                repository_path=request.repository_path,
                path=request.path,
                comparison=request.comparison,
                expected_revision=request.expected_revision,
            )
        )
        if preview.presentation != "text" or preview.text is None:
            raise _error("selection_invalid", "Select a text diff; use Files to capture binary file bytes.")
        text = preview.text
        if request.start_line is not None and request.end_line is not None:
            lines = text.splitlines(keepends=True)
            if request.end_line > len(lines):
                raise _error("selection_invalid", "The selected range exceeds the reviewed diff.")
            text = "".join(lines[request.start_line - 1 : request.end_line])
        source = GitContextSource(
            repository_path=preview.repository.root,
            git_dir=preview.repository.git_dir,
            path=preview.path,
            original_path=preview.original_path,
            comparison=preview.comparison,
            head_oid=preview.repository.head_oid,
            index_revision=preview.index_revision,
            revision=preview.revision,
            start_line=request.start_line,
            end_line=request.end_line,
        )
        return SelectedDiff(source, text.encode("utf-8"))
