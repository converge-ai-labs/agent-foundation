"""Safe public errors for Skill Management."""

from __future__ import annotations

from .github import GitHubAcquisitionError
from .objects import SkillPackageStoreError


class SkillManagementError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.retry_after_seconds = retry_after_seconds


class GitHubCredentialError(ValueError):
    """The selected Workspace Secret cannot be used for GitHub acquisition."""


def skill_not_found() -> SkillManagementError:
    return SkillManagementError("skill_not_found", "The Skill was not found.", status_code=404)


def skill_version_conflict(current_version: int) -> SkillManagementError:
    return SkillManagementError(
        "skill_version_conflict",
        "The Skill version has changed.",
        status_code=409,
        details={"current_version": current_version},
    )


def invalid_skill_cursor() -> SkillManagementError:
    return SkillManagementError("invalid_cursor", "The collection cursor is invalid.", status_code=400)


def github_management_error(error: GitHubAcquisitionError) -> SkillManagementError:
    status_code = {
        "github_source_invalid": 400,
        "github_commit_mismatch": 409,
        "github_auth_failed": 400,
        "github_rate_limited": 429,
        "github_unavailable": 503,
    }[error.code]
    return SkillManagementError(
        error.code,
        str(error),
        status_code=status_code,
        retry_after_seconds=error.retry_after_seconds,
    )


def package_store_management_error(error: SkillPackageStoreError) -> SkillManagementError:
    return SkillManagementError(
        error.code,
        str(error),
        status_code=503 if error.code == "skill_package_unavailable" else 500,
    )
