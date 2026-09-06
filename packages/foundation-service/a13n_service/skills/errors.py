"""Safe public errors for Skill Management."""

from __future__ import annotations

from a13n_service.application_errors import ApplicationError, ErrorCategory

from .github import GitHubAcquisitionError
from .objects import SkillPackageStoreError


class SkillError(ApplicationError):
    pass


class GitHubCredentialError(ValueError):
    """The selected Workspace Secret cannot be used for GitHub acquisition."""


def skill_not_found() -> SkillError:
    return SkillError("skill_not_found", "The Skill was not found.", category=ErrorCategory.not_found)


def skill_version_conflict(current_version: int) -> SkillError:
    return SkillError(
        "skill_version_conflict",
        "The Skill version has changed.",
        category=ErrorCategory.conflict,
        details={"current_version": current_version},
    )


def skill_key_conflict() -> SkillError:
    return SkillError(
        "skill_key_conflict",
        "An active Skill already uses this key.",
        category=ErrorCategory.conflict,
    )


def skill_key_mismatch() -> SkillError:
    return SkillError(
        "skill_key_mismatch",
        "The Skill package name does not match the Skill key.",
        category=ErrorCategory.conflict,
    )


def skill_in_use(blocking_agent_count: int) -> SkillError:
    return SkillError(
        "skill_in_use",
        "The Skill is referenced by a current unarchived Agent.",
        category=ErrorCategory.conflict,
        details={"blocking_agent_count": blocking_agent_count},
    )


def invalid_skill_cursor() -> SkillError:
    return SkillError("invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request)


def github_acquisition_error(error: GitHubAcquisitionError) -> SkillError:
    category = {
        "github_source_invalid": ErrorCategory.invalid_request,
        "github_commit_mismatch": ErrorCategory.conflict,
        "github_auth_failed": ErrorCategory.invalid_request,
        "github_rate_limited": ErrorCategory.rate_limited,
        "github_unavailable": ErrorCategory.unavailable,
    }[error.code]
    return SkillError(
        error.code,
        str(error),
        category=category,
        retry_after_seconds=error.retry_after_seconds,
    )


def package_store_error(error: SkillPackageStoreError) -> SkillError:
    return SkillError(
        error.code,
        str(error),
        category=ErrorCategory.unavailable if error.code == "skill_package_unavailable" else ErrorCategory.internal,
    )
