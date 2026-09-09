"""Resource lifecycle changes applied after runs retain their original revisions."""

from .seed_client import Client


async def resource_history(client: Client, catalog: dict) -> dict:
    agents = catalog["agents"]
    scenarios = {}
    path = f"/api/v1/agents/{agents[0]}"
    agent = await client.request("GET", path)
    original = await client.request("GET", f"/api/v1/agent-revisions/{agent['current_revision_id']}")
    updated = await client.request(
        "POST",
        path + "/revisions",
        expected=201,
        json={
            "expected_version": agent["version"],
            "config": {
                **original["config"],
                "instructions": "Revision 2: produce a concise fictional review with explicit tradeoffs.",
            },
        },
    )
    restored = await client.request(
        "POST",
        path + f"/revisions/{original['id']}/restore",
        expected=201,
        json={"expected_version": updated["agent"]["version"]},
    )
    revisions = await client.collection(path + "/revisions")
    if len(revisions) != 3 or restored["revision"]["source_revision_id"] != original["id"]:
        raise RuntimeError("Agent revision and restore scenario did not preserve history")
    scenarios["agent_revision_restore"] = agent["id"]
    duplicate = await client.request(
        "POST",
        path + "/duplicate",
        expected=201,
        json={
            "expected_version": restored["agent"]["version"],
            "name": "Release reviewer · independent duplicate",
            "description": "Duplicated after real run history; no Runs yet.",
        },
    )
    scenarios["agent_duplicate_without_runs"] = duplicate["id"]
    for action, identifier in (("disable", agents[-2]), ("archive", agents[-1])):
        path = f"/api/v1/agents/{identifier}"
        if action == "archive":
            await client.request("POST", path + "/disable", headers=await client.etag(path))
        resource = await client.request("POST", path + "/" + action, headers=await client.etag(path))
        if (action == "disable" and resource["enabled"]) or (action == "archive" and resource["archived_at"] is None):
            raise RuntimeError("Agent lifecycle did not reach the requested state")
        scenarios[f"agent_{action}"] = identifier
    skill = catalog["skills"][-1]  # Never bound by an Agent; delete through normal referential checks.
    path = f"/api/v1/skills/{skill}"
    await client.request("DELETE", path, expected=204, headers=await client.etag(path))
    await client.request("GET", path, expected=404)
    scenarios["skill_deleted"] = skill
    return scenarios
