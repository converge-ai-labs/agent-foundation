"""Bounded, commit-pinned GitHub acquisition for managed Skill packages."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

import anyio
import httpx2
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from .domain import GitHubRevisionSource, GitHubSkillImportProvenance
from .package import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    NormalizedSkillPackage,
    SkillPackageError,
    normalize_skill_files,
    normalize_skill_path,
)

GITHUB_ACQUISITION_DEADLINE_SECONDS = 60.0
MAX_GITHUB_TREE_ENTRIES = 8192
_MAX_COMMIT_RESPONSE_BYTES = 1024 * 1024
_MAX_TREE_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_BLOB_RESPONSE_OVERHEAD_BYTES = 64 * 1024
_GITHUB_API_ROOT = "https://api.github.com"
_GITHUB_API_VERSION = "2026-03-10"
_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_INVALID_REF_CHARACTERS = frozenset(" ~^:?*[\\")

type GitHubAcquisitionErrorCode = Literal[
    "github_source_invalid",
    "github_commit_mismatch",
    "github_auth_failed",
    "github_rate_limited",
    "github_unavailable",
]


class GitHubAcquisitionError(RuntimeError):
    """Safe provider-independent GitHub acquisition failure."""

    def __init__(
        self,
        code: GitHubAcquisitionErrorCode,
        message: str,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class AcquiredGitHubSkill:
    package: NormalizedSkillPackage
    provenance: GitHubSkillImportProvenance


@dataclass(frozen=True, slots=True)
class _Repository:
    owner: str
    name: str

    @property
    def canonical_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"

    @property
    def api_root(self) -> str:
        return f"{_GITHUB_API_ROOT}/repos/{quote(self.owner, safe='')}/{quote(self.name, safe='')}"


@dataclass(frozen=True, slots=True)
class _BlobSelection:
    path: str
    sha: str
    size: int


class _TreeRef(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    sha: Annotated[str, StringConstraints(pattern=_SHA1_PATTERN)]


class _CommitBody(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    tree: _TreeRef


class _Commit(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    sha: Annotated[str, StringConstraints(pattern=_SHA1_PATTERN)]
    commit: _CommitBody


class _TreeEntry(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    mode: str
    entry_type: str = Field(alias="type")
    sha: Annotated[str, StringConstraints(pattern=_SHA1_PATTERN)]
    size: int | None = Field(default=None, ge=0)


class _Tree(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    tree: tuple[_TreeEntry, ...]
    truncated: bool


class _Blob(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    sha: Annotated[str, StringConstraints(pattern=_SHA1_PATTERN)]
    size: int = Field(ge=0)
    encoding: Literal["base64"]
    content: str


_COMMITS = TypeAdapter(tuple[_Commit, ...])


class GitHubSkillAcquirer:
    """Acquire exactly one selected repository tree through GitHub's REST API."""

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        *,
        deadline_seconds: float = GITHUB_ACQUISITION_DEADLINE_SECONDS,
        blob_concurrency: int = 8,
    ) -> None:
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if blob_concurrency <= 0 or blob_concurrency > 64:
            raise ValueError("blob_concurrency must be between 1 and 64")
        self._http_client = http_client
        self._deadline_seconds = deadline_seconds
        self._blob_concurrency = blob_concurrency

    async def acquire(self, source: GitHubRevisionSource, *, credential: str | None = None) -> AcquiredGitHubSkill:
        repository = _parse_repository_url(source.repository_url)
        requested_ref = _validate_ref(source.ref)
        subdirectory = "" if source.subdirectory == "" else normalize_skill_path(source.subdirectory)
        headers = _request_headers(credential)
        try:
            with anyio.fail_after(self._deadline_seconds):
                commit = await self._resolve_commit(repository, requested_ref, headers)
                if source.expected_commit_sha is not None and commit.sha != source.expected_commit_sha:
                    raise GitHubAcquisitionError(
                        "github_commit_mismatch",
                        "The GitHub source resolved to a different commit.",
                    )
                tree_sha, inspected = await self._resolve_subdirectory(
                    repository,
                    commit.commit.tree.sha,
                    subdirectory,
                    headers,
                )
                selections = await self._select_blobs(repository, tree_sha, inspected, headers)
                files = await self._fetch_blobs(repository, selections, headers)
        except (GitHubAcquisitionError, SkillPackageError):
            raise
        except TimeoutError as error:
            raise GitHubAcquisitionError(
                "github_unavailable",
                "GitHub acquisition did not complete before its deadline.",
            ) from error
        package = normalize_skill_files(files)
        return AcquiredGitHubSkill(
            package=package,
            provenance=GitHubSkillImportProvenance(
                repository_url=repository.canonical_url,
                requested_ref=requested_ref,
                resolved_commit_sha=commit.sha,
                subdirectory=subdirectory,
            ),
        )

    async def _resolve_commit(
        self,
        repository: _Repository,
        ref: str | None,
        headers: dict[str, str],
    ) -> _Commit:
        if ref is None:
            payload = await self._request_json(
                f"{repository.api_root}/commits",
                headers=headers,
                params={"per_page": "1"},
                max_bytes=_MAX_COMMIT_RESPONSE_BYTES,
            )
            try:
                commits = _COMMITS.validate_python(payload)
            except ValidationError as error:
                raise _unavailable("GitHub returned an invalid commit response") from error
            if not commits:
                raise _invalid("The GitHub repository has no importable commit")
            return commits[0]

        payload = await self._request_json(
            f"{repository.api_root}/commits/{quote(ref, safe='')}",
            headers=headers,
            max_bytes=_MAX_COMMIT_RESPONSE_BYTES,
        )
        return _validate_response(_Commit, payload, "commit")

    async def _resolve_subdirectory(
        self,
        repository: _Repository,
        root_tree_sha: str,
        subdirectory: str,
        headers: dict[str, str],
    ) -> tuple[str, int]:
        tree_sha = root_tree_sha
        inspected = 0
        for segment in subdirectory.split("/") if subdirectory else ():
            tree = await self._get_tree(repository, tree_sha, headers=headers, recursive=False)
            inspected = _inspect_tree(inspected, tree)
            match = next((entry for entry in tree.tree if entry.path == segment), None)
            if match is None or match.entry_type != "tree" or match.mode != "040000":
                raise _invalid("The selected GitHub subdirectory does not exist")
            tree_sha = match.sha
        return tree_sha, inspected

    async def _select_blobs(
        self,
        repository: _Repository,
        tree_sha: str,
        inspected: int,
        headers: dict[str, str],
    ) -> tuple[_BlobSelection, ...]:
        tree = await self._get_tree(repository, tree_sha, headers=headers, recursive=True)
        _inspect_tree(inspected, tree)

        selections: list[_BlobSelection] = []
        paths: set[str] = set()
        total_size = 0
        for entry in tree.tree:
            if entry.entry_type == "tree" and entry.mode == "040000":
                continue
            if entry.entry_type != "blob" or entry.mode not in {"100644", "100755"} or entry.size is None:
                raise _invalid("The selected GitHub tree contains a non-regular entry")
            path = normalize_skill_path(entry.path)
            folded = path.casefold()
            if folded in paths:
                raise _invalid("The selected GitHub tree contains colliding paths")
            paths.add(folded)
            if entry.size > MAX_FILE_BYTES:
                raise _package_limit("The selected GitHub tree contains an oversized file")
            total_size += entry.size
            if total_size > MAX_TOTAL_BYTES:
                raise _package_limit("The selected GitHub tree exceeds the package size limit")
            selections.append(_BlobSelection(path=path, sha=entry.sha, size=entry.size))
            if len(selections) > MAX_FILES:
                raise _package_limit("The selected GitHub tree contains too many files")
        return tuple(selections)

    async def _get_tree(
        self,
        repository: _Repository,
        tree_sha: str,
        *,
        headers: dict[str, str],
        recursive: bool,
    ) -> _Tree:
        params = {"recursive": "1"} if recursive else None
        payload = await self._request_json(
            f"{repository.api_root}/git/trees/{tree_sha}",
            headers=headers,
            params=params,
            max_bytes=_MAX_TREE_RESPONSE_BYTES,
        )
        return _validate_response(_Tree, payload, "tree")

    async def _fetch_blobs(
        self,
        repository: _Repository,
        selections: tuple[_BlobSelection, ...],
        headers: dict[str, str],
    ) -> tuple[tuple[str, bytes], ...]:
        results: list[tuple[str, bytes] | None] = [None] * len(selections)
        first_error: GitHubAcquisitionError | None = None
        jobs = iter(enumerate(selections))

        async with anyio.create_task_group() as tasks:

            async def worker() -> None:
                nonlocal first_error
                try:
                    for index, selection in jobs:
                        content = await self._fetch_blob(repository, selection, headers)
                        results[index] = (selection.path, content)
                except GitHubAcquisitionError as error:
                    if first_error is None:
                        first_error = error
                    tasks.cancel_scope.cancel()

            for _ in range(min(self._blob_concurrency, len(selections))):
                tasks.start_soon(worker)

        if first_error is not None:
            raise first_error
        if any(item is None for item in results):
            raise _unavailable("GitHub blob acquisition did not produce a complete package")
        return tuple(item for item in results if item is not None)

    async def _fetch_blob(
        self,
        repository: _Repository,
        selection: _BlobSelection,
        headers: dict[str, str],
    ) -> bytes:
        max_bytes = ((selection.size + 2) // 3) * 4 + _MAX_BLOB_RESPONSE_OVERHEAD_BYTES
        payload = await self._request_json(
            f"{repository.api_root}/git/blobs/{selection.sha}",
            headers=headers,
            max_bytes=max_bytes,
        )
        blob = _validate_response(_Blob, payload, "blob")
        if blob.sha != selection.sha or blob.size != selection.size:
            raise _unavailable("GitHub blob metadata did not match the selected tree")
        try:
            encoded = "".join(blob.content.split()).encode("ascii")
            content = base64.b64decode(encoded, validate=True)
        except (UnicodeEncodeError, ValueError, binascii.Error) as error:
            raise _unavailable("GitHub returned invalid blob content") from error
        if len(content) != selection.size or _git_blob_sha(content) != selection.sha:
            raise _unavailable("GitHub blob content did not match the selected tree")
        return content

    async def _request_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        max_bytes: int,
        params: dict[str, str] | None = None,
    ) -> object:
        try:
            async with self._http_client.stream(
                "GET",
                url,
                headers=headers,
                params=params,
                follow_redirects=False,
            ) as response:
                _raise_for_status(response)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise _unavailable("GitHub returned an oversized response")
        except GitHubAcquisitionError:
            raise
        except httpx2.HTTPError as error:
            raise _unavailable("GitHub is temporarily unavailable") from error
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _unavailable("GitHub returned an invalid JSON response") from error


