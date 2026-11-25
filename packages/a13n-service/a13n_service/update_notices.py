"""Process-local update notices derived from public Service release and advisory metadata."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, ValidationError

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.tenancy.access import OrganizationPath, administering
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.runtime import Runtime

_PACKAGE = "a13n-service"
_REPO = "converge-ai-labs/agent-foundation"
_GITHUB = f"https://github.com/{_REPO}"
_REPO_API = f"https://api.github.com/repos/{_REPO}"
_ADVISORIES = f"{_REPO_API}/security-advisories"
_PYPI = f"https://pypi.org/pypi/{_PACKAGE}/json"
_GHSA_ID = re.compile(r"GHSA-(?:[23456789cfghjmpqrvwx]{4}-){2}[23456789cfghjmpqrvwx]{4}")
_REFRESH_SECONDS = 24 * 60 * 60
_FETCH_SECONDS = 10.0


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


def _stable_version(raw: str) -> Version | None:
    try:
        version = Version(raw)
    except InvalidVersion:
        return None
    if version == Version("0.0.0") or version.is_prerelease:
        return None
    return version


def _stable_candidates(payload: object) -> list[Version]:
    if not isinstance(payload, dict) or not isinstance(releases := payload.get("releases"), dict):
        return []
    return sorted(
        {
            version
            for raw, files in releases.items()
            if (version := _stable_version(raw)) is not None
            and isinstance(files, list)
            and any(isinstance(file, dict) and file.get("yanked") is False for file in files)
        },
        reverse=True,
    )


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


async def _release(client: httpx2.AsyncClient, current: Version) -> StableRelease | None:
    response = await client.get(_PYPI)
    response.raise_for_status()
    candidates = await asyncio.to_thread(lambda: _stable_candidates(response.json()))
    for candidate in candidates:
        tag = f"release/{_PACKAGE}-v{candidate}"
        # PyPI publishes before the final GitHub release. Skip an unfinished release.
        release = await client.get(f"{_REPO_API}/releases/tags/{tag}")
        if release.status_code == 404:
            continue
        release.raise_for_status()
        metadata = release.json()
        if (
            isinstance(metadata, dict)
            and metadata.get("tag_name") == tag
            and metadata.get("draft") is False
            and metadata.get("prerelease") is False
        ):
            return StableRelease(
                version=str(candidate), url=f"{_GITHUB}/releases/tag/{tag}", is_newer=candidate > current
            )
    return None


async def _advisories(client: httpx2.AsyncClient, current: Version) -> tuple[SecurityAdvisory, ...]:
    params: dict[str, str | int] = {"state": "published", "per_page": 100}
    notices: dict[str, SecurityAdvisory] = {}
    while True:
        response = await client.get(_ADVISORIES, params=params, headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            raise ValueError("Expected a list of repository advisories")
        for raw in page:
            if (notice := _match_advisory(raw, current)) is not None:
                notices[notice.id] = notice
        if not (next_page := response.links.get("next")):
            return tuple(notices.values())
        # Extract only GitHub's cursor; all GETs keep the fixed repository endpoint.
        after = httpx2.URL(next_page["url"]).params.get("after")
        if not after or after == params.get("after"):
            raise ValueError("Invalid advisory pagination cursor")
        params["after"] = after


async def _read_upstream[T](
    policy: EndpointPolicy,
    read: Callable[[httpx2.AsyncClient, Version], Awaitable[T]],
    current: Version,
    fallback: T,
) -> T:
    try:
        async with asyncio.timeout(_FETCH_SECONDS):
            async with open_http(policy, timeout=_FETCH_SECONDS, max_bytes=2 * 1024 * 1024) as client:
                return await read(client, current)
    except (httpx2.HTTPError, httpx2.InvalidURL, ServiceError, TimeoutError, ValueError):
        return fallback


class UpdateChecker:
    def __init__(self, current_version: str):
        self.snapshot = UpdateNotices(current_version=current_version, latest_stable=None, advisories=())
        self.current = _stable_version(current_version)

    async def run(self, policy: EndpointPolicy) -> None:
        if self.current is None:
            return
        while True:
            await self.refresh(policy)
            await asyncio.sleep(_REFRESH_SECONDS)

    async def refresh(self, policy: EndpointPolicy) -> None:
        if (current := self.current) is None:
            return
        latest, advisories = await asyncio.gather(
            _read_upstream(policy, _release, current, None), _read_upstream(policy, _advisories, current, ())
        )
        self.snapshot = UpdateNotices(
            current_version=self.snapshot.current_version, latest_stable=latest, advisories=advisories
        )

    async def read(self, runtime: Runtime, actor: Principal, organization_id: str) -> UpdateNotices:
        async with administering(
            runtime.storage,
            runtime.access,
            actor,
            OrganizationPath(organization_id),
            action="organization.update_notices",
            reading=True,
        ):
            return self.snapshot
