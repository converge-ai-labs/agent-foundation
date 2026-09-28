"""Read the seeded state back through the API; each check names what the Console should show."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from decimal import Decimal
from urllib.parse import urlencode

from dev.service.api import Api, Json
from dev.service.seed import Seeded
from dev.service.seed_assets import examples
from dev.service.seed_conversations import NATIVE, PLACED
from dev.service.seed_identity import MEMBERS
from dev.service.seed_memories import FACTS, HANDBOOK, RECORDED
from dev.service.seed_providers import KINDS
from dev.service.seed_resources import SKILLS

type Check = tuple[str, bool]
# The Console lists 30 agents per page.
AGENT_PAGE = 30
DELIVERIES = ("native_files", "inline_files", "placed_files")
# The heading of the text that delivers an inline or placed attachment.
ATTACHMENT = re.compile(r'Attachment "(.+?)" \(')


def verify(api: Api, seeded: Seeded) -> list[Check]:
    api.workspace_id = seeded.workspace
    org, ws = f"/api/v1/organizations/{seeded.organization}", f"/api/v1/workspaces/{seeded.workspace}"
    index = seeded.index
    return [
        *_identity(api, org, ws, index),
        *_providers(api),
        *_resources(api, index),
        *_execution(api, index),
        *_memories(api, index),
        *_usage(api, index),
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
        and not _agents_in(api, index["empty_workspace"]),
    )
    yield (
        "The organization, workspace and administrator have images",
        all(api.get(path)["image_url"] for path in (org, ws, "/api/v1/users/me")),
    )


def _agents_in(api: Api, workspace_id: str) -> list[Json]:
    """The agents of another workspace the session can read."""
    current, api.workspace_id = api.workspace_id, workspace_id
    try:
        return api.items("/api/v1/agents")
    finally:
        api.workspace_id = current


def _providers(api: Api) -> Iterator[Check]:
    for kind in KINDS:
        offered = {item["type"] for item in api.items(f"/api/v1/provider-types/{kind}")}
        accounts = {item["type"] for item in api.items(f"/api/v1/{kind}-providers")}
        yield f"Every {kind} provider type has an account", offered <= accounts
    models = {model["key"]: model for model in api.items("/api/v1/models")}
    fictional = [model for key, model in models.items() if key.startswith("fictional-")]
    offered_models = len(api.items("/api/v1/provider-types/model"))
    yield (
        "Every fictional model provider serves a disabled model, mostly with a catalog reference",
        len(fictional) == offered_models
        and not any(model["enabled"] for model in fictional)
        and sum(model["catalog_ref"] is not None for model in fictional) > offered_models // 2,
    )
    media = api.get("/api/v1/media-understanding-defaults")
    yield (
        "The scripted models are enabled, and the media model is every media default",
        models["local-scripted"]["enabled"]
        and models["local-scripted-media"]["enabled"]
        and {media["image"], media["audio"], media["video"]} == {"local-scripted-media"},
    )


def _resources(api: Api, index: dict[str, str]) -> Iterator[Check]:
    skills = {skill["name"]: skill for skill in api.items("/api/v1/skills")}
    draft = skills["accessibility-review"]
    yield (
        "Skills: every example, one archived, one whose newest revision is not the default",
        {skill.name for skill in SKILLS} <= skills.keys()
        and skills["legacy-style-guide"]["archived_at"] is not None
        and api.items(f"/api/v1/skills/{draft['id']}/revisions")[0]["id"] != draft["default_revision_id"],
    )
    agents = {agent["id"]: agent for agent in api.items("/api/v1/agents")}
    yield (
        "Agents: more than a Console page, an archived one, a duplicate and Agent Composer",
        len(agents) > AGENT_PAGE
        and any(agent["archived_at"] is not None for agent in agents.values())
        and index["writer_duplicate"] in agents
        and any(agent["source"] == "builtin" for agent in agents.values()),
    )
    writer = agents[index["writer"]]
    revisions = api.items(f"/api/v1/agents/{writer['id']}/revisions")
    yield (
        "The writer has three revisions, and the newest is not the default",
        len(revisions) == 3 and revisions[0]["id"] != writer["default_revision_id"] == index["writer_default_revision"],
    )
    templates = api.items("/api/v1/environment-templates")
    yield (
        "A template per environment account, and a disabled one",
        len(templates) == len(api.items("/api/v1/environment-providers")) + 1
        and [template["enabled"] for template in templates].count(False) == 1,
    )
    statuses = {environment["name"]: environment["status"] for environment in api.items("/api/v1/environments")}
    yield (
        "Environments: the shared review workspace is ready, and one is stopped",
        (statuses.get("Release review workspace"), statuses.get("Sprint archive")) == ("ready", "stopped"),
    )
    connections = {(item["type"], item["status"], item["enabled"]) for item in api.items("/api/v1/connections")}
    yield (
        "Connections: ready, pending and disabled MCP servers, and a pending connector account",
        {("mcp", "ready", True), ("mcp", "pending", True), ("mcp", "ready", False), ("composio", "pending", True)}
        <= connections,
    )
    subscription = api.items("/api/v1/subscriptions")[0]
    deliveries = api.items(f"/api/v1/subscriptions/{subscription['id']}/deliveries")
    yield (
        "The webhook subscription delivered lifecycle events",
        any(item["status"] == "delivered" for item in deliveries),
    )
    stored = {asset["name"]: asset for asset in api.items("/api/v1/assets")}
    yield (
        "Every example file is stored with its bytes",
        all(
            stored[example.name]["digest"] == hashlib.sha256(example.data).hexdigest()
            and api.content(f"/api/v1/assets/{stored[example.name]['id']}/content") == example.data
            for example in examples()
        ),
    )
    yield (
        "A published asset names the run that published it",
        any((asset["source"] or {}).get("run_id") == index["published_asset"] for asset in stored.values()),
    )


def _execution(api: Api, index: dict[str, str]) -> Iterator[Check]:
    threads = {thread["id"]: thread for thread in api.items("/api/v1/threads")}
    runs = {run["id"]: run for thread in threads for run in api.items(f"/api/v1/threads/{thread}/runs")}

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
        == ["approval", "call", "call"],
    )
    yield (
        "An approval and a client tool result resumed their runs",
        [run(name)["trigger"] for name in ("approval_approved", "client_tool_completed")] == ["resume", "resume"],
    )
    steered = run("steered")
    entries = api.items(f"/api/v1/threads/{steered['thread_id']}/inbox")
    yield (
        "A running run incorporated a steering message",
        [entry["assigned_run_id"] for entry in entries] == [steered["id"], steered["id"]],
    )
    queued = api.items(f"/api/v1/threads/{run('interrupted')['thread_id']}/inbox", status="pending")
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
    results = api.items(f"/api/v1/threads/{delegated['thread_id']}/inbox")
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
        for item in api.get(f"/api/v1/runs/{index['skill_and_files']}/items")["items"]
        if item["kind"] == "tool_call"
    ]
    yield (
        "The shared workspace shows a skill read, a file write and a command",
        [name for name, _ in calls] == ["view", "write", "shell_exec"] and "/.a13n/skills/" in calls[0][1],
    )
    native, inline, placed = (api.get(f"/api/v1/runs/{index[name]}/items")["items"] for name in DELIVERIES)
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
    yield "Usage is recorded and priced per model", any(model["cost"] for model in api.get("/api/v1/usage")["models"])


def _memories(api: Api, index: dict[str, str]) -> Iterator[Check]:
    handbook = api.get(f"/api/v1/memories/{index['memory_handbook']}")
    preferences = f"/api/v1/memories/{index['memory_preferences']}"
    yield (
        "Memories: a handbook with an always-loaded README, and preferences",
        (handbook["always_load"], handbook["file_count"]) == (["README.md"], len(HANDBOOK))
        and api.get(preferences)["file_count"] > 0,
    )
    edited = api.get(f"/api/v1/runs/{index['memory_edit']}")
    mounts = api.items(f"/api/v1/threads/{edited['thread_id']}/memories")
    yield (
        "The agent's default memory mounts joined its conversation",
        [(mount["name"], mount["access"]) for mount in mounts]
        == [("facts", "write"), ("handbook", "read"), ("prefs", "write")]
        and [mount["name"] for mount in edited["memory_mounts"]] == ["facts", "handbook", "prefs"],
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
    revisions = api.items(f"/api/v1/memories/{index['memory_handbook']}/revisions")
    releases = [(item["op"], item["run_id"]) for item in revisions if item["path"] == "process/releases.md"]
    yield "A person's edit shows in the handbook's history", releases == [("update", None), ("create", None)]
    facts = f"/api/v1/memories/{index['memory_facts']}"
    texts = {record["text"] for record in api.items(f"{facts}/records")}
    yield (
        "A record memory in the fake mem0 holds its records and the one a run recorded",
        api.get(facts)["kind"] == "record" and texts == {*FACTS, RECORDED},
    )


def _attached(items: list[Json], marker: str) -> set[str]:
    """The attachments whose model-facing text in a run's items contains `marker`."""
    texts = (item["content"]["text"] for item in items if item["kind"] == "text_message")
    return {found[1] for text in texts if marker in text and (found := ATTACHMENT.match(text))}


