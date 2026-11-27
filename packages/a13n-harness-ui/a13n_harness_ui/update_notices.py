"""Release and security notices shared by the WebUI and terminal."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Literal

import httpx2
from filelock import FileLock
from httpx2 import AsyncClient
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, ValidationError

from a13n_harness_ui import __version__

_PACKAGE = "a13n-harness-ui"
_REPO = "converge-ai-labs/agent-foundation"
_GITHUB = f"https://github.com/{_REPO}"
_REPO_API = f"https://api.github.com/repos/{_REPO}"
_ADVISORIES = f"{_REPO_API}/security-advisories"
_PYPI = f"https://pypi.org/simple/{_PACKAGE}/"
_GHSA_ID = re.compile(r"GHSA-(?:[23456789cfghjmpqrvwx]{4}-){2}[23456789cfghjmpqrvwx]{4}")
_REFRESH_SECONDS = 24 * 60 * 60
_RETRY_SECONDS = 60 * 60
_FETCH_SECONDS = 10.0
_TERMINAL_FETCH_SECONDS = 3.0
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class StableRelease(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    url: str
    is_newer: bool


class SecurityAdvisory(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    summary: str
    severity: Literal["high", "critical"]
    url: str
    updated_at: str | None


class UpdateNotices(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_version: str
    latest_stable: StableRelease | None
    advisories: tuple[SecurityAdvisory, ...]
    install_method: Literal["uv_tool", "other"]


class _Package(BaseModel):
    ecosystem: str
    name: str | None


class _Affected(BaseModel):
    package: _Package
    vulnerable_version_range: str | None

    def includes(self, current: Version) -> bool:
        package, bounds = self.package, self.vulnerable_version_range
        if package.ecosystem != "pip" or not package.name or canonicalize_name(package.name) != _PACKAGE or not bounds:
            return False
        bounds = bounds.strip()
        # GitHub spells its single-version operator `=`, while PEP 440 uses `==`.
        if bounds.startswith("=") and not bounds.startswith("=="):
            bounds = "=" + bounds
        try:
            return current in SpecifierSet(bounds) if bounds else False
        except InvalidSpecifier:
            return False


def installed_version(raw: str = __version__) -> Version | None:
    """Only published-version builds participate in update detection."""
    try:
        version = Version(raw)
    except InvalidVersion:
        return None
    if version.base_version == "0.0.0" or version.is_prerelease:
        return None
    return version


def _stable_candidates(payload: object) -> list[Version]:
    if not isinstance(payload, dict) or not isinstance(files := payload.get("files"), list):
        raise ValueError("Expected a list of PyPI distribution files")
    candidates: set[Version] = set()
    for file in files:
        if not isinstance(file, dict) or file.get("yanked", False) is not False:
            continue
        if not isinstance(filename := file.get("filename"), str):
            continue
        try:
            if filename.endswith(".whl"):
                name, version, _, _ = parse_wheel_filename(filename)
            else:
                name, version = parse_sdist_filename(filename)
        except (InvalidWheelFilename, InvalidSdistFilename):
            continue
        if name == _PACKAGE and version.base_version != "0.0.0" and not version.is_prerelease:
            candidates.add(version)
    return sorted(candidates, reverse=True)


def _match_advisory(raw: object, current: Version) -> SecurityAdvisory | None:
    if not isinstance(raw, dict) or raw.get("state") != "published" or raw.get("withdrawn_at") is not None:
        return None
    advisory_id, severity, summary = raw.get("ghsa_id"), raw.get("severity"), raw.get("summary")
    if not isinstance(advisory_id, str) or not _GHSA_ID.fullmatch(advisory_id):
        return None
    if severity not in ("high", "critical") or not isinstance(summary, str) or not summary.strip():
        return None
    affected = raw.get("vulnerabilities")
    if not isinstance(affected, list):
        return None
    for item in affected:
        try:
            vulnerability = _Affected.model_validate(item)
        except ValidationError:
            continue
        if vulnerability.includes(current):
            updated_at = raw.get("updated_at")
            return SecurityAdvisory(
                id=advisory_id,
                severity=severity,
                summary=summary.strip(),
                url=f"{_GITHUB}/security/advisories/{advisory_id}",
                updated_at=updated_at if isinstance(updated_at, str) else None,
            )
    return None


async def _get_json(
    client: AsyncClient,
    url: str,
    *,
    accept: str = "application/vnd.github+json",
    params: dict[str, str | int] | None = None,
) -> tuple[object, str | None]:
    async with client.stream("GET", url, headers={"Accept": accept}, params=params) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                raise ValueError("Update metadata exceeds the response size limit")
            body.extend(chunk)
        return json.loads(body), response.links.get("next", {}).get("url")


async def _release(client: AsyncClient) -> str | None:
    payload, _ = await _get_json(client, _PYPI, accept="application/vnd.pypi.simple.v1+json")
    for candidate in await asyncio.to_thread(_stable_candidates, payload):
        tag = f"release/{_PACKAGE}-v{candidate}"
        try:
            metadata, _ = await _get_json(client, f"{_REPO_API}/releases/tags/{tag}")
        except httpx2.HTTPStatusError as error:
            if error.response.status_code == 404:
                # PyPI publishes before the final GitHub Release.
                continue
            raise
        if (
            isinstance(metadata, dict)
            and metadata.get("tag_name") == tag
            and metadata.get("draft") is False
            and metadata.get("prerelease") is False
        ):
            return str(candidate)
    return None


async def _advisories(client: AsyncClient, current: Version) -> tuple[SecurityAdvisory, ...]:
    params: dict[str, str | int] = {"state": "published", "per_page": 100}
    notices: dict[str, SecurityAdvisory] = {}
    while True:
        page, next_url = await _get_json(client, _ADVISORIES, params=params)
        if not isinstance(page, list):
            raise ValueError("Expected a list of repository advisories")
        for raw in page:
            if (notice := _match_advisory(raw, current)) is not None:
                notices[notice.id] = notice
        if next_url is None:
            return tuple(notices.values())
        # Only the cursor can vary; all GETs keep the fixed repository endpoint.
        after = httpx2.URL(next_url).params.get("after")
        if not after or after == params.get("after"):
            raise ValueError("Invalid advisory pagination cursor")
        params["after"] = after


class _CachedRelease(BaseModel):
    checked_at: float
    version: str | None


class _CachedAdvisories(BaseModel):
    checked_at: float
    checked_version: str
    advisories: tuple[SecurityAdvisory, ...]


class _Cache(BaseModel):
    latest_stable: _CachedRelease | None = None
    advisories: _CachedAdvisories | None = None


_Source = Literal["latest_stable", "advisories"]


def _read_cache(path: Path) -> _Cache:
    try:
        return _Cache.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return _Cache()


def _write_cache(path: Path, source: _Source, entry: _CachedRelease | _CachedAdvisories) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # A TUI launch can refresh the same file while WebUI publishes an advisory.
        with FileLock(path.with_suffix(".lock"), timeout=0.2):
            cache = _read_cache(path)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
                temporary = Path(file.name)
            try:
                temporary.write_text(cache.model_copy(update={source: entry}).model_dump_json(), encoding="utf-8")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    except (OSError, TimeoutError):
        pass  # The successfully fetched value remains available in this process.


class UpdateChecker:
    def __init__(
        self,
        root: Path,
        current_version: str = __version__,
        *,
        install_method: Literal["uv_tool", "other"] = "other",
    ) -> None:
        self.path = root / "cache" / "update-notices.json"
        self.current_version = current_version
        self.install_method: Literal["uv_tool", "other"] = install_method
        self.current = installed_version(current_version)
        self._cache = _Cache()

    async def load(self) -> None:
        if self.current is not None:
            self._cache = await asyncio.to_thread(_read_cache, self.path)

    def _advisory_cache(self) -> _CachedAdvisories | None:
        entry = self._cache.advisories
        return entry if entry is not None and entry.checked_version == self.current_version else None

    @property
    def snapshot(self) -> UpdateNotices:
        release = self._cache.latest_stable
        candidate = installed_version(release.version) if release is not None and release.version is not None else None
        latest = (
            StableRelease(
                version=str(candidate),
                url=f"{_GITHUB}/releases/tag/release/{_PACKAGE}-v{candidate}",
                is_newer=candidate > self.current,
            )
            if candidate is not None and self.current is not None
            else None
        )
        advisory_cache = self._advisory_cache()
        return UpdateNotices(
            current_version=self.current_version,
            latest_stable=latest,
            advisories=advisory_cache.advisories if advisory_cache is not None else (),
            install_method=self.install_method,
        )

    def _remaining_delay(self, source: _Source) -> float:
        entry = self._cache.latest_stable if source == "latest_stable" else self._advisory_cache()
        age = time.time() - entry.checked_at if entry is not None else _REFRESH_SECONDS
        return _REFRESH_SECONDS - age if 0 <= age < _REFRESH_SECONDS else 0

    async def check_release(self) -> StableRelease | None:
        """Return a release from the shared cache or a short terminal startup check."""
        if self.current is None:
            return None
        await self.load()
        if not self._remaining_delay("latest_stable"):
            await self._refresh("latest_stable", _TERMINAL_FETCH_SECONDS)
        return self.snapshot.latest_stable

    async def run(self) -> None:
        if self.current is None:
            return
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._poll("latest_stable"))
            tasks.create_task(self._poll("advisories"))

    async def _poll(self, source: _Source) -> None:
        while True:
            if delay := self._remaining_delay(source):
                await asyncio.sleep(delay)
            if source == "latest_stable":
                # A terminal launch may have refreshed the shared entry while WebUI slept.
                persisted = (await asyncio.to_thread(_read_cache, self.path)).latest_stable
                cached = self._cache.latest_stable
                if persisted is not None and (cached is None or persisted.checked_at > cached.checked_at):
                    self._cache = self._cache.model_copy(update={source: persisted})
                    if self._remaining_delay(source):
                        continue
            if not await self._refresh(source):
                await asyncio.sleep(_RETRY_SECONDS)

    async def _refresh(self, source: _Source, timeout: float = _FETCH_SECONDS) -> bool:
        from a13n_harness.http import outbound_tls_verify

        assert self.current is not None
        try:
            async with (
                asyncio.timeout(timeout),
                AsyncClient(verify=outbound_tls_verify(), timeout=timeout, headers={"User-Agent": _PACKAGE}) as client,
            ):
                if source == "latest_stable":
                    version = await _release(client)
                    entry = _CachedRelease(checked_at=time.time(), version=version)
                else:
                    advisories = await _advisories(client, self.current)
                    entry = _CachedAdvisories(
                        checked_at=time.time(), checked_version=self.current_version, advisories=advisories
                    )
        except (httpx2.HTTPError, httpx2.InvalidURL, OSError, ValueError):
            return False
        self._cache = self._cache.model_copy(update={source: entry})
        await asyncio.to_thread(_write_cache, self.path, source, entry)
        return True
