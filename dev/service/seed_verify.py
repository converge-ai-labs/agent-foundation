"""Verify retained coverage before a reset can be reported as successful."""

from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

import anyio

from .seed_client import Client


async def _parallel[Item, Result](items: Sequence[Item], action: Callable[[Item], Awaitable[Result]]) -> list[Result]:
    limiter = anyio.CapacityLimiter(8)
    results: dict[int, Result] = {}

    async def collect(index: int, item: Item) -> None:
        async with limiter:
            results[index] = await action(item)

    async with anyio.create_task_group() as tasks:
        for index, item in enumerate(items):
            tasks.start_soon(collect, index, item)
    return [results[index] for index in range(len(items))]


async def verify(client: Client, manifest: dict) -> dict:
    base = f"/api/v1/workspaces/{manifest['workspace_id']}"
    counts = {}
    sessions = []
    for resource in (
        "agents",
        "skills",
        "assets",
        "sessions",
        "models",
        "model-providers",
        "environments",
        "environment-templates",
        "environment-providers",
        "mcp-connections",
        "connector-providers",
        "connector-connections",
        "application-accounts",
        "service-accounts",
    ):
        params = {"limit": 7}
        if resource == "agents":
            params["include_archived"] = True
        values = await client.collection(base + "/" + resource, params=params)
        if len({item["id"] for item in values}) != len(values):
            raise RuntimeError(f"Pagination duplicated {resource} entries")
        counts[resource] = len(values)
        if resource in {"agents", "skills", "assets"} and len(values) <= 50:
            raise RuntimeError(f"The {resource} fixture no longer exercises the default page boundary")
        if resource == "sessions":
            sessions = values
    if counts["sessions"] != manifest["session_count"]:
        raise RuntimeError("Session count changed during seed verification")

    async def session_threads(session: dict) -> list[dict]:
        threads = await client.collection(f"/api/v1/sessions/{session['id']}/threads")
        if any(thread["session_id"] != session["id"] for thread in threads):
            raise RuntimeError("Thread belongs to the wrong Session")
        return threads

    all_threads = [thread for group in await _parallel(sessions, session_threads) for thread in group]
    all_runs = await client.collection(base + "/runs", params={"limit": 200})
    threads_by_id = {thread["id"]: thread for thread in all_threads}
    if len(threads_by_id) != len(all_threads) or len({run["id"] for run in all_runs}) != len(all_runs):
        raise RuntimeError("Pagination duplicated Thread or Run entries")
    if any(
        run["thread_id"] not in threads_by_id or run["session_id"] != threads_by_id[run["thread_id"]]["session_id"]
        for run in all_runs
    ):
        raise RuntimeError("Run belongs to an unexpected Thread or Session")
    statuses = Counter(item["status"] for item in all_runs)
    if not {"completed", "failed", "waiting", "cancelled"}.issubset(statuses):
        raise RuntimeError("Required persisted Run outcomes are missing")
    if any(item["status"] in {"accepted", "running"} or not item["sealed_at"] for item in all_runs):
        raise RuntimeError("Seed left an active or unsealed Run")
    long_runs = [item for item in all_runs if item["thread_id"] == manifest["long_thread_id"]]
    if len(long_runs) < 13:
        raise RuntimeError("The long conversation lost its real continuations")

    async def retained_items(retained: dict) -> list[dict] | None:
        path = f"/api/v1/runs/{retained['id']}/items"
        for attempt in range(100):
            response = await client.http.get(path, params={"limit": 100})
            if response.status_code == 200:
                page = response.json()
                items = page["items"]
                if page.get("next_cursor") is not None:
                    items.extend(await client.collection(path, params={"limit": 100, "cursor": page["next_cursor"]}))
                return items
            if response.status_code != 409:
                raise RuntimeError(f"Retained transcript verification failed: HTTP {response.status_code}")
            if retained["status"] not in {"completed", "waiting"}:
                return None
            if attempt == 99:
                raise RuntimeError(
                    f"Retained transcript missing for {retained['id']} ({retained['status']}); "
                    f"scenarios: {[name for group in manifest['scenarios'].values() for name, value in group.items() if value == retained['id']]}"
                )
            await anyio.sleep(0.1)
        raise RuntimeError("Retained transcript verification exhausted its retry bound")

    item_kinds = Counter()
    unavailable = []
    for retained, items in zip(all_runs, await _parallel(all_runs, retained_items), strict=True):
        if items is None:
            unavailable.append(retained["id"])
        else:
            item_kinds.update(item["kind"] for item in items)
    by_id = {item["id"]: item for item in all_runs}
    scenes = manifest["scenarios"]["conversations"]
    expected = {
        "failed_run": "failed",
        "failed_retry": "failed",
        "waiting_for_client": "waiting",
        "feedback_completed": "completed",
        "interrupted_run": "cancelled",
        "interrupted_with_queue": "cancelled",
        "interrupted_retry_completed": "completed",
        "structured_output": "completed",
        "malformed_output_failure": "failed",
    }
    for name, status in expected.items():
        if by_id[scenes[name]]["status"] != status:
            raise RuntimeError(f"Retained scenario {name} has an unexpected outcome")
    if by_id[scenes["failed_retry"]]["retry_of_run_id"] != scenes["failed_run"]:
        raise RuntimeError("Retry lineage is missing")
    if by_id[scenes["feedback_completed"]]["parent_run_id"] != scenes["feedback_source"]:
        raise RuntimeError("Feedback lineage is missing")
    first_agent = manifest["agent_ids"][0]
    agent = await client.request("GET", f"{base}/agents/{first_agent}")
    if not any(
        item["agent_id"] == first_agent and item["agent_revision_id"] != agent["current_revision_id"]
        for item in all_runs
    ):
        raise RuntimeError("Historical runs no longer preserve their original Agent revisions")
    expired = await client.request("GET", f"/api/v1/api-keys/{manifest['scenarios']['identity']['api_key_expired']}")
    if datetime.fromisoformat(expired["expires_at"]) >= datetime.now(UTC):
        raise RuntimeError("The naturally expired API key is not expired yet")
    with client.scope(manifest["empty_workspace_id"]):
        empty = f"/api/v1/workspaces/{manifest['empty_workspace_id']}"
        for resource in ("agents", "skills", "assets", "sessions", "environments"):
            if await client.collection(empty + "/" + resource):
                raise RuntimeError(f"Empty workspace unexpectedly contains {resource}")
    audit = await client.collection(base + "/security-audit-events")
    if not audit:
        raise RuntimeError("Normal management operations did not produce audit history")
    return {
        "resource_counts": counts,
        "retained_item_kinds": dict(sorted(item_kinds.items())),
        "unavailable_transcript_runs": unavailable,
        "run_statuses": dict(sorted(statuses.items())),
        "thread_origins": dict(sorted(Counter(item["origin_kind"] for item in all_threads).items())),
        "run_count": len(all_runs),
        "thread_count": len(all_threads),
        "long_conversation_runs": len(long_runs),
        "security_audit_events": len(audit),
        "verified_at": datetime.now(UTC).isoformat(),
    }


