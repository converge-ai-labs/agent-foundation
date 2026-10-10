"""Findings preview through real Runs, trace reads and managed analyst tools; never placeholder evidence."""

from __future__ import annotations

from dev.fixtures.findings import EXAMPLES
from dev.service.api import Api, ApiError, Json
from dev.service.seed_connections import FAILING_TOOL, LOOKUP_TOOL
from dev.service.seed_conversations import Talk
from dev.service.seed_local import Local


def seed_findings(api: Api, local: Local, connection: Json) -> dict[str, str]:
    agent = api.post(
        "/api/v1/agents",
        {
            "name": "Release operations assistant for international shipping and publishing review",
            "description": "Fictional Findings preview with ambiguous intent, a failed lookup and a read-only lookup.",
            "labels": {"team": "operations", "purpose": "findings-preview"},
            "config": {
                "model": local.model["key"],
                "instructions": "A fictional preview assistant. Use the configured review lookup when requested. Shipping quotes are simulated. Do not publish or modify any real resource.",
                "toolsets": {key: {"enabled": False} for key in ("files", "shell", "web", "memory")},
                "connection_tools": [
                    {"connection_id": connection["id"], "tools": [LOOKUP_TOOL, FAILING_TOOL], "permission": "allow"}
                ],
            },
        },
    )
    index = {"findings_agent": agent["id"], "findings_revision": agent["default_revision_id"]}
    talk = Talk(api)
    runs = {key: talk.start(agent, example["prompt"]) for key, example in EXAMPLES.items()}
    index |= {f"findings_run_{key}": run["id"] for key, run in runs.items()}
    backend = api.get("/api/v1/trace-backend")["type"]
    index["findings_trace_backend"] = backend or "none"
    if backend is None:
        # The normal untraced preview stays usable; no invented trace IDs or analysis provenance.
        return index
    traces: dict[str, Json] = {}
    for key, run in runs.items():
        page = api.until(f"/api/v1/traces?run_id={run['id']}", lambda page: bool(page["items"]), timeout=60)
        traces[key] = page["items"][0]
        index[f"findings_trace_{key}"] = traces[key]["trace_id"]
    api.post("/api/v1/finding-agent")
    analysis = api.post("/api/v1/finding-analyses", {"agent_id": agent["id"], "max_traces": 3}, idempotent=True)
    finished = api.sealed_run(analysis["run_id"])
    if finished["status"] != "completed":
        raise ApiError(f"Findings preview analysis {analysis['id']} did not complete")
    generated = api.items("/api/v1/findings", agent_id=agent["id"])
    by_category = {finding["category"]: finding for finding in generated}
    if set(by_category) != {example["category"] for example in EXAMPLES.values()}:
        raise ApiError("Scripted Findings analysis did not produce all three preview diagnoses")
    for key, example in EXAMPLES.items():
        index[f"finding_{key}"] = by_category[example["category"]]["id"]
    confirmed = by_category[EXAMPLES["endpoint"]["category"]]
    api.patch(
        f"/api/v1/findings/{confirmed['id']}",
        confirmed,
        {
            "assessment": "confirmed",
            "assessment_note": "Confirmed against the failed lookup and pinned configuration. The assistant reported the missing prerequisite honestly. Restore the intended review operation before recommending publishing; do not relax the prerequisite or fabricate a review.",
        },
    )
    corrected = by_category[EXAMPLES["readonly"]["category"]]
    api.patch(
        f"/api/v1/findings/{corrected['id']}",
        corrected,
        {
            "assessment": "false_positive",
            "closed": True,
            "assessment_note": "The diagnosis confused a read-only lookup with a write. The tool result retrieved an existing review; no draft mutation or publishing action occurred. An approval gate would add friction without addressing a demonstrated side effect. Closed as an incorrect diagnosis, not as proof that a newer revision repaired anything.",
        },
    )
    index["findings_analysis"] = analysis["id"]
    index["findings_analysis_run"] = analysis["run_id"]
    return index
