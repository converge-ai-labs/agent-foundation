"""Verify retained coverage before a reset can be reported as successful."""

from collections import Counter
from datetime import UTC, datetime

import anyio

from .seed_client import Client


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

    all_runs = []
    all_threads = []
    for session in sessions:
        threads = await client.collection(f"/api/v1/sessions/{session['id']}/threads")
        all_threads.extend(threads)
        for thread in threads:
            all_runs.extend(await client.collection(f"/api/v1/threads/{thread['id']}/runs"))
    statuses = Counter(item["status"] for item in all_runs)
    if not {"completed", "failed", "waiting", "cancelled"}.issubset(statuses):
        raise RuntimeError("Required persisted Run outcomes are missing")
    if any(item["status"] in {"accepted", "running"} or not item["sealed_at"] for item in all_runs):
        raise RuntimeError("Seed left an active or unsealed Run")
    long_runs = [item for item in all_runs if item["thread_id"] == manifest["long_thread_id"]]
    if len(long_runs) < 13:
        raise RuntimeError("The long conversation lost its real continuations")
    item_kinds = Counter()
    unavailable = []
    for retained in all_runs:
        path = f"/api/v1/runs/{retained['id']}/items"
        for attempt in range(100):
            response = await client.http.get(path, params={"limit": 100})
            if response.status_code == 200:
                page = response.json()
                item_kinds.update(item["kind"] for item in page["items"])
                break
            if response.status_code != 409:
                raise RuntimeError(f"Retained transcript verification failed: HTTP {response.status_code}")
            if retained["status"] not in {"completed", "waiting"}:
                unavailable.append(retained["id"])
                break
            if attempt == 99:
                raise RuntimeError(
                    f"Retained transcript missing for {retained['id']} ({retained['status']}); "
                    f"scenarios: {[name for group in manifest['scenarios'].values() for name, value in group.items() if value == retained['id']]}"
                )
            await anyio.sleep(0.1)
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
    agent = await client.request("GET", f"/api/v1/agents/{first_agent}")
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
