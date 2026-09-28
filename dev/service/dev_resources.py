"""Machine-private resources (real credentials) applied to a seeded checkout through the public API.

`~/.a13n/dev-resources.toml` is shared by every checkout on the machine; see `dev-resources.example.toml`.
Everything is applied to the default workspace: providers and templates identified by name, models by key. Each
checkout records a digest per applied provider, so unchanged values are not resent.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tomllib
from collections import Counter
from pathlib import Path
from typing import Literal

from a13n_service.infra.ids import Key
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from dev.service.api import Api, Json
from dev.service.checkout import ADMIN_EMAIL, ADMIN_PASSWORD, Checkout, write_private

DEFAULT_FILE = Path.home() / ".a13n/dev-resources.toml"
MAX_BYTES = 65536

type Values = dict[str, JsonValue]


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelEntry(_Entry):
    key: Key
    name: str
    upstream_model: str
    # The provider type's first model API when omitted.
    model_api: str | None = None
    enabled: bool = True


class ProviderEntry(_Entry):
    type: str
    name: str
    configuration: Values = Field(default_factory=dict)
    # Omitted for credential-free types; a blank value skips the provider and what depends on it.
    credential: dict[str, str] | None = Field(default=None, repr=False)


class ModelProviderEntry(ProviderEntry):
    models: tuple[ModelEntry, ...] = ()


class ConnectorProviderEntry(_Entry):
    type: str
    name: str
    configuration: Values = Field(default_factory=dict)
    credentials: dict[str, str] = Field(repr=False)


class TemplateEntry(_Entry):
    name: str
    provider: str
    # The template's recipe, validated by the provider type's environment schema.
    configuration: Values
    stop_after: int | None = None
    delete_after: int | None = None


class Resources(_Entry):
    version: Literal[1]
    model_providers: tuple[ModelProviderEntry, ...] = ()
    web_providers: tuple[ProviderEntry, ...] = ()
    environment_providers: tuple[ProviderEntry, ...] = ()
    environment_templates: tuple[TemplateEntry, ...] = ()
    connector_providers: tuple[ConnectorProviderEntry, ...] = ()

    @model_validator(mode="after")
    def templates_name_providers(self) -> Resources:
        names = {entry.name for entry in self.environment_providers}
        if any(template.provider not in names for template in self.environment_templates):
            raise ValueError("every environment template names an environment provider of this file")
        return self


def load(path: Path = DEFAULT_FILE) -> Resources | None:
    """Read the file only when it is a regular file private to this user; errors never echo its contents."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(f"{path} must be a regular file, not a symlink") from None
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError(f"{path} must be a regular file with mode 0600")
        if metadata.st_uid != os.getuid():
            raise ValueError(f"{path} must be owned by this user")
        content = stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError(f"{path} exceeds {MAX_BYTES} bytes")
    try:
        return Resources.model_validate(tomllib.loads(content.decode()))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"{path} is not valid TOML: {type(error).__name__}") from None
    except ValidationError as error:
        # Pydantic keeps rejected input out of `loc` and `msg`, so credentials are never echoed.
        problems = "; ".join(f"{'.'.join(map(str, item['loc'])) or 'file'}: {item['msg']}" for item in error.errors())
        raise ValueError(f"{path} is invalid: {problems}") from None


def filled(credential: dict[str, str] | None) -> bool:
    return credential is None or all(value.strip() for value in credential.values())


def _provider_body(entry: ProviderEntry | ConnectorProviderEntry) -> Json:
    credential = entry.credentials if isinstance(entry, ConnectorProviderEntry) else entry.credential
    return {"type": entry.type, "name": entry.name, "config": entry.configuration, "credential": credential}


class Applied:
    """Digests of provider bodies already applied, per checkout; the file holds no credential."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.digests: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}

    @staticmethod
    def digest(body: Json) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def save(self) -> None:
        write_private(self.path, json.dumps(self.digests, indent=2, sort_keys=True) + "\n")


def apply_to(checkout: Checkout, path: Path = DEFAULT_FILE) -> str | None:
    """Apply the private file to a seeded checkout's running Service; a summary, or None when nothing applies."""
    if not checkout.seed_report.exists() or (resources := load(path)) is None:
        return None
    with Api(checkout.service_url) as api:
        api.login(ADMIN_EMAIL, ADMIN_PASSWORD)
        counts = apply(api, resources, Applied(checkout.state / "dev-resources.json"))
    return ", ".join(f"{count} {kind}" for kind, count in counts.items() if count) or "no entry has a filled credential"


