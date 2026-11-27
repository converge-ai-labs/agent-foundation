"""Thread-owned bookmarks; Host queries enrich their owner-only WebUI view."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from html import escape
from pathlib import Path

import httpx2
from a13n_harness.model_context import ModelContextBlock, ModelContextPlacement
from anyio import CapacityLimiter
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from a13n_harness_ui.errors import HarnessUiError, ThreadError
from a13n_harness_ui.github_link_status import GitHubLinkStatus
from a13n_harness_ui.host_git import GitRepository, HostGit
from a13n_harness_ui.storage.contracts import Thread
from a13n_harness_ui.storage.repositories import ThreadRepository
from a13n_harness_ui.thread_link_models import (
    ThreadLink,
    ThreadLinkKind,
    ThreadLinksView,
    UrlLink,
    UrlLinkView,
    WorktreeLink,
    WorktreeLinkView,
)

WORKTREE_PRESET = (
    "Work in an isolated linked Git worktree for this task. Follow the repository's conventions for placement, "
    "branch naming, base revision, and setup. When available, register the worktree with "
    "link_thread_resource(kind='worktree', target=<absolute Host path>) as soon as it is ready. "
    "Keep task changes in that worktree."
)

THREAD_LINK_GUIDANCE = (
    "Use link_thread_resource to register worktrees and external artifacts you actively use in this chat. "
    "Use kind worktree with an absolute Host path, or kind url with an HTTP(S) URL. "
    "<thread-links> lists the current references. Links do not change the working directory, mount paths, "
    "or grant access. Use unlink_thread_resource to remove an obsolete reference, without deleting its target."
)


def _invalid(message: str) -> ThreadError:
    return ThreadError(message, code="thread_link_invalid")


def _path(target: str) -> str:
    if not Path(target).is_absolute() or "\x00" in target:
        raise _invalid("Use an absolute Host worktree path.")
    return os.path.normcase(os.path.normpath(target))


_HTTP_URL = TypeAdapter(AnyHttpUrl)


def _url(target: str) -> str:
    target = target.strip()
    if "\\" in target or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in target
    ):
        raise _invalid("Use an HTTP(S) URL without whitespace or control characters.")
    try:
        url = _HTTP_URL.validate_python(target)
    except ValidationError:
        raise _invalid("Use an absolute HTTP(S) URL without user credentials.") from None
    if "://" not in target or url.username is not None or url.password is not None:
        raise _invalid("Use an absolute HTTP(S) URL without user credentials.")
    return str(url)


def _target(kind: ThreadLinkKind, value: str) -> str:
    if not 1 <= len(value) <= 4096:
        raise _invalid("Thread link targets must be between 1 and 4096 characters.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid("Thread link targets must be representable as UTF-8.") from None
    return _path(value) if kind == "worktree" else _url(value)


class ThreadLinks:
    def __init__(self, threads: ThreadRepository, host_git: HostGit, *, changed: Callable[[str], None]) -> None:
        self._threads = threads
        self._git = host_git
        self._changed = changed
        self._git_slots = CapacityLimiter(4)
        self._github = GitHubLinkStatus(share_computer=host_git.enabled)

    async def _thread(self, thread_id: str) -> Thread:
        thread = await self._threads.get(thread_id)
        if thread is None:
            raise ThreadError("Thread is unavailable.", code="thread_not_found")
        return thread

    def _native_git(self, thread: Thread) -> bool:
        return thread.configuration.environment_profile_id == "environment-native" and self._git.available

    async def _discover(self, path: str) -> tuple[GitRepository | None, bool]:
        try:
            async with self._git_slots:
                return (await self._git.discover(path)).repository, False
        except HarnessUiError as exc:
            return None, exc.code == "host_git_not_found"

    async def require_worktree_mode(self, thread: Thread, environment_profile_id: str, roots: tuple[str, ...]) -> None:
        if (
            thread.configuration.project_id
            and environment_profile_id == "environment-native"
            and self._git.available
            and roots
        ):
            repository, _ = await self._discover(roots[0])
            if repository is not None:
                return
        raise ThreadError("Worktree mode requires a Git project in Full Control.", code="worktree_mode_unavailable")

    async def _canonical_path(self, path: str, *, allow_host_git: bool) -> str:
        if allow_host_git and self._git.available:
            repository, _ = await self._discover(path)
            if repository is not None:
                return _path(repository.root)
        return path

    async def link(
        self, thread_id: str, kind: ThreadLinkKind, target: str, label: str | None, *, allow_host_git: bool
    ) -> ThreadLink:
        normalized = _target(kind, target)
        label = (label.strip() or None) if label is not None else None
        if label is not None:
            if len(label) > 120:
                raise _invalid("Thread link labels may contain up to 120 characters.")
            try:
                label.encode("utf-8")
            except UnicodeEncodeError:
                raise _invalid("Thread link labels must be representable as UTF-8.") from None
        await self._thread(thread_id)
        if kind == "worktree":
            normalized = await self._canonical_path(normalized, allow_host_git=allow_host_git)
        link = await self._threads.link(thread_id, kind, normalized, label)
        self._changed(thread_id)
        return link

    async def unlink(self, thread_id: str, kind: ThreadLinkKind, target: str, *, allow_host_git: bool = False) -> bool:
        await self._thread(thread_id)
        normalized = _target(kind, target)
        removed = await self._threads.unlink(thread_id, kind, normalized)
        if not removed and allow_host_git and kind == "worktree":
            canonical = await self._canonical_path(normalized, allow_host_git=True)
            if canonical != normalized:
                removed = await self._threads.unlink(thread_id, kind, canonical)
        if removed:
            self._changed(thread_id)
        return removed

    async def context(self, thread_id: str) -> ModelContextBlock:
        links = await self._threads.links(thread_id)
        content = "\n".join(
            f'<{link.kind} {"path" if isinstance(link, WorktreeLink) else "url"}="'
            f'{escape(link.path if isinstance(link, WorktreeLink) else link.url, quote=True)}"'
            + (f' label="{escape(link.label, quote=True)}"' if link.label else "")
            + " />"
            for link in links
        )
        return ModelContextBlock(
            source_id="a13n-harness-ui.thread-links",
            placement=ModelContextPlacement.REQUEST_EPILOGUE,
            context_group="runtime_state",
            content=f'<thread-links thread_id="{escape(thread_id, quote=True)}">\n{content}\n</thread-links>',
        )

    async def view(self, thread_id: str) -> ThreadLinksView:
        thread = await self._thread(thread_id)
        links = await self._threads.links(thread_id)
        worktrees, mode_available = await self._worktrees(
            thread, [link for link in links if isinstance(link, WorktreeLink)]
        )
        urls = [link for link in links if isinstance(link, UrlLink)]
        if not urls:
            return ThreadLinksView(worktrees=worktrees, urls=(), worktree_mode_available=mode_available)
        async with httpx2.AsyncClient(timeout=5, follow_redirects=False) as client:
            statuses = await asyncio.gather(*(self._github.status(client, link.url) for link in urls))
        return ThreadLinksView(
            worktrees=worktrees,
            worktree_mode_available=mode_available,
            urls=tuple(
                UrlLinkView(**link.model_dump(), github=status) for link, status in zip(urls, statuses, strict=True)
            ),
        )

    async def _worktrees(self, thread: Thread, links: list[WorktreeLink]) -> tuple[tuple[WorktreeLinkView, ...], bool]:
        native = self._native_git(thread)
        root = thread.configuration.local_roots[0] if native and thread.configuration.local_roots else None
        paths = dict.fromkeys(link.path for link in links) if native else {}
        if root is not None:
            paths[_path(root)] = None
        observations = dict(zip(paths, await asyncio.gather(*(self._discover(path) for path in paths)), strict=True))
        primary, _ = observations.get(_path(root), (None, False)) if root is not None else (None, False)
        worktrees: dict[str, WorktreeLinkView] = {}
        for link in links:
            repository, removed = observations.get(link.path, (None, False))
            # The saved reference remains the action target, even if the path is
            # now nested inside another repository.
            if repository is not None and _path(repository.root) != link.path:
                repository = None
            worktrees[link.path] = _worktree_view(link.path, link.label, repository, removed=removed)
        if primary is not None and _path(primary.git_dir) != _path(primary.common_dir):
            path = _path(primary.root)
            if path not in worktrees:
                worktrees[path] = _worktree_view(path, None, primary, implicit=True)
        return tuple(worktrees.values()), primary is not None and thread.configuration.project_id is not None


def _worktree_view(
    path: str, label: str | None, repository: GitRepository | None, *, removed: bool = False, implicit: bool = False
) -> WorktreeLinkView:
    if repository is None:
        return WorktreeLinkView(
            path=path, label=label, implicit=implicit, state="removed" if removed else "unavailable"
        )
    return WorktreeLinkView(
        path=path,
        label=label,
        implicit=implicit,
        state="ready" if repository.branch is not None else "detached",
        branch=repository.branch,
        head_oid=repository.head_oid,
    )
