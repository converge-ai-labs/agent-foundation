"""Read the seeded state back through the API; each check names what the Console should show."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator

from dev.service.api import Api, Json
from dev.service.seed import Seeded
from dev.service.seed_assets import examples
from dev.service.seed_conversations import NATIVE, PLACED
from dev.service.seed_identity import MEMBERS
from dev.service.seed_memories import HANDBOOK
from dev.service.seed_providers import KINDS
from dev.service.seed_resources import SKILLS

type Check = tuple[str, bool]
# The Console lists 30 agents per page.
AGENT_PAGE = 30
DELIVERIES = ("native_files", "inline_files", "placed_files")
# The heading of the text that delivers an inline or placed attachment.
ATTACHMENT = re.compile(r'Attachment "(.+?)" \(')


def verify(api: Api, seeded: Seeded) -> list[Check]:
    org, ws, index = seeded.organization, seeded.workspace, seeded.index
    return [
        *_identity(api, org, ws, index),
        *_providers(api, org, ws),
        *_resources(api, org, ws, index),
        *_execution(api, ws, index),
        *_memories(api, ws, index),
    ]


def _identity(api: Api, org: str, ws: str, index: dict[str, str]) -> Iterator[Check]:
    roles = {grant["principal"]["email"]: grant["role"] for grant in api.items(f"{ws}/grants")}
    yield (
        "Members hold builder, runner and viewer grants",
        all(roles.get(email) == role for role, (email, _) in MEMBERS.items()),
    )
    invitations = {item["id"]: item for item in api.items(f"{ws}/invitations")}
    pending = invitations[index["invitation_pending"]]
    yield "One invitation is pending", (pending["accepted_at"], pending["revoked_at"]) == (None, None)
    keys = {key["id"]: key for key in api.items("/api/v1/users/me/keys")}
    yield (
        "API keys: active, expiring and revoked",
        keys[index["api_key_active"]]["revoked_at"] is None
        and keys[index["api_key_expiring"]]["expires_at"] is not None
        and keys[index["api_key_revoked"]]["revoked_at"] is not None,
    )
    accounts = {account["id"]: account["status"] for account in api.items(f"{ws}/service-accounts")}
    yield (
        "Service accounts: one active, one disabled",
        [accounts[index["service_account_active"]], accounts[index["service_account_disabled"]]]
        == ["active", "disabled"],
    )
    workspaces = {workspace["id"]: workspace for workspace in api.items("/api/v1/workspaces")}
    yield (
        "Workspaces: this one, an empty one and an archived one",
        len(workspaces) == 3
        and workspaces[index["archived_workspace"]]["archived_at"] is not None
        and not api.items(f"/api/v1/workspaces/{index['empty_workspace']}/agents"),
    )
    yield (
        "The organization, workspace and administrator have images",
        all(api.get(path)["image_url"] for path in (org, ws, "/api/v1/users/me")),
    )


def _providers(api: Api, org: str, ws: str) -> Iterator[Check]:
    for kind in KINDS:
        offered = {item["type"] for item in api.items(f"/api/v1/provider-types/{kind}")}
        accounts = {item["type"] for item in api.items(f"{org}/{kind}-providers")}
        yield f"Every {kind} provider type has an account", offered <= accounts
    models = {model["key"]: model for model in api.items(f"{org}/models")}
    fictional = [model for key, model in models.items() if key.startswith("fictional-")]
    offered_models = len(api.items("/api/v1/provider-types/model"))
    yield (
        "Every fictional model provider serves a disabled model, mostly with a catalog reference",
        len(fictional) == offered_models
        and not any(model["enabled"] for model in fictional)
        and sum(model["catalog_ref"] is not None for model in fictional) > offered_models // 2,
    )
    media = api.get(f"{ws}/media-understanding-defaults")
    yield (
        "The scripted models are enabled, and the media model is every media default",
        models["local-scripted"]["enabled"]
        and models["local-scripted-media"]["enabled"]
        and {media["image"], media["audio"], media["video"]} == {models["local-scripted-media"]["id"]},
    )


def _resources(api: Api, org: str, ws: str, index: dict[str, str]) -> Iterator[Check]:
    skills = {skill["key"]: skill for skill in api.items(f"{ws}/skills")}
    draft = skills["accessibility-review"]
    yield (
        "Skills: every example, one archived, one whose newest revision is not the default",
        {skill.key for skill in SKILLS} <= skills.keys()
        and skills["legacy-style-guide"]["archived_at"] is not None
        and api.items(f"{ws}/skills/{draft['id']}/revisions")[0]["id"] != draft["default_revision_id"],
    )
    agents = {agent["key"]: agent for agent in api.items(f"{ws}/agents")}
    yield (
        "Agents: more than a Console page, an archived one, a duplicate and the configuration assistant",
        len(agents) > AGENT_PAGE
        and agents["legacy-triage"]["archived_at"] is not None
        and {"release-writer-copy", "configuration-assistant"} <= agents.keys(),
    )
    writer = agents["release-writer"]
    revisions = api.items(f"{ws}/agents/{writer['id']}/revisions")
    yield (
        "The writer has three revisions, and the newest is not the default",
        len(revisions) == 3 and revisions[0]["id"] != writer["default_revision_id"] == index["writer_default_revision"],
    )
    yield (
        "Secrets of workspace and personal scope",
        {secret["scope"] for secret in api.items(f"{ws}/secrets")} == {"workspace", "user"},
    )
    templates = api.items(f"{ws}/environment-templates")
    yield (
        "A template per environment account, and a disabled one",
        len(templates) == len(api.items(f"{org}/environment-providers")) + 1
        and [template["enabled"] for template in templates].count(False) == 1,
    )
    statuses = {environment["name"]: environment["status"] for environment in api.items(f"{ws}/environments")}
    yield (
        "Environments: the shared review workspace is ready, and one is stopped",
        (statuses.get("Release review workspace"), statuses.get("Sprint archive")) == ("ready", "stopped"),
    )
    connections = {(item["type"], item["status"], item["enabled"]) for item in api.items(f"{ws}/connections")}
    yield (
        "Connections: ready, pending and disabled MCP servers, and a pending connector account",
        {("mcp", "ready", True), ("mcp", "pending", True), ("mcp", "ready", False), ("composio", "pending", True)}
        <= connections,
    )
    subscription = api.items(f"{ws}/subscriptions")[0]
    deliveries = api.items(f"{ws}/subscriptions/{subscription['id']}/deliveries")
    yield (
        "The webhook subscription delivered lifecycle events",
        any(item["status"] == "delivered" for item in deliveries),
    )
    stored = {asset["name"]: asset for asset in api.items(f"{ws}/assets")}
    yield (
        "Every example file is stored with its bytes",
        all(
            stored[example.name]["digest"] == hashlib.sha256(example.data).hexdigest()
            and api.content(f"{ws}/assets/{stored[example.name]['id']}/content") == example.data
            for example in examples()
        ),
    )
    yield (
        "A published asset names the run that published it",
        any((asset["source"] or {}).get("run_id") == index["published_asset"] for asset in stored.values()),
    )


def _execution(api: Api, ws: str, index: dict[str, str]) -> Iterator[Check]:
    threads = {thread["id"]: thread for thread in api.items(f"{ws}/threads")}
    runs = {run["id"]: run for thread in threads for run in api.items(f"{ws}/threads/{thread}/runs")}

    def run(name: str) -> Json:
        return runs[index[name]]

    def thread_runs(thread_id: str) -> list[Json]:
        return [item for item in runs.values() if item["thread_id"] == thread_id]

    yield (
        "Runs completed, waiting, failed and cancelled, and none still active",
        {item["status"] for item in runs.values()} == {"completed", "waiting", "failed", "cancelled"},
    )
    yield (
        "Waits for an approval, a client tool and a user's answer",
        [run(name)["wait_reason"] for name in ("approval_waiting", "client_tool_waiting", "question_waiting")]
        == ["approval", "client_tool", "user_input"],
    )
    yield (
        "An approval and a client tool result resumed their runs",
        [run(name)["trigger"] for name in ("approval_approved", "client_tool_completed")] == ["resume", "resume"],
    )
    steered = run("steered")
    entries = api.items(f"{ws}/threads/{steered['thread_id']}/inbox")
    yield (
        "A running run incorporated a steering message",
        [entry["assigned_run_id"] for entry in entries] == [steered["id"], steered["id"]],
    )
    queued = api.items(f"{ws}/threads/{run('interrupted')['thread_id']}/inbox", status="pending")
    yield (
        "An interrupted run keeps its thread's queued message",
        run("interrupted")["status"] == "cancelled" and len(queued) == 1,
    )
    yield (
        "Failed runs: a model error and malformed structured output",
        [run("failed")["status"], run("malformed_output")["status"]] == ["failed", "failed"],
    )
    yield "Structured output is recorded", isinstance(run("structured_output")["output"], dict)
    delegated = run("delegated")
    # The result steers the parent's run while it waits, or starts a successor run once it has ended.
    results = api.items(f"{ws}/threads/{delegated['thread_id']}/inbox")
    yield (
        "A sub-agent ran in a child thread, and its result reached the parent",
        any(thread["origin_run_id"] == delegated["id"] for thread in threads.values())
        and ("child_result", "consumed") in {(entry["kind"], entry["status"]) for entry in results},
    )
    yield (
        "A fork branches the multi-turn conversation",
        threads[index["fork"]]["origin_thread_id"] == index["conversation"]
        and len(thread_runs(index["conversation"])) == 5,
    )
    calls = [
        (item["content"]["toolCallName"], item["content"]["arguments"])
        for item in api.get(f"{ws}/runs/{index['skill_and_files']}/items")["items"]
        if item["kind"] == "tool_call"
    ]
    yield (
        "The shared workspace shows a skill read, a file write and a command",
        [name for name, _ in calls] == ["view", "write", "shell_exec"] and "/.a13n/skills/" in calls[0][1],
    )
    native, inline, placed = (api.get(f"{ws}/runs/{index[name]}/items")["items"] for name in DELIVERIES)
    media_types = {
        item["content"]["value"]["event"]["content"]["media_type"]
        for item in native
        if item["content"].get("name") == "a13n.input.media"
    }
    yield (
        "Attachments reach the model natively, as inline text, and placed in the environment",
        media_types == {example.content_type for example in examples() if example.name in NATIVE}
        and _attached(inline, " bytes):") == {example.name for example in examples()} - {*NATIVE, *PLACED}
        and _attached(placed, "placed in the environment at: /workspace/.a13n/attachments/") == set(PLACED),
    )
    writer_runs = thread_runs(index["conversation"])
    yield (
        "Earlier runs keep the writer's first revision, and one run is pinned to it",
        {item["agent_revision_id"] for item in writer_runs}
        == {index["writer_first_revision"], index["writer_default_revision"]}
        and run("run_pinned_to_first_revision")["revision_selection"] == "pinned",
    )
    yield (
        "Another member started a conversation",
        run("member_conversation")["principal_id"] == index["member_runner"],
    )
    yield "Usage is recorded and priced per model", any(model["cost"] for model in api.get(f"{ws}/usage")["models"])


def _memories(api: Api, ws: str, index: dict[str, str]) -> Iterator[Check]:
    handbook = api.get(f"{ws}/memories/{index['memory_handbook']}")
    preferences = f"{ws}/memories/{index['memory_preferences']}"
    yield (
        "Memories: a handbook with an always-loaded README, and preferences",
        (handbook["always_load"], handbook["file_count"]) == (["README.md"], len(HANDBOOK))
        and api.get(preferences)["file_count"] > 0,
    )
    edited = api.get(f"{ws}/runs/{index['memory_edit']}")
    mounts = api.items(f"{ws}/threads/{edited['thread_id']}/memories")
    yield (
        "The agent's default memory mounts joined its conversation",
        [(mount["name"], mount["access"]) for mount in mounts] == [("handbook", "read"), ("prefs", "write")]
        and [mount["name"] for mount in edited["memory_mounts"]] == ["handbook", "prefs"],
    )
    [change] = api.items(f"{preferences}/revisions", run_id=edited["id"])
    detail = api.get(f"{preferences}/revisions/{change['seq']}")
    yield (
        "A run's tool call edited a preference, with history and a diff",
        change["op"] == "update"
        and change["tool_call_id"] is not None
        and "Reply in Chinese." in api.get(f"{preferences}/files/language.md")["content"]
        and any("+- Reply in Chinese." in hunk for hunk in detail["hunks"]),
    )
    revisions = api.items(f"{ws}/memories/{index['memory_handbook']}/revisions")
    releases = [(item["op"], item["run_id"]) for item in revisions if item["path"] == "process/releases.md"]
    yield "A person's edit shows in the handbook's history", releases == [("update", None), ("create", None)]


def _attached(items: list[Json], marker: str) -> set[str]:
    """The attachments whose model-facing text in a run's items contains `marker`."""
    texts = (item["content"]["text"] for item in items if item["kind"] == "text_message")
    return {found[1] for text in texts if marker in text and (found := ATTACHMENT.match(text))}
