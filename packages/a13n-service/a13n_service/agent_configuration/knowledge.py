"""Deployment-owned Skill files exposed through confined read-only Direct Local."""

from __future__ import annotations

from pathlib import Path

from a13n_harness.capabilities import FileSkillSource, SkillManager, SkillsCapability
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.providers.environment.management import Environment

from .definition import ASSETS
from .persistence import failure


class KnowledgeFiles:
    def __init__(self, root: Path = ASSETS / "skills") -> None:
        self._root = root

    def validate(self) -> Path:
        """Check the installed read-only source without locking or retaining its contents."""
        try:
            if self._root.is_symlink() or not self._root.is_dir():
                raise ValueError("The Skill directory is unavailable.")
            root = self._root.resolve()
            entry = root / "configure-agent" / "SKILL.md"
            if not entry.is_file():
                raise ValueError("The configuration Skill entry is unavailable.")
            size = 0
            for path in root.rglob("*"):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("The Skill directory is not confined.")
                if path.is_file():
                    size += path.stat().st_size
            if size > 512 * 1024:
                raise ValueError("The configuration Skill exceeds its content limit.")
            return root
        except (OSError, ValueError) as error:
            raise failure(
                "configuration_knowledge_unavailable", "The deployed configuration Skill is unavailable."
            ) from error

    def environment(self) -> Environment:
        root = self.validate()
        provider = DIRECT_LOCAL
        configuration = provider.validate_environment(
            schema_version="1", value={"root": {"path": str(root), "read_only": True}, "max_value_bytes": 512 * 1024}
        )
        return provider.construct(
            environment_id="configuration-knowledge",
            configuration=configuration,
            state=None,
            runtime=None,
        )


def knowledge_capability() -> SkillsCapability:
    return SkillsCapability(SkillManager((FileSkillSource("configuration", ("/environment/builtin-skills",)),)))
