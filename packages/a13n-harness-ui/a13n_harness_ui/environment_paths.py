"""Harness UI mapping from Project roots to Harness aggregate mount paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EnvironmentPathLayout:
    """Model-facing paths for one admitted Harness UI Environment profile."""

    project_mounts: tuple[str, ...]
    user_skills: str
    content_plugin_roots: tuple[tuple[str, str], ...] = ()
    content_plugin_skills: tuple[tuple[str, str], ...] = ()

    @classmethod
    def resolve(
        cls,
        *,
        canonical_host_paths: bool,
        project_roots: tuple[str | Path, ...],
        user_skills_root: Path | None = None,
        content_plugins: tuple[tuple[str, str, str | None], ...] = (),
    ) -> EnvironmentPathLayout:
        if canonical_host_paths:
            project_mounts = tuple(Path(root).as_posix() for root in project_roots)
            skills_root = (user_skills_root or Path.home() / ".agents" / "skills").expanduser().resolve(strict=False)
            return cls(
                project_mounts=project_mounts,
                user_skills=skills_root.as_posix(),
                content_plugin_roots=tuple(
                    (plugin_id, Path(root).as_posix()) for plugin_id, root, _ in content_plugins
                ),
                content_plugin_skills=tuple(
                    (plugin_id, Path(skills).as_posix())
                    for plugin_id, _, skills in content_plugins
                    if skills is not None
                ),
            )
        return cls(
            project_mounts=tuple(
                "/workspace" if index == 1 else f"/environment/workspace-{index}"
                for index in range(1, len(project_roots) + 1)
            ),
            user_skills="/environment/user-skills",
            content_plugin_roots=tuple(
                (plugin_id, f"/environment/content-plugin-{index}")
                for index, (plugin_id, _, _) in enumerate(content_plugins, start=1)
            ),
            content_plugin_skills=tuple(
                (plugin_id, f"/environment/content-plugin-{index}/{Path(skills).relative_to(root).as_posix()}")
                for index, (plugin_id, root, skills) in enumerate(content_plugins, start=1)
                if skills is not None
            ),
        )


__all__ = ["EnvironmentPathLayout"]
