"""Environment template configurations, provider capabilities, and unused instances."""

from pathlib import Path

from a13n_service.settings import Settings

from .seed_client import Client


async def local_provider(client: Client, base: str) -> dict:
    providers = await client.collection(base + "/environment-providers")
    for provider in providers:
        if (
            provider["type"] == "direct-local"
            and provider["enabled"]
            and provider["configuration_source"] == "deployment"
        ):
            return provider
    raise RuntimeError("Local seeding requires environments.local_providers.direct-local")


async def local_workspace(client: Client, base: str, provider_id: str, root: Path, name: str) -> dict:
    root.mkdir(parents=True, mode=0o700)
    template = await client.request(
        "POST",
        base + "/environment-templates",
        expected=201,
        json={
            "name": name,
            "provider_id": provider_id,
            "preparation": "on_run",
            "retention": {"idle": {"stop_after": None, "delete_after": None}},
            "configuration": {
                "root": {"path": str(root)},
                "shell_profiles": [{"profile_id": "default", "executable": "/bin/sh"}],
            },
        },
    )
    environment = await client.request(
        "POST", base + "/environments", expected=201, json={"template_id": template["id"]}
    )
    directory = root / "environments" / environment["id"]
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    return {"environment_id": environment["id"], "template_id": template["id"], "root": str(directory)}


async def environments(client: Client, base: str, catalog: dict, settings: Settings) -> dict:
    provider = await local_provider(client, base)
    scenarios = {}
    for name, shell in (
        ("Reference files", False),
        ("Writable draft files", True),
        ("Archived template_config", True),
    ):
        root = settings.filesystem.root / name.lower().replace(" ", "-")
        template_config = {
            "provider_id": provider["id"],
            "preparation": "on_run",
            "retention": {"idle": {"stop_after": None, "delete_after": None}},
            "configuration": {
                "root": {"path": str(root)},
                "shell_profiles": [{"profile_id": "default", "executable": "/bin/sh"}] if shell else [],
            },
        }
        template = await client.request(
            "POST",
            base + "/environment-templates",
            expected=201,
            json={"name": name, "description": "Public local fixture for Environment views", **template_config},
        )
        scenarios["template_" + name.lower().replace(" ", "_")] = template["id"]
        if name == "Archived template_config":
            path = f"/api/v1/environment-templates/{template['id']}"
            await client.request("PATCH", path, headers=await client.etag(path), json={"archived": True})
            continue
        instance = await client.request(
            "POST", base + "/environments", expected=201, json={"template_id": template["id"]}
        )
        directory = root / "environments" / instance["id"]
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        (directory / "README.md").write_text("# Fictional local workspace\nNo customer content.\n")
        scenarios["environment_" + ("draft" if shell else "reference")] = instance["id"]
        if name == "Reference files":
            await client.request(
                "POST",
                f"/api/v1/environment-templates/{template['id']}/revisions",
                expected=201,
                json={**template_config, "expected_version": template["version"], "preparation": "on_use"},
            )
    disabled = await client.request(
        "POST",
        base + "/environment-providers",
        expected=201,
        json={"type": "e2b", "name": "Disabled E2B provider", "configuration": {}},
    )
    path = f"/api/v1/environment-providers/{disabled['id']}"
    await client.request("PATCH", path, headers=await client.etag(path), json={"enabled": False})
    scenarios["environment_provider_disabled"] = disabled["id"]
    return scenarios
