"""Local resources and their versions, lifecycle states, and dependency links."""

import hashlib
import io
import zipfile
from pathlib import Path

from a13n_service.settings import Settings

from .seed_assets import asset_examples
from .seed_client import Client
from .seed_environments import local_provider, local_workspace

FIXTURES = Path(__file__).with_name("fixtures")
AGENT_NAMES = (
    "Release reviewer",
    "Documentation assistant",
    "产品交互评审",
    "Research notes",
    "Support triage",
    "Code review",
    "Meeting summary",
    "数据分析助手",
    "Translation desk",
    "Onboarding guide",
    "Weekly report",
    "Incident review",
    "Design feedback",
    "Knowledge search",
    "Planning assistant",
    "A deliberately long Agent name for testing truncation and responsive navigation",
)


def agent_config(name: str, **values) -> dict:
    toolsets = {
        key: {"tools": {tool: {"permission": "allow"} for tool in tools}}
        for key, tools in {
            "files": ("view", "write", "edit", "multi_edit", "mkdir", "move", "copy", "delete", "ls", "glob", "grep"),
            "shell": ("exec", "info", "wait", "input", "signal"),
        }.items()
    }
    toolsets.update(values.pop("toolsets", {}))
    return {
        "toolsets": toolsets,
        "model": {"model_key": "local-scripted", "characteristics": {"context_window": 32768}},
        "instructions": "Review fictional project materials using the scripted local development model.",
        "input_adapter": {"adapter_key": "native", "config": {}},
        "protocol": {"schema_version": "1", "public_name": name, "output_modes": ["text"], "limits": {}},
        **values,
    }


async def resources(client: Client, base: str, model_url: str, settings: Settings):
    scenarios = {}
    provider = await client.request(
        "POST",
        base + "/model-providers",
        expected=201,
        json={
            "type": "openai",
            "name": "Local scripted model (fictional)",
            "credential": "public-local-model-token",
            "configuration": {"base_url": model_url, "auth_mode": "bearer"},
        },
    )
    model = await client.request(
        "POST",
        base + "/models",
        expected=201,
        json={
            "key": "local-scripted",
            "name": "Local UI development model",
            "provider_id": provider["id"],
            "upstream_model": "local-scripted",
            "model_api": "openai.chat_completions",
            "settings": {},
        },
    )
    scenarios["model_ready"] = model["id"]
    for state in ("disabled", "no_auth"):
        alternate = await client.request(
            "POST",
            base + "/model-providers",
            expected=201,
            json={
                "type": "openai",
                "name": f"Local model provider · {state}",
                "credential": "public-local-model-token" if state == "disabled" else None,
                "configuration": {"base_url": model_url, "auth_mode": "bearer" if state == "disabled" else "none"},
                "enabled": state != "disabled",
            },
        )
        scenarios[f"model_provider_{state}"] = alternate["id"]
    disabled = await client.request(
        "POST",
        base + "/models",
        expected=201,
        json={
            "key": "local-disabled",
            "name": "Disabled local model",
            "provider_id": provider["id"],
            "upstream_model": "local-scripted",
            "model_api": "openai.chat_completions",
            "enabled": False,
        },
    )
    scenarios["model_disabled"] = disabled["id"]
    provider = await local_provider(client, base)
    root = settings.filesystem.root / "workspace"
    workspace = await local_workspace(client, base, provider["id"], root, "Local review workspace")
    publication_path = root / "published-review.md"
    publication_path.write_bytes((FIXTURES / "brief.md").read_bytes())
    scenarios["environment_shared"] = workspace["environment_id"]
    scenarios["environment_template"] = workspace["template_id"]
    assets, skills, agents, skill_keys, asset_checks = [], [], [], [], []
    examples = asset_examples()
    for index in range(64):
        filename, media_type, content = examples[index % len(examples)]
        asset = await client.request(
            "POST",
            base + "/assets",
            expected=201,
            params={"filename": f"{index + 1:02d} {filename}", "media_type": media_type},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        assets.append(asset["id"])
        asset_checks.append(
            {
                "id": asset["id"],
                "media_type": media_type,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        response = await client.http.get(f"/api/v1/assets/{asset['id']}/content")
        if response.status_code != 200 or response.content != content:
            raise RuntimeError(
                f"Seed Asset content verification failed: HTTP {response.status_code}, expected {len(content)} bytes, received {len(response.content)}"
            )
        upload = await upload_skill(client, base, index + 1)
        skill = await client.request(
            "POST",
            base + "/skills",
            expected=201,
            json={
                "name": f"Local review {index + 1:02d}" if index % 3 else f"产品评审技能 {index + 1:02d}",
                "source": {"kind": "zip_upload", "upload_id": upload["upload_id"]},
            },
        )
        skills.append(skill["skill"]["id"])
        skill_keys.append(skill["skill"]["key"])
    upload = await upload_skill(client, base, 1, version=2)
    await client.request(
        "POST",
        f"/api/v1/skills/{skills[0]}/revisions",
        expected=201,
        json={"expected_version": 1, "source": {"kind": "zip_upload", "upload_id": upload["upload_id"]}},
    )
    scenarios["skill_multiple_revisions"] = skills[0]
    for index in range(56):
        name = AGENT_NAMES[index % len(AGENT_NAMES)] + (f" · {index + 1}" if index >= len(AGENT_NAMES) else "")
        config = agent_config(name, skills=[{"skill_key": skill_keys[index], "version": 1}] if index % 4 != 3 else [])
        created = await client.request(
            "POST",
            base + "/agents",
            expected=201,
            json={
                "name": name,
                "description": None
                if index % 3 == 0
                else ("Fictional development Agent. " * (30 if index % 3 == 1 else 1)),
                "config": config,
            },
        )
        agents.append(created["agent"]["id"])
    config = {
        **agent_config("Client review · waiting and feedback"),
        "client_tools": [
            {
                "name": "local_review",
                "description": "Ask the local client to supply a fictional review decision.",
                "parameters_json_schema": {
                    "type": "object",
                    "properties": {"prompt": {"type": "string"}},
                    "required": ["prompt"],
                },
            }
        ],
    }
    created = await client.request(
        "POST", base + "/agents", expected=201, json={"name": "Client review · waiting and feedback", "config": config}
    )
    scenarios["agent_client_tool"] = created["agent"]["id"]
    return {
        "assets": assets,
        "skills": skills,
        "agents": agents,
        "environment_id": workspace["environment_id"],
        "environment_provider_id": provider["id"],
        "asset_checks": asset_checks,
        "publication_path": str(publication_path),
        "scenarios": scenarios,
    }


async def upload_skill(client: Client, base: str, number: int, *, version: int = 1) -> dict:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
        target.writestr(
            "SKILL.md",
            (FIXTURES / "SKILL.md").read_text().replace("local-review", f"local-review-{number}")
            + f"\nRevision {version}: fictional review criteria.\n",
        )
        if number % 2 == 0 or version > 1:
            target.writestr(
                "references/checklist.md", "# Review checklist\n\n- Navigation\n- Keyboard focus\n- Empty states\n"
            )
    return await client.request(
        "POST",
        base + "/skill-uploads",
        expected=201,
        content=archive.getvalue(),
        headers={"Content-Type": "application/zip"},
    )
