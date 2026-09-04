"""Environment invariants for root and child Agent invocations."""

from __future__ import annotations

from collections.abc import Sequence

from ..domain import (
    EnvironmentExecutionConfig,
    SubagentSelection,
)
from ..errors import (
    agent_revision_not_executable,
)


def require_writable_skill_environment(
    skills: Sequence[object],
    environment: EnvironmentExecutionConfig | None,
) -> None:
    if not skills:
        return
    if environment is None:
        raise agent_revision_not_executable("skill_environment_required")
    if environment.access == "read_only":
        raise agent_revision_not_executable("skill_environment_not_writable")


def validate_child_environment(
    root_environment: EnvironmentExecutionConfig | None,
    child_environment: EnvironmentExecutionConfig | None,
    selection: SubagentSelection,
) -> None:
    mode = selection.environment.mode
    if mode == "none" and child_environment is not None:
        raise agent_revision_not_executable("subagent_environment_required")
    if mode == "dedicated" and child_environment is None:
        raise agent_revision_not_executable("subagent_environment_required")
    if mode == "shared_root":
        if (
            root_environment is None
            or child_environment is None
            or environment_target_identity(root_environment) != environment_target_identity(child_environment)
            or access_rank(child_environment.access) > access_rank(root_environment.access)
        ):
            raise agent_revision_not_executable("subagent_environment_incompatible")


def environment_target_identity(environment: EnvironmentExecutionConfig) -> tuple[object, ...]:
    if environment.environment_target_id is not None:
        return (
            environment.environment_target_id,
            environment.provider_package_revision_id,
            environment.provider_lock,
        )
    return (
        environment.connection,
        environment.provider_package_revision_id,
        environment.provider_lock,
        environment.target_key,
    )


def access_rank(access: str) -> int:
    return {"read_only": 0, "read_write": 1, "full": 2}[access]
