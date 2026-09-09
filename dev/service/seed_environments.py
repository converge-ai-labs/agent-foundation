"""Environment recipes, access modes, unused instances, and preparation failure."""

from a13n_service.settings import Settings

from .seed_client import Client
from .seed_journeys import run


async def environments(client: Client, base: str, catalog: dict, settings: Settings) -> dict:
    provider = await client.request(
        "POST",
        base + "/environment-providers",
        expected=201,
        json={"type": "a13n.direct-local", "name": "Additional local recipe scenarios", "configuration": {}},
    )
    scenarios = {}
    for name, access in (
        ("Read-only reference files", "read_only"),
        ("Writable draft files", "read_write"),
        ("Archived recipe", "full"),
        ("Missing local directory", "full"),
    ):
        root = settings.filesystem.root / name.lower().replace(" ", "-")
        if name != "Missing local directory":
            root.mkdir(parents=True, mode=0o700)
            (root / "README.md").write_text("# Fictional local workspace\nNo customer content.\n")
        recipe = {
            "provider_id": provider["id"],
            "access": access,
            "preparation": "on_run",
            "retention": {"idle": {"stop_after": None, "delete_after": None}},
            "configuration": {
                "root": {"path": str(root)},
                "shell_profiles": [{"profile_id": "default", "executable": "/bin/sh"}],
            },
        }
        template = await client.request(
            "POST",
            base + "/environment-templates",
            expected=201,
            json={"name": name, "description": "Public local fixture for Environment views", **recipe},
        )
        scenarios["template_" + name.lower().replace(" ", "_")] = template["id"]
        if name == "Archived recipe":
            path = f"/api/v1/environment-templates/{template['id']}"
            await client.request("PATCH", path, headers=await client.etag(path), json={"archived": True})
            continue
        instance = await client.request(
            "POST", base + "/environments", expected=201, json={"template_id": template["id"]}
        )
        scenarios["environment_" + access if name != "Missing local directory" else "environment_missing_directory"] = (
            instance["id"]
        )
        if name == "Read-only reference files":
            await client.request(
                "POST",
                f"/api/v1/environment-templates/{template['id']}/revisions",
                expected=201,
                json={**recipe, "expected_version": template["version"], "preparation": "on_use"},
            )
        elif name == "Missing local directory":
            failed = await run(
                client,
                base,
                catalog["agents"][3],
                "Review a missing fictional local directory.",
                environment_id=instance["id"],
                expected="failed",
            )
            scenarios["environment_preparation_failure"] = failed["id"]
    disabled = await client.request(
        "POST",
        base + "/environment-providers",
        expected=201,
        json={"type": "a13n.direct-local", "name": "Disabled local Environment provider", "configuration": {}},
    )
    path = f"/api/v1/environment-providers/{disabled['id']}"
    await client.request("PATCH", path, headers=await client.etag(path), json={"enabled": False})
    scenarios["environment_provider_disabled"] = disabled["id"]
    return scenarios
