"""Agent UI mapping from Project roots to Harness aggregate mount paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EnvironmentPathLayout:
    """Model-facing paths for one admitted Agent UI Environment profile."""

    project_mounts: tuple[str, ...]
    user_skills: str
    content_plugin_skills: tuple[tuple[str, str], ...] = ()

    @classmethod
    def resolve(
        cls,
        *,
        canonical_host_paths: bool,
        project_roots: tuple[str | Path, ...],
        user_skills_root: Path | None = None,
        content_plugin_skills: tuple[tuple[str, str], ...] = (),
    ) -> EnvironmentPathLayout:
        if not project_roots:
            raise ValueError("project_roots must not be empty")
        if canonical_host_paths:
            project_mounts = tuple(Path(root).as_posix() for root in project_roots)
            skills_root = (user_skills_root or Path.home() / ".agents" / "skills").expanduser().resolve(strict=False)
            return cls(
                project_mounts=project_mounts,
                user_skills=skills_root.as_posix(),
                content_plugin_skills=tuple(
                    (plugin_id, Path(path).as_posix()) for plugin_id, path in content_plugin_skills
                ),
            )
        return cls(
            project_mounts=tuple(
                "/workspace" if index == 1 else f"/environment/workspace-{index}"
                for index in range(1, len(project_roots) + 1)
            ),
            user_skills="/environment/user-skills",
            content_plugin_skills=tuple(
                (plugin_id, f"/environment/content-plugin-{index}")
                for index, (plugin_id, _path) in enumerate(content_plugin_skills, start=1)
            ),
        )


__all__ = ["EnvironmentPathLayout"]
