"""Best-effort, owner-only GitHub status for linked review artifacts."""

from __future__ import annotations

import os
import re
import shutil
from collections import OrderedDict
from dataclasses import dataclass
from subprocess import DEVNULL
from time import monotonic
from typing import Literal
from urllib.parse import urlsplit

import httpx2
from anyio import CapacityLimiter, Lock, fail_after, run_process

from a13n_harness_ui.thread_link_models import GitHubLinkView

_TTL = 60
_PATH = re.compile(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(pull|issues|discussions)/([1-9][0-9]{0,9})(?:/.*)?")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z0-9]+")
_DISCUSSION = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    discussion(number: $number) { title closed answerChosenAt }
  }
}
"""
_TOKEN_ENV = {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"}


@dataclass(frozen=True, slots=True)
class _Resource:
    host: str
    owner: str
    repo: str
    kind: Literal["pr", "issue", "discussion"]
    number: int

    @classmethod
    def parse(cls, url: str) -> _Resource | None:
        parts = urlsplit(url)
        host = parts.hostname
        match = _PATH.fullmatch(parts.path)
        if host is None or not _HOST.fullmatch(host) or parts.port is not None or not match:
            return None
        owner, repo, kind, number = match.groups()
        # gh can return its canonical host's stored credentials for aliases.
        # Only canonical hosts may enter enrichment; never send to the alias.
        if (
            host.endswith(".github.com")
            or host == "github.localhost"
            or host.endswith(".github.localhost")
            or (host.endswith(".ghe.com") and host.count(".") != 2)
        ):
            return None
        if owner in {".", ".."} or repo in {".", ".."}:
            return None
        return cls(host, owner, repo, _KIND[kind], int(number))


_KIND: dict[str, Literal["pr", "issue", "discussion"]] = {"pull": "pr", "issues": "issue", "discussions": "discussion"}


async def _host_token(host: str) -> str | None:
    gh = shutil.which("gh")
    if gh is None:
        return None
    # Generic Enterprise environment tokens are not bound to the requested host.
    environment = {key: value for key, value in os.environ.items() if key.upper() not in _TOKEN_ENV}
    try:
        with fail_after(3):
            result = await run_process(
                [gh, "auth", "token", "--hostname", host], env=environment, stderr=DEVNULL, check=False
            )
        return result.stdout.decode("utf-8").strip() or None if result.returncode == 0 else None
    except (OSError, TimeoutError, UnicodeError):
        return None


class GitHubLinkStatus:
    def __init__(self, *, share_computer: bool) -> None:
        self._share_computer = share_computer
        self._tokens: OrderedDict[str, tuple[float, str | None]] = OrderedDict()
        self._statuses: OrderedDict[_Resource, tuple[float, GitHubLinkView | None]] = OrderedDict()
        self._token_lock = Lock()
        self._requests = CapacityLimiter(6)

    async def _token(self, host: str) -> str | None:
        if host == "github.com" and (token := os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
            return token
        if not self._share_computer:
            return None
        async with self._token_lock:
            cached = self._tokens.get(host)
            if cached is not None and monotonic() < cached[0]:
                return cached[1]
            token = await _host_token(host)
            self._tokens[host] = (monotonic() + _TTL, token)
            self._tokens.move_to_end(host)
            if len(self._tokens) > 64:
                self._tokens.popitem(last=False)
            return token

    async def status(self, client: httpx2.AsyncClient, url: str) -> GitHubLinkView | None:
        resource = _Resource.parse(url)
        if resource is None:
            return None
        async with self._requests:
            cached = self._statuses.get(resource)
            if cached is not None and monotonic() < cached[0]:
                return cached[1]
            token = await self._token(resource.host)
            public = resource.host == "github.com"
            result = None
            if token or (public and resource.kind != "discussion"):
                result = await self._fetch(client, resource, token)
            if result is None and (latest := self._statuses.get(resource)) is not None:
                result = latest[1]
            if result is None and (public or token):
                result = GitHubLinkView(kind=resource.kind, number=resource.number)
            self._statuses[resource] = (monotonic() + _TTL, result)
            self._statuses.move_to_end(resource)
            if len(self._statuses) > 512:
                self._statuses.popitem(last=False)
            return result

    async def _fetch(self, client: httpx2.AsyncClient, resource: _Resource, token: str | None) -> GitHubLinkView | None:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        cloud = resource.host == "github.com" or resource.host.endswith(".ghe.com")
        host = f"api.{resource.host}" if cloud else resource.host
        base = f"https://{host}" if cloud else f"https://{host}/api/v3"
        try:
            # Redirects stay disabled: only the token's resolved host receives it.
            if resource.kind == "discussion":
                graphql = f"{base}/graphql" if cloud else f"https://{host}/api/graphql"
                response = await client.post(
                    graphql,
                    headers=headers,
                    json={
                        "query": _DISCUSSION,
                        "variables": {"owner": resource.owner, "repo": resource.repo, "number": resource.number},
                    },
                )
            else:
                collection = "pulls" if resource.kind == "pr" else "issues"
                response = await client.get(
                    f"{base}/repos/{resource.owner}/{resource.repo}/{collection}/{resource.number}", headers=headers
                )
            if response.status_code == 401:
                self._tokens.pop(resource.host, None)
            response.raise_for_status()
            data = response.json()
            if resource.kind == "discussion":
                data = data["data"]["repository"]["discussion"]
            title = data["title"]
            if not isinstance(title, str):
                raise TypeError("Expected title")
        except (httpx2.HTTPError, ValueError, KeyError, TypeError):
            return None
        if resource.kind == "discussion":
            status = "answered" if data.get("answerChosenAt") else "closed" if data.get("closed") else "open"
        elif resource.kind == "pr" and data.get("merged"):
            status = "merged"
        elif data.get("state") == "closed":
            status = "closed"
        elif resource.kind == "pr" and data.get("draft"):
            status = "draft"
        else:
            status = "open"
        return GitHubLinkView(kind=resource.kind, number=resource.number, status=status, title=title)
