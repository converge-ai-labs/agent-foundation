"""What lets seeded runs execute on this machine: the scripted model and `local` environments.

The scripted model (`dev/fixtures/model.py`) is served twice: as a plain model, and as a media model that
declares every understanding capability, so it takes attachments natively and is the workspace's
media-understanding default. `local` environments are directories under the checkout's `var/dev/environments`, a
development-only type that the checkout's settings enable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dev.service.api import Api, Json

MODEL_KEY = "local-scripted"
# The media kinds of the workspace's media-understanding defaults.
MEDIA = ("image", "audio", "video")
# Fictional prices, so usage shows cost; the scripted model reports 20 input tokens per request.
PRICING = {
    "provider": "local",
    "model": MODEL_KEY,
    "rules": [
        {
            "rule_id": "standard",
            "prices": [{"price_key": "input_mtok", "price": "3"}, {"price_key": "output_mtok", "price": "15"}],
        }
    ],
    "source": "manual",
    "source_revision": "2026-09-24",
}


@dataclass(frozen=True, slots=True)
class Local:
    model: Json
    media: Json
    template: Json
    # The managed instance that seeded conversations share as their `workspace` mount.
    environment: Json


def seed_local(api: Api, model_url: str, environments: Path) -> Local:
    provider = api.post(
        "/api/v1/model-providers",
        {
            "type": "openai",
            "name": "Local scripted model",
            "config": {"base_url": model_url},
            "credential": {"api_key": "local-scripted"},
        },
    )
    model = _model(api, provider, MODEL_KEY, "Local scripted model", ())
    # A distinct upstream name: one run may resolve both models, and a shared name would be ambiguous.
    media = _model(api, provider, "local-scripted-media", "Local scripted media model", (*MEDIA, "document"))
    defaults = api.get("/api/v1/media-understanding-defaults")
    api.put("/api/v1/media-understanding-defaults", defaults, dict.fromkeys(MEDIA, media["key"]))
    local = api.post(
        "/api/v1/environment-providers", {"type": "local", "name": "Local directories (development)", "config": {}}
    )
    recipe = {"root": {"path": str(environments)}, "shell_profiles": [{"profile_id": "sh", "executable": "/bin/sh"}]}
    template = api.post(
        "/api/v1/environment-templates",
        {
            "name": "Local workspace",
            "description": "A directory on this machine; development only.",
            "provider_id": local["id"],
            "labels": {"runtime": "local"},
            "config": {"recipe": recipe},
        },
    )
    environment = api.post("/api/v1/environments", {"template_id": template["id"], "name": "Release review workspace"})
    return Local(model, media, template, environment)


def _model(api: Api, provider: Json, key: str, name: str, understands: tuple[str, ...]) -> Json:
    characteristics = {"capabilities": [f"{kind}_understanding" for kind in understands]}
    config = {"model_name": key, "model_api": "openai.chat_completions", "characteristics": characteristics}
    body = {"provider_id": provider["id"], "key": key, "name": name, "config": config}
    return api.post("/api/v1/models", {**body, "pricing": PRICING})


def stopped_environment(api: Api, template: Json) -> dict[str, str]:
    """A second instance, created and then stopped, so the lifecycle shows more than `ready`."""
    reserved = api.post("/api/v1/environments", {"template_id": template["id"], "name": "Sprint archive"})
    path = f"/api/v1/environments/{reserved['id']}"
    api.post(f"{path}/stop", current=api.until(path, lambda environment: environment["status"] == "ready"))
    api.until(path, lambda environment: environment["status"] == "stopped")
    return {"environment_stopped": reserved["id"]}
