"""Acquire and verify one publication source outside durable publication transactions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.storage import transaction

from .domain import (
    GitHubRevisionSource,
    SkillImportProvenance,
    ZipSkillImportProvenance,
    ZipUploadSkillSource,
)
from .errors import (
    GitHubCredentialError,
    SkillManagementError,
    github_management_error,
    package_store_management_error,
)
from .github import AcquiredGitHubSkill, GitHubAcquisitionError
from .objects import SkillPackageStore, SkillPackageStoreError
from .package import NormalizedSkillPackage, SkillPackageError
from .persistence import require_owned_upload, require_upload_manifest
from .support import authorize_skill_workspace


class GitHubCredentialResolver(Protocol):
    async def resolve(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        action: WorkspaceAction,
        secret_id: str,
    ) -> str: ...


class GitHubSourceAcquirer(Protocol):
    async def acquire(
        self,
        source: GitHubRevisionSource,
        *,
        credential: str | None = None,
    ) -> AcquiredGitHubSkill: ...


@dataclass(frozen=True, slots=True)
class PreparedSkillSource:
    package: NormalizedSkillPackage
    provenance: SkillImportProvenance
    upload_id: str | None


class SkillSourcePreparer:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        packages: SkillPackageStore,
        github: GitHubSourceAcquirer | None,
        credentials: GitHubCredentialResolver | None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._packages = packages
        self._github = github
        self._credentials = credentials
        self._clock = clock or (lambda: datetime.now(UTC))

    async def prepare(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        action: WorkspaceAction,
        source: ZipUploadSkillSource | GitHubRevisionSource,
    ) -> PreparedSkillSource:
        if isinstance(source, ZipUploadSkillSource):
            return await self._prepare_upload(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                action=action,
                upload_id=source.upload_id,
            )
        return await self._prepare_github(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            action=action,
            source=source,
        )

    async def _prepare_upload(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        action: WorkspaceAction,
        upload_id: str,
    ) -> PreparedSkillSource:
        async with transaction(self._sessions) as session:
            await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=action,
                concealed_code="skill_upload_not_found",
            )
            upload = await require_owned_upload(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                upload_id=upload_id,
                now=self._clock(),
            )
            manifest = require_upload_manifest(upload)
            provenance = ZipSkillImportProvenance(archive_sha256=upload.archive_sha256)
        try:
            package = await self._packages.read_verified_package(
                organization_id=organization_id,
                workspace_id=workspace_id,
                manifest=manifest,
            )
        except SkillPackageStoreError as error:
            raise package_store_management_error(error) from error
        return PreparedSkillSource(package=package, provenance=provenance, upload_id=upload_id)

    async def _prepare_github(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        action: WorkspaceAction,
        source: GitHubRevisionSource,
    ) -> PreparedSkillSource:
        if self._github is None:
            raise SkillManagementError(
                "github_unavailable",
                "GitHub acquisition is unavailable.",
                status_code=503,
            )
        credential = await self._resolve_credential(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            action=action,
            source=source,
        )
        try:
            acquired = await self._github.acquire(source, credential=credential)
            await self._packages.publish(
                organization_id=organization_id,
                workspace_id=workspace_id,
                package=acquired.package,
            )
        except GitHubAcquisitionError as error:
            raise github_management_error(error) from error
        except SkillPackageError as error:
            raise SkillManagementError(error.code, str(error), status_code=400) from error
        except SkillPackageStoreError as error:
            raise package_store_management_error(error) from error
        return PreparedSkillSource(package=acquired.package, provenance=acquired.provenance, upload_id=None)

    async def _resolve_credential(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        action: WorkspaceAction,
        source: GitHubRevisionSource,
    ) -> str | None:
        if source.credential_secret_id is None:
            return None
        if self._credentials is None:
            raise _github_credential_unavailable()
        try:
            return await self._credentials.resolve(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                action=action,
                secret_id=source.credential_secret_id,
            )
        except GitHubCredentialError as error:
            raise _github_credential_unavailable() from error


def _github_credential_unavailable() -> SkillManagementError:
    return SkillManagementError(
        "github_auth_failed",
        "The selected GitHub credential is unavailable.",
        status_code=400,
    )