def _parse_repository_url(repository_url: str) -> _Repository:
    if repository_url != repository_url.strip() or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in repository_url
    ):
        raise _invalid("The GitHub repository URL is invalid")
    try:
        parsed = urlsplit(repository_url)
        port = parsed.port
    except ValueError as error:
        raise _invalid("The GitHub repository URL is invalid") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.casefold() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or "%" in parsed.path
    ):
        raise _invalid("The GitHub repository URL is invalid")
    path = parsed.path.rstrip("/")
    parts = path.removeprefix("/").split("/")
    if len(parts) != 2:
        raise _invalid("The GitHub repository URL must identify one repository")
    owner, repository = parts
    repository = repository.removesuffix(".git")
    if (
        repository in {".", ".."}
        or _OWNER_PATTERN.fullmatch(owner) is None
        or _REPOSITORY_PATTERN.fullmatch(repository) is None
    ):
        raise _invalid("The GitHub repository URL is invalid")
    return _Repository(owner=owner, name=repository)


def _validate_ref(ref: str | None) -> str | None:
    if ref is None:
        return None
    if (
        ref == "@"
        or ref.startswith("/")
        or ref.endswith(("/", "."))
        or "//" in ref
        or ".." in ref
        or "@{" in ref
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in ref)
        or any(character in _INVALID_REF_CHARACTERS for character in ref)
    ):
        raise _invalid("The GitHub ref is invalid")
    if any(segment.startswith(".") or segment.endswith(".lock") for segment in ref.split("/")):
        raise _invalid("The GitHub ref is invalid")
    return ref