def _usage(api: Api, index: dict[str, str]) -> Iterator[Check]:
    window = {"start": index["usage_history_start"], "end": index["usage_history_end"]}
    overview_path = "/api/v1/usage/overview?" + urlencode(window)
    overview = api.get(overview_path)
    agents = api.items("/api/v1/usage/agents", **window)
    models = api.items("/api/v1/usage/models", **window)
    total = overview["usage"]
    for name, values in (
        ("days", [day["usage"] for day in overview["daily"]]),
        ("agents", [row["usage"] for row in agents]),
        ("models", [row["usage"] for row in models]),
    ):
        yield (
            f"Usage: {name} reconcile with overview tokens, requests and known USD cost",
            (
                all(
                    sum(row[field] for row in values) == total[field]
                    for field in ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "unpriced_requests")
                )
                and sum(Decimal(row["cost"]) for row in values if row["cost"] is not None) == Decimal(total["cost"])
            ),
        )
    yield (
        "Usage: 30 daily buckets contain gaps and a visible peak",
        (
            len(overview["daily"]) == 30
            and any(day["usage"]["requests"] == 0 for day in overview["daily"])
            and max(day["usage"]["requests"] for day in overview["daily"])
            > 2 * overview["daily"][0]["usage"]["requests"]
        ),
    )
    yield (
        "Usage: cached tokens are an input subset and the rate is weighted",
        (
            0 < total["cache_read_tokens"] < total["input_tokens"]
            and abs(total["cache_hit_rate"] - total["cache_read_tokens"] / total["input_tokens"]) < 1e-12
        ),
    )
    yield (
        "Usage: unknown model prices stay unknown",
        any(
            row["model"] == "usage-unpriced" and row["usage"]["cost"] is None and row["usage"]["unpriced_requests"] > 0
            for row in models
        ),
    )
    yield (
        "Usage: multiple model requests count each Run once",
        (
            sum(row["runs"]["runs"] for row in agents) == overview["runs"]["runs"] < total["requests"]
            and overview["runs"]["average_duration_seconds"] > 0
        ),
    )
    workspace = api.workspace_id
    try:
        api.workspace_id = index["empty_workspace"]
        yield (
            "Usage: the empty Workspace has no consumption",
            api.get(overview_path)["usage"]["requests"] == 0,
        )
    finally:
        api.workspace_id = workspace
