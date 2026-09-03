"""Safe public errors for Skill Management."""

from __future__ import annotations

from a13n_service.public_errors import PublicError

from .github import GitHubAcquisitionError
from .objects import SkillPackageStoreError


class SkillError(PublicError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        headers = {"Retry-After": str(retry_after_seconds)} if retry_after_seconds is not None else None
        super().__init__(code, message, status_code=status_code, details=details, headers=headers)
        self.retry_after_seconds = retry_after_seconds


class GitHubCredentialError(ValueError):
    """The selected Workspace Secret cannot be used for GitHub acquisition."""


def skill_not_found() -> SkillError:
    return SkillError("skill_not_found", "The Skill was not found.", status_code=404)


def skill_version_conflict(current_version: int) -> SkillError:
    return SkillError(
        "skill_version_conflict",
        "The Skill version has changed.",
        status_code=409,
        details={"current_version": current_version},
    )


def skill_key_conflict() -> SkillError:
    return SkillError(
        "skill_key_conflict",
        "An active Skill already uses this key.",
        status_code=409,
    )


def skill_key_mismatch() -> SkillError:
    return SkillError(
        "skill_key_mismatch",
        "The Skill package name does not match the Skill key.",
        status_code=409,
    )


def skill_in_use(blocking_agent_count: int) -> SkillError:
    return SkillError(
        "skill_in_use",
        "The Skill is referenced by a current unarchived Agent.",
        status_code=409,
        details={"blocking_agent_count": blocking_agent_count},
    )


def invalid_skill_cursor() -> SkillError:
    return SkillError("invalid_cursor", "The collection cursor is invalid.", status_code=400)


def github_acquisition_error(error: GitHubAcquisitionError) -> SkillError:
    status_code = {
        "github_source_invalid": 400,
        "github_commit_mismatch": 409,
        "github_auth_failed": 400,
        "github_rate_limited": 429,
        "github_unavailable": 503,
    }[error.code]
    return SkillError(
        error.code,
        str(error),
        status_code=status_code,
        retry_after_seconds=error.retry_after_seconds,
    )


def package_store_error(error: SkillPackageStoreError) -> SkillError:
    return SkillError(
        error.code,
        str(error),
        status_code=503 if error.code == "skill_package_unavailable" else 500,
    )