def _request_headers(credential: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "User-Agent": "agent-foundation-service",
    }
    if credential is None:
        return headers
    if (
        not credential
        or len(credential) > 4096
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in credential)
    ):
        raise GitHubAcquisitionError("github_auth_failed", "The selected GitHub credential is invalid.")
    headers["Authorization"] = f"Bearer {credential}"
    return headers


def _inspect_tree(current: int, tree: _Tree) -> int:
    if tree.truncated:
        raise _package_limit("The selected GitHub tree is incomplete or too large")
    inspected = current + len(tree.tree)
    if inspected > MAX_GITHUB_TREE_ENTRIES:
        raise _package_limit("The selected GitHub tree contains too many entries")
    return inspected


def _validate_response[ResponseModel: BaseModel](
    model: type[ResponseModel], payload: object, subject: str
) -> ResponseModel:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise _unavailable(f"GitHub returned an invalid {subject} response") from error


def _raise_for_status(response: httpx2.Response) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    if status == 429 or (status == 403 and response.headers.get("x-ratelimit-remaining") == "0"):
        retry_after_seconds = _retry_after_seconds(response.headers.get("retry-after"))
        raise GitHubAcquisitionError(
            "github_rate_limited",
            "GitHub rate limited the acquisition request.",
            retry_after_seconds=retry_after_seconds,
        )
    if status in {401, 403}:
        raise GitHubAcquisitionError("github_auth_failed", "GitHub authentication failed.")
    if status == 404 and "authorization" in response.request.headers:
        raise GitHubAcquisitionError("github_auth_failed", "GitHub authentication failed.")
    if 300 <= status < 500:
        raise _invalid("The GitHub source could not be resolved")
    raise _unavailable("GitHub is temporarily unavailable")


def _retry_after_seconds(value: str | None) -> int | None:
    if value is None or not value.isdecimal():
        return None
    seconds = int(value)
    return seconds if 1 <= seconds <= 86_400 else None


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _invalid(message: str) -> GitHubAcquisitionError:
    return GitHubAcquisitionError("github_source_invalid", message)


def _unavailable(message: str) -> GitHubAcquisitionError:
    return GitHubAcquisitionError("github_unavailable", message)


def _package_limit(message: str) -> SkillPackageError:
    return SkillPackageError("skill_package_limit", message)
