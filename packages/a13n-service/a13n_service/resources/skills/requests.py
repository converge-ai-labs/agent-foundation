"""Shared request budget and importer construction for package authoring transports."""

from fastapi import Request

from a13n_service.resources.runtime import Runtime
from a13n_service.resources.skills.github import GitHub
from a13n_service.resources.skills.schemas import GitHubSource, UploadSource
from a13n_service.tenancy.requests import Actor, limit_uploads


async def package_importer(
    request: Request, actor: Actor, runtime: Runtime, source: UploadSource | GitHubSource
) -> GitHub:
    """The importer; reading from GitHub counts against the caller's upload budget, as the stored archive does."""
    if isinstance(source, GitHubSource):
        await limit_uploads(request, actor.id)
    settings = runtime.settings
    return GitHub(
        policy=runtime.endpoint_policy,
        timeout=settings.control.import_timeout,
        max_bytes=settings.objects.max_bytes,
    )