def report(manifest: dict) -> str:
    coverage = manifest["coverage"]
    lines = [
        "# Local seed coverage",
        "",
        "All content and identities are fictional. Verification completed through the Service API.",
        "",
        f"Bulk Sessions use {len(manifest['bulk_environments'])} isolated execution slots; paths are recorded in seed.json.",
        "",
        "## Retained resources",
        "",
        "| Resource | Count |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {name} | {count} |" for name, count in coverage["resource_counts"].items())
    lines.extend(["", "## Run outcomes", "", "| Outcome | Count |", "| --- | ---: |"])
    lines.extend(f"| {name} | {count} |" for name, count in coverage["run_statuses"].items())
    lines.extend(["", "## Retained transcript content", "", "| Item kind | Count |", "| --- | ---: |"])
    lines.extend(f"| {name} | {count} |" for name, count in coverage["retained_item_kinds"].items())
    if coverage["unavailable_transcript_runs"]:
        lines.extend(["", "Failed or cancelled Runs without retained Items (unavailable-transcript UI):", ""])
        lines.extend(f"- `{identifier}`" for identifier in coverage["unavailable_transcript_runs"])
    lines.extend(["", "## Scenario index", "", "| Area | Scenario | Resource |", "| --- | --- | --- |"])
    for area, scenarios in manifest["scenarios"].items():
        for name, identifier in scenarios.items():
            if isinstance(identifier, dict):
                identifier = identifier["user_id"]
            lines.append(f"| {area} | {name.replace('_', ' ')} | `{identifier}` |")
    lines.extend(
        [
            "",
            "Waiting client-tool requests and queued inputs are intentionally retained. No Run remains accepted or running.",
            "Dates and identifiers are produced normally; no terminal status or timestamp is patched into storage.",
            "",
        ]
    )
    return "\n".join(lines)
