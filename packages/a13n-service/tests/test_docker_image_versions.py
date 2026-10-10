"""Service release defaults, explicit template pins and durable Docker image identity across upgrades."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from a13n_environment.docker.configuration import DockerEnvironmentConfiguration
from a13n_environment.docker.management import DockerManagement
from a13n_environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine
from a13n_environment.models import EnvironmentState
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_service.infra.db import transaction
from a13n_service.providers.environments.docker import DEFAULT_SANDBOX_IMAGE, default_image, docker
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.runs.environments.adapters import execution_connector
from a13n_service.runs.environments.lifecycle import claim
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow

from .environments_support import backdate, reserve

IMAGE = "ghcr.io/converge-ai-labs/a13n-sandbox"


def installed(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    def version(name: str) -> str:
        assert name == "a13n-service"
        return value

    monkeypatch.setattr(import_module(default_image.__module__), "version", version)


@pytest.mark.parametrize(
    "version,tag",
    [
        ("0.0.0", "dev"),
        ("0.0.0+g123", "dev"),
        ("0.1.0.dev3", "dev"),
        ("0.1.0rc1.dev3", "dev"),
        ("0.1.0.dev3+g123", "dev"),
        ("0.0.1", "0.0.1"),
        ("0.1.0", "0.1.0"),
        ("12.34.56", "12.34.56"),
        ("0.1.0rc1", "0.1.0-rc.1"),
        ("12.34.56rc78", "12.34.56-rc.78"),
    ],
)
def test_service_version_selects_a_reviewed_sandbox_independent_of_its_version(
    monkeypatch: pytest.MonkeyPatch, version: str, tag: str
) -> None:
    installed(monkeypatch, version)
    definition = docker(host=None, mount_roots=())
    model = definition.environment_model
    expected = f"{IMAGE}:dev" if tag == "dev" else DEFAULT_SANDBOX_IMAGE
    assert model.model_validate({}).image == expected
    assert model.model_json_schema()["properties"]["image"]["default"] == expected
    assert model.model_validate({"image": "custom@sha256:" + "a" * 64}).image == "custom@sha256:" + "a" * 64
    # Service policy does not change the independently released Harness provider.
    assert DockerEnvironmentConfiguration().image == f"{IMAGE}:dev"


def test_invalid_metadata_does_not_fall_back_to_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    installed(monkeypatch, "invalid")
    with pytest.raises(ValueError):
        default_image()


def test_missing_package_metadata_does_not_fall_back_to_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(import_module(default_image.__module__), "version", missing)
    with pytest.raises(PackageNotFoundError):
        default_image()


async def select_release(service: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    # Drive claims explicitly; only the SDK transport is fake, not recipe validation or construction.
    sweeps = [task for task in service.app.state.background if task.get_name() == "control-sweeps"]
    for task in sweeps:
        task.cancel()
    await asyncio.gather(*sweeps, return_exceptions=True)
    installed(monkeypatch, version)

    definition = docker(host=None, mount_roots=())
    registry = replace(
        service.runtime.registry,
        environments=ProviderCatalog(
            definition if item.type == "docker" else item for item in service.runtime.registry.environments.values()
        ),
    )
    service.runtime = replace(service.runtime, registry=registry)
    service.app.state.runtime = service.runtime


async def template(service: SimpleNamespace, recipe: dict) -> dict:
    provider = await service.client.post("/api/v1/environment-providers", json={"name": "Docker", "type": "docker"})
    assert provider.status_code == 201, provider.text
    response = await service.client.post(
        "/api/v1/environment-templates",
        json={"name": "Box", "provider_id": provider.json()["id"], "config": {"recipe": recipe}},
    )
    assert response.status_code == 201, response.text
    assert response.json()["config"]["recipe"] == recipe
    return response.json()


def retained_state(recipe: dict, environment_id: str) -> EnvironmentState:
    target = DockerManagement(
        DockerEnvironmentConfiguration.model_validate(recipe),
        environment_id,
        None,
        DockerProviderRuntime(DockerSDKEngine(Mock())),
    )
    return EnvironmentState(
        provider_key="docker",
        state_version="1",
        state={
            "environment_id": environment_id,
            "container_id": "a" * 64,
            "configuration_fingerprint": target.fingerprint,
        },
    )


@pytest.mark.anyio
@pytest.mark.parametrize("image", [None, f"{IMAGE}:0.1.0", "custom@sha256:" + "a" * 64])
async def test_instance_pins_effective_image_and_template_preserves_explicit_pin(
    service, monkeypatch: pytest.MonkeyPatch, image: str | None
) -> None:  # type: ignore[no-untyped-def]
    await select_release(service, monkeypatch, "0.1.0")
    configured = await template(service, {} if image is None else {"image": image})
    instance = await reserve(service, configured["id"])
    operation = await claim(service.runtime, instance["id"], owner="test")
    assert operation is not None
    expected = image or DEFAULT_SANDBOX_IMAGE
    assert operation.target.recipe["image"] == expected
    async with transaction(service.runtime.storage) as session:
        row = await session.get(EnvironmentRow, instance["id"])
        assert row is not None
        assert Handle.model_validate(row.handle).recipe["image"] == expected
    state = retained_state(dict(operation.target.recipe), operation.target.environment_id)
    first = await execution_connector(service.runtime, replace(operation.target, state=state))
    assert first.state == state

    # A restarted/new Worker reconstructs the same target under the next Service release without a fingerprint conflict.
    monkeypatch.setattr(import_module(default_image.__module__), "DEFAULT_SANDBOX_IMAGE", f"{IMAGE}:0.2.0")
    await select_release(service, monkeypatch, "0.1.1")
    await backdate(service, instance["id"], lease_expires_at=timedelta(seconds=1))
    resumed = await claim(service.runtime, instance["id"], owner="new-worker")
    assert resumed is not None and resumed.target.recipe["image"] == expected
    restored = await execution_connector(service.runtime, replace(resumed.target, state=state))
    assert restored.state == state

    # The unchanged template follows the new default only if the caller left image unspecified.
    next_instance = await reserve(service, configured["id"])
    next_operation = await claim(service.runtime, next_instance["id"], owner="test")
    assert next_operation is not None
    assert next_operation.target.recipe["image"] == (image or f"{IMAGE}:0.2.0")


@pytest.mark.anyio
@pytest.mark.parametrize("has_state", [False, True])
async def test_legacy_handle_keeps_dev_even_after_an_interrupted_create(service, monkeypatch, has_state):
    await select_release(service, monkeypatch, "0.1.0")
    configured = await template(service, {})
    instance = await reserve(service, configured["id"])
    operation = await claim(service.runtime, instance["id"], owner="test")
    assert operation is not None
    recipe = {"image": "ghcr.io/converge-ai-labs/a13n-docker-environment:dev"}
    state = retained_state(recipe, instance["id"]) if has_state else None
    # Old durable handles copied the sparse template, including before a create response arrived.
    async with transaction(service.runtime.storage) as session:
        row = await session.get(EnvironmentRow, instance["id"])
        assert row is not None
        row.handle = Handle(recipe={}, state=state).model_dump(mode="json")
    await backdate(service, instance["id"], lease_expires_at=timedelta(seconds=1))
    resumed = await claim(service.runtime, instance["id"], owner="new-worker")
    assert resumed is not None and resumed.target.recipe == recipe
    # The same resolved recipe drives all management actions and fixed-target execution.
    if state is not None:
        connector = await execution_connector(service.runtime, resumed.target)
        assert connector.state == state


@pytest.mark.anyio
async def test_a_recipe_rejected_by_the_new_definition_records_a_durable_refusal(
    service, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    await select_release(service, monkeypatch, "0.1.0")
    configured = await template(service, {})
    instance = await reserve(service, configured["id"])
    # A persisted template can outlive the schema that accepted it. Validation must fail the operation, not the sweep.
    async with transaction(service.runtime.storage) as session:
        row = await session.get(EnvironmentTemplateRow, configured["id"])
        assert row is not None
        row.config = {**row.config, "recipe": {"image": ""}}
    assert await claim(service.runtime, instance["id"], owner="test") is None
    async with transaction(service.runtime.storage) as session:
        row = await session.get(EnvironmentRow, instance["id"])
        assert row is not None and row.failure is not None
        assert row.handle is None
        assert row.failure["code"] == "provider_spec_invalid"
        assert row.failure["permanent"] is True
        assert row.failure["certainty"] == "not_dispatched"
