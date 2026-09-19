"""Private resource loading and repeatable local import."""

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import SecretStr

from dev.service.dev_resource_sync import (
    AppliedResources,
    _sync_connectors,
    _sync_environments,
    _sync_web,
    sync_resources,
)
from dev.service.dev_resources import _active, _model_credential, load_resources


def test_private_file_requires_private_regular_file(tmp_path: Path):
    path = tmp_path / "resources.toml"
    path.write_text(
        'version = 1\n[[model_providers]]\ntype = "openrouter"\nname = "OpenRouter"\ncredential = "secret"\nmodels = []\n'
    )
    path.chmod(0o600)
    assert load_resources(path).model_providers[0].credential.get_secret_value() == "secret"

    path.chmod(0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        load_resources(path)

    path.chmod(0o600)
    link = tmp_path / "link.toml"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="Cannot open"):
        load_resources(link)


def test_invalid_file_does_not_echo_credential(tmp_path: Path):
    path = tmp_path / "resources.toml"
    path.write_text('version = 1\n[[model_providers]]\ncredential = "private-secret"\n')
    path.chmod(0o600)
    with pytest.raises(ValueError) as error:
        load_resources(path)
    assert "private-secret" not in str(error.value)


def test_example_and_typed_model_credential(tmp_path: Path):
    example = Path(__file__).parents[1] / "dev-resources.example.toml"
    path = tmp_path / "resources.toml"
    path.write_bytes(example.read_bytes())
    path.chmod(0o600)
    resources = load_resources(path)
    assert len(resources.environment_templates) == 1
    assert not _active(resources)
    provider = resources.model_providers[0]
    provider.credential["api_key"] = SecretStr("test-key")
    assert _model_credential(provider) == "test-key"


@pytest.mark.anyio
async def test_import_creates_once_and_updates_existing_model(tmp_path: Path):
    path = tmp_path / "resources.toml"
    path.write_text(
        'version = 1\n[[model_providers]]\ntype = "openrouter"\nname = "OpenRouter"\n'
        'credential = "secret"\n[[model_providers.models]]\nkey = "glm"\nname = "GLM"\n'
        'upstream_model = "z-ai/glm-5.3-flash"\n'
    )
    path.chmod(0o600)
    resources = load_resources(path)

    class Client:
        def __init__(self):
            self.providers = []
            self.models = []
            self.posts = 0
            self.updates = 0

        async def collection(self, route):
            if route.endswith("model-providers"):
                return self.providers
            if route.endswith("/models"):
                return self.models
            return []

        async def etag(self, route):
            return {"If-Match": '"1"'}

        async def request(self, method, route, *, expected=200, **kwargs):
            data = kwargs["json"]
            if method == "POST":
                self.posts += 1
                item = {
                    **data,
                    "id": f"id-{self.posts}",
                    "workspace_id": "workspace-1",
                }
                (self.providers if route.endswith("model-providers") else self.models).append(item)
                return item
            item = next(
                item
                for item in (self.providers if "model-providers" in route else self.models)
                if route.endswith(item["id"])
            )
            self.updates += 1
            item.update(data)
            return item

    client = Client()
    base = "/api/v1/workspaces/workspace-1"
    applied = AppliedResources(tmp_path / "applied.json")
    assert (await sync_resources(client, base, resources, applied))["models"] == 1
    assert client.posts == 2
    assert client.models[0]["model_api"] == "openrouter.chat_completions"
    applied = AppliedResources(applied.path)
    assert (await sync_resources(client, base, resources, applied))["models"] == 1
    assert client.posts == 2
    assert client.updates == 0

    client.models[0]["upstream_model"] = "old-model"
    assert (await sync_resources(client, base, resources, applied))["models"] == 1
    assert client.models[0]["upstream_model"] == "z-ai/glm-5.3-flash"
    assert client.updates == 1
    assert "secret" not in applied.path.read_text()
    assert applied.path.stat().st_mode & 0o077 == 0


@pytest.mark.anyio
async def test_web_environment_and_connector_import_reuses_resources(tmp_path: Path):
    example = Path(__file__).parents[1] / "dev-resources.example.toml"
    path = tmp_path / "resources.toml"
    path.write_text(example.read_text().replace('api_key = ""', 'api_key = "test-key"'))
    path.chmod(0o600)
    resources = load_resources(path)

    class Client:
        def __init__(self):
            self.items = {
                "web-providers": [],
                "environment-providers": [],
                "environment-templates": [],
                "connector-providers": [],
            }
            self.revisions = {}
            self.creates = 0

        async def collection(self, route):
            return self.items[route.rsplit("/", 1)[-1]]

        async def etag(self, route):
            return {"If-Match": '"1"'}

        async def request(self, method, route, *, expected=200, **kwargs):
            if method == "GET":
                return self.revisions[route.rsplit("/", 1)[-1]]
            data = kwargs["json"]
            if method == "POST":
                self.creates += 1
                kind = route.rsplit("/", 1)[-1]
                item = {**deepcopy(data), "id": f"id-{self.creates}", "workspace_id": "workspace-1", "version": 1}
                if kind == "environment-templates":
                    item["default_revision_id"] = "revision-1"
                    self.revisions["revision-1"] = data
                self.items[kind].append(item)
                return item
            if method == "PUT":
                return {}
            if method == "PATCH":
                return {"version": data.get("expected_version", 1) + 1}
            raise AssertionError(method)

    client = Client()
    base = "/api/v1/workspaces/workspace-1"
    applied = AppliedResources(tmp_path / "applied.json")
    for _ in range(2):
        assert await _sync_web(client, base, resources, applied) == 1
        assert await _sync_environments(client, base, resources, applied) == (1, 1)
        assert await _sync_connectors(client, base, resources, applied) == 1
        applied = AppliedResources(applied.path)
    assert client.creates == 4
    resources.connector_providers[0].configuration["changed"] = True
    with pytest.raises(ValueError, match="configuration cannot be changed"):
        await _sync_connectors(client, base, resources, applied)
    assert client.creates == 4
    resources.environment_providers[0].configuration["changed"] = True
    with pytest.raises(ValueError, match="configuration cannot be changed"):
        await _sync_environments(client, base, resources, applied)
