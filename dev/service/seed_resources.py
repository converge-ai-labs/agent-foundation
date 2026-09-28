"""Skills with revisions, environment templates and a webhook subscription."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from dev.service.api import Api, Json
from dev.service.seed_assets import upload


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    labels: dict[str, str]
    body: str
    references: bool = False


SKILLS = (
    Skill(
        "release-notes",
        "Turn a list of changes into short, factual release notes.",
        {"team": "docs"},
        "Group changes under Added, Changed and Fixed, one line each, without marketing language.",
    ),
    Skill(
        "accessibility-review",
        "Review a screen for keyboard, focus and contrast problems.",
        {"team": "design"},
        "Check keyboard navigation first, then focus order, then contrast. Report each finding with its screen.",
        references=True,
    ),
    Skill(
        "incident-summary",
        "Summarize a fictional incident timeline for stakeholders.",
        {"team": "support"},
        "State impact, timeline, cause and follow-ups, in that order.",
    ),
    Skill(
        "translation-glossary",
        "Keep product terms consistent between English and Chinese (中文术语表).",
        {"team": "docs", "locale": "zh-CN"},
        "Use the glossary term for every product noun; never translate the product name.",
    ),
    Skill(
        "legacy-style-guide",
        "A retired style guide, archived.",
        {},
        "Prefer title case in headings.",
    ),
)
ARCHIVED_SKILL = "legacy-style-guide"


def seed_skills(api: Api) -> dict[str, Json]:
    """Every skill, by its SKILL.md name. `accessibility-review` gains a newer revision that is not its default."""
    skills = {
        skill.name: api.post("/api/v1/skills", {"source": publish(api, skill, revision=1), "labels": skill.labels})
        for skill in SKILLS
    }
    draft = next(skill for skill in SKILLS if skill.name == "accessibility-review")
    api.post(
        f"/api/v1/skills/{skills[draft.name]['id']}/revisions",
        {"source": publish(api, draft, revision=2), "make_default": False, "note": "Draft: adds motion checks"},
        current=skills[draft.name],
    )
    api.post(f"/api/v1/skills/{skills[ARCHIVED_SKILL]['id']}/archive", current=skills[ARCHIVED_SKILL])
    return skills


def publish(api: Api, skill: Skill, *, revision: int) -> Json:
    """An upload source holding the skill's package at `revision`."""
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        manifest = f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n"
        archive.writestr(
            f"{skill.name}/SKILL.md", f"{manifest}# {skill.name}\n\n{skill.body}\n\nRevision {revision}.\n"
        )
        if skill.references or revision > 1:
            archive.writestr(f"{skill.name}/references/checklist.md", "# Checklist\n\n- Navigation\n- Empty states\n")
    upload_id = upload(api, f"{skill.name}-{revision}.zip", "application/zip", package.getvalue())
    return {"kind": "upload", "upload_id": upload_id}


def seed_templates(api: Api, environment_providers: dict[str, Json], local_template: Json) -> None:
    """A template per fictional environment account and a disabled template."""
    for provider_type, provider in environment_providers.items():
        body = {"name": f"{provider['name']} sandbox", "provider_id": provider["id"]}
        api.post("/api/v1/environment-templates", {**body, "labels": {"runtime": provider_type}})
    retired = api.post(
        "/api/v1/environment-templates",
        {
            "name": "Retired workspace",
            "description": "Disabled: refuses new environments.",
            "provider_id": local_template["provider_id"],
            "config": local_template["config"],
        },
    )
    api.patch(f"/api/v1/environment-templates/{retired['id']}", retired, {"enabled": False})


def seed_subscription(api: Api, model_url: str) -> Json:
    """A webhook subscription the scripted model's `/webhooks` route accepts."""
    return api.post(
        "/api/v1/subscriptions",
        {
            "name": "Release notifications",
            "url": model_url.removesuffix("/v1") + "/webhooks",
            "kinds": ["run.completed", "run.failed", "run.waiting"],
        },
    )
