"""Resource lifecycle changes applied after runs retain their original revisions."""

from .seed_client import Client


async def resource_history(client: Client, base: str, catalog: dict) -> dict:
    agents = catalog["agents"]
    scenarios = {}
    path = f"{base}/agents/{agents[0]}"
    agent = await client.request("GET", path)
    original = await client.request("GET", f"/api/v1/agent-revisions/{agent['default_revision_id']}")
    await client.request(
        "POST",
        path + "/revisions",
        expected=201,
        headers=await client.etag(path),
        json={
            "config": {
                **original["config"],
                "instructions": "Revision 2: produce a concise fictional review with explicit tradeoffs.",
            },
        },
    )
    selected = await client.request(
        "POST",
        path + f"/revisions/{original['id']}/default",
        expected=200,
        headers=await client.etag(path),
        json={},
    )
    edited = await client.request(
        "POST",
        path + "/revisions",
        expected=201,
        headers=await client.etag(path),
        json={
            "config": {**original["config"], "instructions": "Revision 3: edited from the older default."},
            "change_summary": "Edit the selected historical configuration",
        },
    )
    revisions = await client.collection(path + "/revisions")
    if len(revisions) != 3 or selected["revision"]["id"] != original["id"] or edited["revision"]["version"] != 3:
        raise RuntimeError("Agent revision and default-selection scenario did not preserve history")
    scenarios["agent_revision_default"] = agent["id"]
    duplicate = await client.request(
        "POST",
        path + "/duplicate",
        expected=201,
        headers=await client.etag(path),
        json={
            "name": "Release reviewer · independent duplicate",
            "description": "Duplicated after real run history; no Runs yet.",
        },
    )
    scenarios["agent_duplicate_without_runs"] = duplicate["id"]
    for action, identifier in (("disable", agents[-2]), ("archive", agents[-1])):
        path = f"{base}/agents/{identifier}"
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