def apply(api: Api, resources: Resources, applied: Applied) -> Counter[str]:
    """Create or update every entry whose credential is filled; returns how many of each kind are in place."""
    # The seeded state holds several workspaces; resources go to the one bootstrap created.
    api.workspace_id = api.first_workspace()["id"]
    counts: Counter[str] = Counter()
    model_apis = {item["type"]: item["model_apis"] for item in api.items("/api/v1/provider-types/model")}
    models = {model["key"]: model for model in api.items("/api/v1/models")}
    for entry in resources.model_providers:
        if filled(entry.credential):
            provider = _apply_provider(api, "model-providers", _provider_body(entry), applied)
            for model in entry.models:
                _apply_model(api, provider, model, model_apis[entry.type][0], models.get(model.key))
            counts.update({"model providers": 1, "models": len(entry.models)})
    for entry in resources.web_providers:
        if filled(entry.credential):
            _apply_provider(api, "web-providers", _provider_body(entry), applied)
            counts["web providers"] += 1
    environment_providers = {
        entry.name: _apply_provider(api, "environment-providers", _provider_body(entry), applied)
        for entry in resources.environment_providers
        if filled(entry.credential)
    }
    counts["environment providers"] += len(environment_providers)
    templates = {template["name"]: template for template in api.items("/api/v1/environment-templates")}
    for entry in resources.environment_templates:
        if provider := environment_providers.get(entry.provider):
            _apply_template(api, provider, entry, templates.get(entry.name))
            counts["environment templates"] += 1
    for entry in resources.connector_providers:
        if filled(entry.credentials):
            _apply_provider(api, "connector-providers", _provider_body(entry), applied)
            counts["connector providers"] += 1
    return counts


def _apply_model(api: Api, provider: Json, entry: ModelEntry, default_api: str, current: Json | None) -> None:
    config = {"model_name": entry.upstream_model, "model_api": entry.model_api or default_api}
    if current is None:
        body = {"provider_id": provider["id"], "key": entry.key, "name": entry.name}
        current = api.post("/api/v1/models", {**body, "config": config})
    elif current["provider_id"] != provider["id"]:
        raise ValueError(f"Model key {entry.key} belongs to another provider")
    applied = (current["name"], current["enabled"], {key: current["config"][key] for key in config})
    if applied != (entry.name, entry.enabled, config):
        update = {"name": entry.name, "config": config, "enabled": entry.enabled}
        api.patch(f"/api/v1/models/{entry.key}", current, update)


def _apply_template(api: Api, provider: Json, entry: TemplateEntry, current: Json | None) -> None:
    lifetimes = {"stop_after_seconds": entry.stop_after, "delete_after_seconds": entry.delete_after}
    config = {"recipe": entry.configuration, **{key: value for key, value in lifetimes.items() if value is not None}}
    desired = {"name": entry.name, "provider_id": provider["id"], "config": config}
    if current is None:
        api.post("/api/v1/environment-templates", desired)
    elif current["provider_id"] != provider["id"] or any(current["config"][key] != config[key] for key in config):
        api.patch(f"/api/v1/environment-templates/{current['id']}", current, desired)


def _apply_provider(api: Api, kind: str, body: Json, applied: Applied) -> Json:
    """The workspace's provider named in `body`, created or updated to match it."""
    current = next((item for item in api.items(f"/api/v1/{kind}") if item["name"] == body["name"]), None)
    marker, digest = f"{kind}:{body['name']}", Applied.digest(body)
    if current is None:
        current = api.post(f"/api/v1/{kind}", body)
    elif current["type"] != body["type"]:
        raise ValueError(f"The existing {kind} {body['name']} has type {current['type']}, not {body['type']}")
    elif applied.digests.get(marker) != digest:
        update = {"config": body["config"], "credential": body["credential"], "enabled": True}
        current = api.patch(f"/api/v1/{kind}/{current['id']}", current, update)
    applied.digests[marker] = digest
    applied.save()
    return current
