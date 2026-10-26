"""Read-only configuration summaries and explicit configuration sections."""

import difflib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from a13n_service.infra.reads import Preview, digest, json_text, preview
from a13n_service.resources import catalog_reads
from a13n_service.resources.agents.schemas import AgentConfig, AgentRevision
from a13n_service.resources.agents.toolsets import ToolsetCatalog

# Whole configuration is available for lossless editing; named sections are never writable replacements.
type ConfigSection = Literal[
    "all",
    "model",
    "model_settings",
    "model_characteristics",
    "instructions",
    "toolsets",
    "skills",
    "skill_sync",
    "connection_tools",
    "tool_search",
    "builtin_skills",
    "client_tools",
    "user_questions",
    "subagent_mode",
    "subagents",
    "reviewer",
    "media_understanding",
    "plugins",
    "output_spec",
    "retries",
    "default_environment_template_id",
    "lazy_environment",
    "memory_mounts",
    "memory",
]


class ConfigSummary(BaseModel):
    model: str
    toolsets: list[str]
    connection_count: int
    skill_count: int
    subagent_count: int
    omitted_sections: tuple[str, ...] = ("configuration",)


def config_summary(value: AgentConfig) -> ConfigSummary:
    return ConfigSummary(
        model=value.model,
        toolsets=sorted(key for key, selection in value.toolsets.items() if selection.enabled),
        connection_count=len(value.connection_tools),
        skill_count=len(value.skills) + len(value.builtin_skills),
        subagent_count=len(value.subagents),
    )


class RevisionSummary(BaseModel):
    view: Literal["compact"] = "compact"
    id: str
    agent_id: str
    number: int
    digest: str
    is_default: bool
    created_at: datetime
    note: Preview
    config: ConfigSummary
    omitted_sections: tuple[str, ...] = ("config", "note")


class RevisionSummaryPage(BaseModel):
    items: list[RevisionSummary]
    next_cursor: str | None


def revision_summary(value: AgentRevision, default_revision_id: str | None) -> RevisionSummary:
    return RevisionSummary(
        id=value.id,
        agent_id=value.agent_id,
        number=value.number,
        digest=value.digest,
        is_default=value.id == default_revision_id,
        created_at=value.created_at,
        note=preview(value.note),
        config=config_summary(value.config),
    )


def config_section(value: AgentConfig, section: ConfigSection) -> object:
    return value if section == "all" else getattr(value, section)


def config_diff(base: AgentConfig, target: AgentConfig, section: ConfigSection) -> str:
    def lines(value: AgentConfig) -> list[str]:
        data = json.loads(json_text(value))
        selected = data if section == "all" else data.get(section)
        return (json.dumps(selected, sort_keys=True, ensure_ascii=False, indent=2) + "\n").splitlines(keepends=True)

    return "".join(
        difflib.unified_diff(lines(base), lines(target), fromfile="base/" + section, tofile="target/" + section)
    )


class RunAgentSummary(BaseModel):
    view: Literal["compact"] = "compact"
    revision_id: str
    config: ConfigSummary
    connection_ids: list[str]
    skill_ids: list[str]
    subagent_ids: list[str]
    overridden_paths: list[str]
    references_truncated: bool
    omitted_sections: tuple[str, ...] = ("original", "effective", "overrides")


def run_agent_summary(revision: AgentRevision, effective: AgentConfig) -> RunAgentSummary:
    original_data, effective_data = revision.config.model_dump(mode="json"), effective.model_dump(mode="json")
    # Paths name whole replaced top-level sections, not a claim that nested values are all different.
    paths = sorted(
        key for key in original_data.keys() | effective_data.keys() if original_data.get(key) != effective_data.get(key)
    )
    return RunAgentSummary(
        revision_id=revision.id,
        config=config_summary(effective),
        connection_ids=[value.connection_id for value in effective.connection_tools[:10]],
        skill_ids=[value.skill_id for value in effective.skills[:10]],
        subagent_ids=[value.agent_id for value in list(effective.subagents.values())[:10]],
        overridden_paths=paths,
        references_truncated=any(
            count > 10 for count in (len(effective.connection_tools), len(effective.skills), len(effective.subagents))
        ),
    )


def toolset_index(
    catalog: ToolsetCatalog, *, workspace_id: str, query: str | None, limit: int, cursor: str | None
) -> catalog_reads.CatalogIndex:
    records = [
        catalog_reads.CatalogIndexItem(
            name=value.key,
            description=value.description,
            tool_count=len(value.tools),
            supported_tool_count=sum(tool.deployment_supported for tool in value.tools),
        )
        for value in catalog.items
    ]
    return catalog_reads.index_page(
        records,
        revision=digest(json_text(catalog)),
        scope=f"{workspace_id}/toolsets",
        query=query,
        limit=limit,
        cursor=cursor,
    )
