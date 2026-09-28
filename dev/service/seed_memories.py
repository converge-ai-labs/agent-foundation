"""Memories: a team handbook agents read, preferences they edit, facts they record, and an agent that mounts all
three by default.

The conversation edits a preference through the memory tools, then a person edits the handbook, so both file
memories show history attributed to a run's tool call and to a person, with diffs to restore from. The facts are a
record memory in the scripted model process's fake mem0 server (`dev/fixtures/mem0.py`), the only mem0 a seeded
state may call; another conversation recalls them and records one more through the record tools.
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
FACTS = ("The team demo happens every Friday afternoon.", "Release notes are written in English and Chinese.")
RECORDED = "The next team demo moves to Thursday."
RECORD = f'[memory-record] facts "{RECORDED}"'


@dataclass(frozen=True, slots=True)
class Memories:
    handbook: Json
    preferences: Json
    facts: Json
    agent: Json  # mounts the preferences and facts for writing and the handbook for reading by default


def seed_memories(api: Api, model: Json, model_url: str) -> Memories:
    handbook = _memory(
        api,
        {
            "name": "Team handbook",
            "description": "How the fictional product team works.",
            "labels": {"team": "docs"},
            "guide": "Keep one file per process under process/. Agents read this memory; people maintain it.",
            "always_load": ["README.md"],
        },
        HANDBOOK,
    )
    preferences = _memory(api, {"name": "User preferences", "description": "How the team likes answers."}, PREFERENCES)
    facts = _facts(api, model_url)
    mounts = [
        {"name": "prefs", "memory_id": preferences["id"], "access": "write"},
        {"name": "handbook", "memory_id": handbook["id"], "access": "read"},
        {"name": "facts", "memory_id": facts["id"], "access": "write", "recall": True},
    ]
    agent = api.post(
        "/api/v1/agents",
        {
            "name": "Team assistant",
            "description": "Remembers the team's preferences and facts, and reads its handbook.",
            "labels": {"team": "docs"},
            "config": {
                "model": model["key"],
                "instructions": "You are the team assistant of a fictional product team. Keep answers short.",
                "memory_mounts": mounts,
            },
        },
    )
    return Memories(handbook, preferences, facts, agent)


def remembered(talk: Talk, memories: Memories) -> dict[str, str]:
    """A conversation whose first run edits a preference and whose second run sees a person's handbook edit."""
    api = talk.api
    edited = talk.start(memories.agent, f"{EDIT}\nFrom now on, reply in Chinese.")
    files = f"/api/v1/memories/{memories.handbook['id']}/files"
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


def recorded(talk: Talk, memories: Memories) -> dict[str, str]:
    """A conversation that recalls the facts at its start and records one more."""
    run = talk.start(memories.agent, f"{RECORD}\nThe demo moves to Thursday; please remember it.")
    return {"memory_facts": memories.facts["id"], "memory_record": run["id"]}


def _facts(api: Api, model_url: str) -> Json:
    """A record memory in the fake mem0 server the scripted model process serves, with two records."""
    provider = api.post(
        "/api/v1/memory-providers",
        {
            "type": "mem0_oss",
            "name": "Local mem0 (fake)",
            "config": {"base_url": model_url.removesuffix("/v1") + "/mem0"},
        },
    )
    facts = api.post(
        "/api/v1/memories",
        {
            "name": "Team facts",
            "description": "Short facts about the team, recalled by similarity.",
            "type": "mem0_oss",
            "provider_id": provider["id"],
            "guide": "Record one lasting fact about the team per record.",
        },
    )
    for text in FACTS:
        api.post(f"/api/v1/memories/{facts['id']}/records", {"text": text})
    return facts


def _memory(api: Api, body: Json, files: dict[str, str]) -> Json:
    memory = api.post("/api/v1/memories", {"type": "postgres", **body})
    for path, content in files.items():
        api.post(f"/api/v1/memories/{memory['id']}/files", {"path": path, "content": content})
    return memory
