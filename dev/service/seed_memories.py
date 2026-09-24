"""File memories: a team handbook agents read, preferences they edit, and an agent that mounts both by default.

The conversation edits a preference through the memory tools, then a person edits the handbook, so both
memories show history attributed to a run's tool call and to a person, with diffs to restore from.
"""

from __future__ import annotations

from dataclasses import dataclass

from dev.service.api import Api, Json
from dev.service.seed_conversations import Talk

HANDBOOK = {
    "README.md": "---\ndescription: How the handbook is organized\n---\n# Team handbook\n\n"
    "- `process/` holds how the team works.\n- `glossary.md` lists product terms.\n",
    "process/releases.md": "---\ndescription: How releases ship\n---\n"
    "- Releases ship on Tuesdays.\n- Docs review every release note.\n",
    "process/reviews.md": "Every change needs one approving review before it merges.\n",
    "glossary.md": "---\ndescription: Product terms in English and Chinese\n---\n"
    "- Orbit: the fictional product (轨道).\n- Workspace: where a team's agents work.\n",
}
PREFERENCES = {
    "language.md": "---\ndescription: Reply language and tone\n---\n- Reply in English.\n- Keep answers short.\n",
    "formatting.md": "Use bullet lists for steps.\n",
}
EDIT = '[memory-edit] prefs language.md "Reply in English." -> "Reply in Chinese."'


@dataclass(frozen=True, slots=True)
class Memories:
    handbook: Json
    preferences: Json
    agent: Json  # mounts the preferences for writing and the handbook for reading by default


def seed_memories(api: Api, ws: str, model: Json) -> Memories:
    handbook = _memory(
        api,
        ws,
        {
            "key": "team-handbook",
            "name": "Team handbook",
            "description": "How the fictional product team works.",
            "labels": {"team": "docs"},
            "guide": "Keep one file per process under process/. Agents read this memory; people maintain it.",
            "always_load": ["README.md"],
        },
        HANDBOOK,
    )
    preferences = _memory(
        api,
        ws,
        {"key": "user-prefs", "name": "User preferences", "description": "How the team likes answers."},
        PREFERENCES,
    )
    mounts = [
        {"name": "prefs", "memory_id": preferences["id"], "access": "write"},
        {"name": "handbook", "memory_id": handbook["id"], "access": "read"},
    ]
    agent = api.post(
        f"{ws}/agents",
        {
            "key": "team-assistant",
            "name": "Team assistant",
            "description": "Remembers the team's preferences and reads its handbook.",
            "labels": {"team": "docs"},
            "config": {
                "model": {"model_id": model["id"]},
                "instructions": "You are the team assistant of a fictional product team. Keep answers short.",
                "memory_mounts": mounts,
            },
        },
    )
    return Memories(handbook, preferences, agent)


def remembered(talk: Talk, memories: Memories) -> dict[str, str]:
    """A conversation whose first run edits a preference and whose second run sees a person's handbook edit."""
    api, ws = talk.api, talk.ws
    edited = talk.start(memories.agent, f"{EDIT}\nFrom now on, reply in Chinese.")
    files = f"{ws}/memories/{memories.handbook['id']}/files"
    releases = api.get(f"{files}/process/releases.md")
    content = releases["content"].replace("Tuesdays", "Wednesdays")
    api.put(f"{files}/process/releases.md", releases, {"content": content})
    informed = talk.reply(edited, memories.agent, "What changed in the handbook?")
    return {
        "memory_handbook": memories.handbook["id"],
        "memory_preferences": memories.preferences["id"],
        "memory_edit": edited["id"],
        "memory_changes": informed["id"],
    }


def _memory(api: Api, ws: str, body: Json, files: dict[str, str]) -> Json:
    memory = api.post(f"{ws}/memories", {"type": "postgres", **body})
    for path, content in files.items():
        api.post(f"{ws}/memories/{memory['id']}/files", {"path": path, "content": content})
    return memory
