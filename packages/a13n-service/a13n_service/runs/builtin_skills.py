"""Host adapter for builtin local Skills, independent of the run's Environment."""

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from functools import lru_cache
from html import escape
from pathlib import Path

from a13n_harness import AgentContext
from a13n_harness.capabilities.skills import FileSkillSource, SkillCatalogItem, SkillManager
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity, source_tool_id
from a13n_harness.toolsets.files import FileToolset
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset

from a13n_service.providers.registry import web_operations
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.agents.toolsets import catalog
from a13n_service.resources.providers.service import list_provider_types
from a13n_service.runs.builtin_files import ROOT, BuiltinFiles, packaged_text
from a13n_service.runs.runtime import Runtime


@lru_cache(maxsize=1)
def _published() -> dict[str, str]:
    return packaged_text(Path(__file__).parent.parent / "builtin_skills" / "platform-operations")


async def platform_skill(runtime: Runtime) -> "BuiltinSkillCapability":
    files = dict(await asyncio.to_thread(_published))
    files[f"{ROOT}/schemas/agent-config.json"] = json.dumps(AgentConfig.model_json_schema(), indent=2)
    registry = runtime.registry
    toolsets = catalog(
        frozenset(operation for definition in registry.web.values() for operation in web_operations(definition))
    )
    instance = {
        "providers": {
            kind: list_provider_types(registry, kind).model_dump(mode="json")
            for kind in ("model", "environment", "connector", "web")
        },
        "toolsets": toolsets.model_dump(mode="json"),
        "plugins": list(runtime.settings.plugins.keys),
    }
    files[f"{ROOT}/instance/catalog.json"] = json.dumps(instance, indent=2)
    # A versioned instruction invalidates stale model assumptions after deployment or catalog changes.
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
    operator = BuiltinFiles(files)
    manager = SkillManager((FileSkillSource("builtin", (ROOT,)),))
    items = await manager.scan(files=operator)
    return BuiltinSkillCapability(operator, items, digest)


@dataclass
class BuiltinSkillCapability(AbstractCapability[AgentContext]):
    files: BuiltinFiles
    items: tuple[SkillCatalogItem, ...]
    digest: str

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return (
            FileToolset(self.files)
            .get_toolset(include_mutations=False, allowed_names=frozenset({"view", "ls", "glob", "grep"}))
            .prepared(_identities)
            .prefixed("platform")
        )

    def get_instructions(self) -> str:
        lines = [
            f"Builtin skill catalog version: {self.digest}. If this changed, reread the relevant documents.",
            "Use platform_view to read SKILL.md before platform tasks, then its references as needed.",
            "platform_ls, platform_glob and platform_grep read only these local text documents.",
            "These tools require no user environment and grant no platform operation permissions.",
            "<available-skills>",
        ]
        for item in self.items:
            lines.append(
                f'<skill name="{escape(item.name, quote=True)}" path="{escape(item.path, quote=True)}/SKILL.md">{escape(item.description)}</skill>'
            )
        return "\n".join([*lines, "</available-skills>"])


def _identities(_ctx: RunContext[AgentContext], tools: list[ToolDefinition]) -> list[ToolDefinition]:
    """Local instructions have independent read permissions from the user's environment files."""
    return [
        replace(
            tool,
            metadata={
                **(tool.metadata or {}),
                TOOL_IDENTITY_KEY: ToolIdentity(source_tool_id("builtin_skills", tool.name)),
            },
        )
        for tool in tools
    ]
