"""Skill control-plane construction."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx2

from a13n_service.process.components import ServiceComponents
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.skills.catalog import SkillCatalogService
from a13n_service.skills.credentials import DatabaseGitHubCredentialResolver
from a13n_service.skills.github import GitHubSkillAcquirer
from a13n_service.skills.publication import SkillPublicationService
from a13n_service.skills.sources import SkillSourcePreparer
from a13n_service.skills.uploads import SkillUploadService


@dataclass(frozen=True, slots=True)
class _SkillBundle:
    uploads: SkillUploadService
    publication: SkillPublicationService
    catalog: SkillCatalogService


async def build_skill_bundle(
    components: ServiceComponents,
    shared: SharedRuntime,
    execution: ExecutionResources,
    stack: AsyncExitStack,
) -> _SkillBundle:
    """Construct Skill upload, publication, and catalog services."""

    github_acquirer = components.skill_github_acquirer
    if github_acquirer is None:
        github_http_client = await stack.enter_async_context(httpx2.AsyncClient(follow_redirects=False))
        github_acquirer = GitHubSkillAcquirer(github_http_client)
    credential_resolver = components.skill_credential_resolver
    if credential_resolver is None:
        credential_resolver = DatabaseGitHubCredentialResolver(
            shared.storage.sessions,
            shared.secret_protector,
        )
    uploads = SkillUploadService(shared.storage.sessions, execution.skill_package_store)
    source_preparer = SkillSourcePreparer(
        shared.storage.sessions,
        execution.skill_package_store,
        github_acquirer,
        credential_resolver,
    )
    return _SkillBundle(
        uploads=uploads,
        publication=SkillPublicationService(shared.storage.sessions, source_preparer),
        catalog=SkillCatalogService(shared.storage.sessions, execution.skill_package_store),
    )


__all__ = ["build_skill_bundle"]
