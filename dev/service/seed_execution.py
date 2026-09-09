"""Structured output and parent/child execution scenarios."""

import hashlib
from pathlib import Path

from .seed_client import Client
from .seed_journeys import run
from .seed_resources import agent_config


async def execution(client: Client, base: str, catalog: dict) -> dict:
    config = {
        **agent_config("Structured local review"),
        "retries": {"tools": 0, "output": 1},
        "output_spec": {
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
        },
    }
    structured = await client.request(
        "POST", base + "/agents", expected=201, json={"name": "Structured review output", "config": config}
    )
    scenarios = {"agent_structured": structured["agent"]["id"]}
    result = await run(
        client, base, scenarios["agent_structured"], "[structured] Return the fictional review as typed data."
    )
    if result["output"]["outcome"] != "ready":
        raise RuntimeError("Structured-output scenario did not retain its validated object")
    scenarios["structured_output"] = result["id"]
    failed = await run(
        client,
        base,
        scenarios["agent_structured"],
        "[structured-invalid] Return an intentionally malformed local output.",
        expected="failed",
    )
    scenarios["malformed_output_failure"] = failed["id"]
    config.pop("output_spec")
    config["subagent_mode"] = "async"
    config["subagents"] = {
        "reviewer": {"agent_id": catalog["agents"][3], "description": "Local fictional review specialist"}
    }
    parent = await client.request(
        "POST", base + "/agents", expected=201, json={"name": "Delegated release review", "config": config}
    )
    scenarios["agent_parent"] = parent["agent"]["id"]
    delegated = await run(
        client, base, scenarios["agent_parent"], "[delegate] Ask the reviewer to check the fictional release."
    )
    threads = await client.collection(f"/api/v1/sessions/{delegated['session_id']}/threads")
    if len(threads) < 2:
        raise RuntimeError("Delegation scenario did not retain its child Thread")
    scenarios["delegated_parent_run"] = delegated["id"]
    scenarios["delegated_child_thread"] = next(item["id"] for item in threads if item["id"] != delegated["thread_id"])
    path = Path(catalog["publication_path"])
    content = path.read_bytes()
    publisher = await client.request(
        "POST",
        base + "/agents",
        expected=201,
        json={
            "name": "Publish a retained review artifact",
            "config": agent_config("Local artifact publisher", asset_publication={"enabled": True}),
        },
    )
    published = await run(
        client,
        base,
        publisher["agent"]["id"],
        f"[publish] /workspace/{path.name}\nPublish the prepared fictional review fixture.",
        environment_id=catalog["environment_id"],
    )
    assets = await client.collection(base + "/assets")
    asset = next((item for item in assets if item["source"].get("run_id") == published["id"]), None)
    if (
        asset is None
        or asset["source"]["kind"] != "run_output"
        or asset["content_sha256"] != hashlib.sha256(content).hexdigest()
    ):
        raise RuntimeError("Published artifact did not retain its bytes and Run provenance")
    catalog["assets"].append(asset["id"])
    catalog["asset_checks"].append(
        {
            "id": asset["id"],
            "media_type": "text/markdown",
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    )
    scenarios["agent_publisher"] = publisher["agent"]["id"]
    scenarios["artifact_publication_run"] = published["id"]
    scenarios["run_output_asset"] = asset["id"]
    return scenarios
