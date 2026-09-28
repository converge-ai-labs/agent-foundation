"""Agents: the cast the seeded conversations use, their lifecycle variants, and enough of a catalogue to page."""

from __future__ import annotations

from dataclasses import dataclass

from dev.service.api import Api, Json
from dev.service.seed_assets import png
from dev.service.seed_connections import FAILING_TOOL, LOOKUP_TOOL
from dev.service.seed_local import Local

REVIEW_TOOL = {
    "name": "local_review",
    "description": "Ask the user to review a draft; they return a decision and a short reason.",
    "parameters_json_schema": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]},
}
REPORT = {
    "name": "review_report",
    "schema": {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": ["ready", "blocked"]},
            "checks": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
        "required": ["outcome", "checks", "summary"],
        "additionalProperties": False,
    },
}
# Idle agents that fill the list past one Console page (30), so paging has something to show.
CATALOG = (
    "Support triage",
    "Code review",
    "Meeting summary",
    "数据分析助手",
    "Translation desk",
    "Onboarding guide",
    "Weekly report",
    "Design feedback",
    "Knowledge search",
    "Planning assistant",
    "Changelog curator",
    "Customer interview notes",
    "Roadmap digest",
    "Bug bash coordinator",
    "Localization check",
    "Accessibility audit",
    "Sprint retrospective",
    "Pricing page review",
    "Status page drafts",
    "Survey analysis",
    "A deliberately long agent name for checking truncation and responsive navigation",
)


@dataclass(frozen=True, slots=True)
class Cast:
    """The agents seeded conversations run."""

    writer: Json  # the release-notes skill and asset publication, in the shared workspace
    reviewer: Json  # a client tool and user questions
    lookup: Json  # MCP tools, one of which asks for approval
    structured: Json  # structured output
    assistant: Json  # plain answers; also the coordinator's sub-agent
    viewer: Json  # the media model, which takes images, audio and PDF natively
    coordinator: Json  # async delegation
    analyst: Json  # a default template, so each thread reserves its own environment


def seed_agents(api: Api, local: Local, connection: Json, search: Json, skills: dict[str, Json]) -> Cast:
    def agent(name: str, description: str, labels: dict[str, str], model: Json = local.model, **config: object) -> Json:
        instructions = f"You are the {name.lower()} of a fictional product team. Keep answers short."
        body = {"model": model["key"], "instructions": instructions, **config}
        return api.post("/api/v1/agents", {"name": name, "description": description, "labels": labels, "config": body})

    assistant = agent("Documentation assistant", "Answers questions about the docs.", {"team": "docs"})
    cast = Cast(
        writer=agent(
            "Release writer",
            "Drafts release notes in the shared review workspace.",
            {"team": "docs", "stage": "production"},
            skills=[{"skill_id": skills["release-notes"]["id"]}],
            toolsets={"assets": {"enabled": True}},
        ),
        reviewer=agent(
            "Release reviewer",
            "Asks the user to review drafts before publishing.",
            {"team": "docs"},
            client_tools=[REVIEW_TOOL],
            user_questions=True,
        ),
        lookup=agent(
            "Review lookup",
            "Looks up review records over MCP; lookups need approval.",
            {"team": "qa"},
            connection_tools=[
                {
                    "connection_id": connection["id"],
                    "tools": [LOOKUP_TOOL, FAILING_TOOL],
                    "permission": "ask",
                    "permissions": {FAILING_TOOL: "allow"},
                }
            ],
        ),
        structured=agent("Structured review", "Returns a typed review report.", {"team": "qa"}, output_spec=REPORT),
        assistant=assistant,
        viewer=agent(
            "Media reviewer",
            "Reviews screenshots, recordings and PDFs with a model that understands them.",
            {"team": "design"},
            model=local.media,
        ),
        coordinator=agent(
            "Release coordinator",
            "Delegates reviews to the documentation assistant.",
            {"team": "docs"},
            subagent_mode="async",
            subagents={"reviewer": {"agent_id": assistant["id"], "description": "Reviews one release note."}},
        ),
        analyst=agent(
            "Workspace analyst",
            "Works in an environment of its own, reserved from the local template.",
            {"team": "platform"},
            default_environment_template_id=local.template["id"],
            skills=[{"skill_id": skills["accessibility-review"]["id"]}],
        ),
    )
    api.put(f"/api/v1/agents/{cast.writer['id']}/avatar", cast.writer, png())
    web_tools = {"search": {"enabled": True, "config": {"provider_id": search["id"]}}, "fetch": {"enabled": True}}
    agent(
        "Research notes",
        "Searches the web; its fictional search account never answers.",
        {"team": "research"},
        toolsets={"web": {"enabled": True, "tools": web_tools}},
    )
    legacy = agent("Legacy triage", "Replaced by the release reviewer.", {"team": "support"})
    api.post(f"/api/v1/agents/{legacy['id']}/archive", current=legacy)
    api.post("/api/v1/agent-composer")
    for name in CATALOG:
        agent(name, "An idle fictional agent.", {"team": "catalog"})
    return cast
