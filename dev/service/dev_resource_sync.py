"""Import machine-private resources through local Service management APIs."""

from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path

import httpx2
from a13n_service.app import create_app
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.settings import Settings

from .dev_resources import DevelopmentResources, _active, _filled, _model_credential, _revealed, load_resources
from .seed_client import Client
from .seed_identity import PASSWORD


class AppliedResources:
    """Remember which private Provider values were successfully applied."""

    def __init__(self, path: Path):
        self.path = path
        self.applied: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}

    @staticmethod
    def _key(kind: str, name: str) -> str:
        return f"{kind}:{name.casefold()}"

    @staticmethod
    def _digest(value: dict) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return sha256(encoded).hexdigest()

    def changed(self, kind: str, name: str, value: dict) -> bool:
        return self.applied.get(self._key(kind, name)) != self._digest(value)

    def record(self, kind: str, name: str, value: dict) -> None:
        if not self.changed(kind, name, value):
            return
        self.applied[self._key(kind, name)] = self._digest(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.path.parent, prefix=".dev-resources-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(self.applied, stream, sort_keys=True)
            stream.write("\n")
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


async def _sync_models(
    client: Client, base: str, resources: DevelopmentResources, applied: AppliedResources
) -> tuple[int, int]:
    workspace_id = base.rsplit("/", 1)[-1]
    providers = {
        item["name"].casefold(): item
        for item in await client.collection(base + "/model-providers")
        if item["workspace_id"] == workspace_id
    }
    models = {item["key"].casefold(): item for item in await client.collection(base + "/models")}
    provider_count = model_count = 0
    registry = built_in_provider_registry()
    for configured in resources.model_providers:
        credential = _model_credential(configured)
        if credential is None and registry.credential_format(configured.type) is not None:
            continue
        desired_provider = {
            "type": configured.type,
            "name": configured.name,
            "configuration": configured.configuration,
            "credential": credential,
        }
        provider = providers.get(configured.name.casefold())
        if provider is None:
            provider = await client.request(
                "POST",
                base + "/model-providers",
                expected=201,
                json=desired_provider,
            )
        else:
            if provider["type"] != configured.type:
                raise ValueError(f"Development Model Provider type mismatch: {configured.name}")
            if applied.changed("model_provider", configured.name, desired_provider):
                path = f"/api/v1/workspaces/{workspace_id}/model-providers/{provider['id']}"
                provider = await client.request(
                    "PATCH",
                    path,
                    headers=await client.etag(path),
                    json={"configuration": configured.configuration, "credential": credential, "enabled": True},
                )
        applied.record("model_provider", configured.name, desired_provider)
        provider_count += 1
        for configured_model in configured.models:
            api = configured_model.model_api or registry.definition(configured.type).default_model_api
            desired = {
                "name": configured_model.name,
                "upstream_model": configured_model.upstream_model,
                "model_api": api,
                "enabled": configured_model.enabled,
            }
            model = models.get(configured_model.key.casefold())
            if model is None:
                model = await client.request(
                    "POST",
                    base + "/models",
                    expected=201,
                    json={**desired, "key": configured_model.key, "provider_id": provider["id"]},
                )
            else:
                if model["workspace_id"] != workspace_id or model["provider_id"] != provider["id"]:
                    raise ValueError(f"Development Model key belongs to another Provider: {configured_model.key}")
                if any(model[field] != value for field, value in desired.items()):
                    path = f"/api/v1/workspaces/{workspace_id}/models/{model['id']}"
                    model = await client.request("PATCH", path, headers=await client.etag(path), json=desired)
            model_count += 1
    return provider_count, model_count


async def _sync_web(client: Client, base: str, resources: DevelopmentResources, applied: AppliedResources) -> int:
    workspace_id = base.rsplit("/", 1)[-1]
    existing = {
        item["name"].casefold(): item
        for item in await client.collection(base + "/web-providers")
        if item["workspace_id"] == workspace_id
    }
    count = 0
    for configured in resources.web_providers:
        if not _filled(configured.credential):
            continue
        credential = _revealed(configured.credential)
        desired = {
            "type": configured.type,
            "name": configured.name,
            "configuration": configured.configuration,
            "credential": credential,
        }
        provider = existing.get(configured.name.casefold())
        if provider is None:
            await client.request(
                "POST",
                base + "/web-providers",
                expected=201,
                json=desired,
            )
        else:
            if provider["type"] != configured.type:
                raise ValueError(f"Development Web Provider type mismatch: {configured.name}")
            if applied.changed("web_provider", configured.name, desired):
                route = base + f"/web-providers/{provider['id']}"
                await client.request(
                    "PATCH",
                    route,
                    headers=await client.etag(route),
                    json={"configuration": configured.configuration, "credential": credential, "enabled": True},
                )
        applied.record("web_provider", configured.name, desired)
        count += 1
    return count


async def _sync_environments(
    client: Client, base: str, resources: DevelopmentResources, applied: AppliedResources
) -> tuple[int, int]:
    workspace_id = base.rsplit("/", 1)[-1]
    providers = {
        item["name"].casefold(): item
        for item in await client.collection(base + "/environment-providers")
        if item["workspace_id"] == workspace_id or item["configuration_source"] == "deployment"
    }
    templates = {
        item["name"].casefold(): item
        for item in await client.collection(base + "/environment-templates")
        if item["workspace_id"] == workspace_id
    }
    provider_count = template_count = 0
    unavailable: set[str] = set()
    for configured in resources.environment_providers:
        key = configured.name.casefold()
        if configured.credential and not _filled(configured.credential):
            unavailable.add(key)
            continue
        credential = _revealed(configured.credential) or None
        desired_provider = {
            "type": configured.type,
            "name": configured.name,
            "configuration": configured.configuration,
            "credential": credential,
        }
        provider = providers.get(key)
        if provider is None:
            provider = await client.request(
                "POST",
                base + "/environment-providers",
                expected=201,
                json=desired_provider,
            )
            providers[key] = provider
        else:
            if provider["type"] != configured.type:
                raise ValueError(f"Development Environment Provider differs from existing resource: {configured.name}")
            if applied.changed("environment_provider", configured.name, desired_provider):
                if provider["configuration"] != configured.configuration:
                    raise ValueError(
                        f"Development Environment Provider configuration cannot be changed: {configured.name}"
                    )
                if credential is not None:
                    route = f"/api/v1/environment-providers/{provider['id']}"
                    await client.request(
                        "PUT", route + "/credential", headers=await client.etag(route), json={"credential": credential}
                    )
        applied.record("environment_provider", configured.name, desired_provider)
        provider_count += 1
    for configured in resources.environment_templates:
        key = configured.provider.casefold()
        if key in unavailable:
            continue
        provider = providers.get(key)
        if provider is None:
            raise ValueError(f"Development Environment Template Provider not found: {configured.provider}")
        desired = {
            "provider_id": provider["id"],
            "configuration": configured.configuration,
            "preparation": configured.preparation,
            "retention": {"idle": {"stop_after": configured.stop_after, "delete_after": configured.delete_after}},
        }
        template = templates.get(configured.name.casefold())
        if template is None:
            await client.request(
                "POST", base + "/environment-templates", expected=201, json={"name": configured.name, **desired}
            )
        else:
            revision = await client.request(
                "GET", f"/api/v1/environment-template-revisions/{template['current_revision_id']}"
            )
            if any(revision[field] != value for field, value in desired.items()):
                await client.request(
                    "POST",
                    f"/api/v1/environment-templates/{template['id']}/revisions",
                    expected=201,
                    json={**desired, "expected_version": template["version"]},
                )
        template_count += 1
    return provider_count, template_count


async def _sync_connectors(
    client: Client, base: str, resources: DevelopmentResources, applied: AppliedResources
) -> int:
    workspace_id = base.rsplit("/", 1)[-1]
    existing = {
        item["name"].casefold(): item
        for item in await client.collection(base + "/connector-providers")
        if item["workspace_id"] == workspace_id
    }
    count = 0
    for configured in resources.connector_providers:
        if not _filled(configured.credentials):
            continue
        credentials = _revealed(configured.credentials)
        desired = {
            "type": configured.type,
            "name": configured.name,
            "configuration": configured.configuration,
            "credentials": credentials,
        }
        provider = existing.get(configured.name.casefold())
        if provider is None:
            await client.request(
                "POST",
                base + "/connector-providers",
                expected=201,
                json=desired,
            )
        else:
            if provider["type"] != configured.type:
                raise ValueError(f"Development Connector Provider differs from existing resource: {configured.name}")
            if applied.changed("connector_provider", configured.name, desired):
                if provider["configuration"] != configured.configuration:
                    raise ValueError(
                        f"Development Connector Provider configuration cannot be changed: {configured.name}"
                    )
                await client.request(
                    "PATCH",
                    f"/api/v1/connector-providers/{provider['id']}",
                    json={"expected_version": provider["version"], "credentials": credentials, "status": "active"},
                )
        applied.record("connector_provider", configured.name, desired)
        count += 1
    return count


async def sync_resources(
    client: Client, base: str, resources: DevelopmentResources, applied: AppliedResources
) -> dict[str, int]:
    """Upsert configured resources through the ordinary authenticated API."""
    model_providers, models = await _sync_models(client, base, resources, applied)
    web_providers = await _sync_web(client, base, resources, applied)
    environment_providers, environment_templates = await _sync_environments(client, base, resources, applied)
    connector_providers = await _sync_connectors(client, base, resources, applied)
    return {
        "model_providers": model_providers,
        "models": models,
        "web_providers": web_providers,
        "environment_providers": environment_providers,
        "environment_templates": environment_templates,
        "connector_providers": connector_providers,
    }


async def sync_existing(settings: Settings, state: Path) -> dict[str, int] | None:
    resources = load_resources()
    manifest_path = state / "seed.json"
    if resources is None or not manifest_path.exists() or not _active(resources):
        return None
    # The seeded local account is the only authority used. The temporary app
    # follows the same ordinary authenticated API path as the seed itself.
    workspace_id = json.loads(manifest_path.read_text())["workspace_id"]
    origin = settings.iam.public_origin
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app),
            base_url=origin.replace("http://", "https://", 1),
            headers={"Origin": origin},
            timeout=60,
            trust_env=False,
        ) as http:
            client = Client(http)
            login = await client.request(
                "POST",
                "/api/v1/auth/login",
                json={"email": settings.iam.initial_admin_email, "password": PASSWORD},
            )
            http.headers["X-A13N-CSRF-Token"] = login["csrf_token"]
            http.headers["X-A13N-Workspace-ID"] = workspace_id
            result = await sync_resources(
                client,
                f"/api/v1/workspaces/{workspace_id}",
                resources,
                AppliedResources(state / "dev-resources-applied.json"),
            )
            await client.request("POST", "/api/v1/auth/logout", expected=204)
            return result
