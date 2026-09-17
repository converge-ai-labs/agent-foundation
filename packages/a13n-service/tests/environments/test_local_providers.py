from __future__ import annotations

from dataclasses import replace

import pytest
from a13n_environment import build_environment_provider_catalog
from a13n_service.environments.domain import LOCAL_PROVIDER_TYPES, CreateProviderRequest, UpdateProviderRequest
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.local import synchronize_local_providers
from a13n_service.environments.service import EnvironmentService
from a13n_service.etags import resource_etag
from a13n_service.process.components import Components
from a13n_service.process.environment import build_environment_catalog
from a13n_service.settings import Settings
from anyio import create_task_group

from .conftest import WORKSPACE_ID, actor


def test_local_types_require_explicit_oss_deployment_configuration():
    assert not LOCAL_PROVIDER_TYPES.intersection(build_environment_catalog(Settings(), Components()))
    configured = Settings(environments={"local_providers": {"docker": {}}})
    catalog = build_environment_catalog(configured, Components())
    assert "docker" in catalog and "direct-local" not in catalog
    with pytest.raises(ValueError, match="local_providers"):
        Settings(environments={"provider_builtins": ["docker"]})
    with pytest.raises(ValueError, match="OSS identity"):
        build_environment_catalog(configured, Components(request_authenticator=actor))


@pytest.mark.anyio
async def test_startup_publishes_one_read_only_organization_provider(environment_sessions, protector):
    catalog = build_environment_provider_catalog(builtin_keys=("docker",))
    configuration = {"docker": {"docker_host": "unix:///run/docker.sock"}}
    async with create_task_group() as tasks:
        for _ in range(2):
            tasks.start_soon(synchronize_local_providers, environment_sessions, catalog, configuration)
    service = EnvironmentService(
        environment_sessions, catalog, protector, deployment_provider_types=LOCAL_PROVIDER_TYPES
    )
    providers = await service.list_providers(actor=actor(), workspace_id=WORKSPACE_ID)
    assert len(providers.items) == 1
    provider = providers.items[0]
    assert provider.workspace_id is None
    assert provider.configuration_source == "deployment"
    assert provider.name == "Docker" and provider.enabled
    assert provider.configuration == configuration["docker"]
    definitions = await service.provider_types(actor())
    assert definitions.items[0].deployment_managed
    with pytest.raises(EnvironmentManagementError, match="configured by the deployment"):
        await service.create_provider(
            actor=actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="docker", name="Other")
        )


@pytest.mark.anyio
async def test_reconfiguration_preserves_old_identity_and_disables_removed_backend(environment_sessions, protector):
    catalog = build_environment_provider_catalog(builtin_keys=("docker",))
    service = EnvironmentService(environment_sessions, catalog, protector)
    first = {"docker": {"docker_host": "unix:///first/docker.sock"}}
    await synchronize_local_providers(environment_sessions, catalog, first)
    original = (await service.list_providers(actor=actor(), workspace_id=WORKSPACE_ID)).items[0]
    await synchronize_local_providers(environment_sessions, catalog, first)
    assert (await service.get_provider(actor=actor(), resource_id=original.id)) == original

    await synchronize_local_providers(
        environment_sessions, catalog, {"docker": {"docker_host": "unix:///second/docker.sock"}}
    )
    providers = (await service.list_providers(actor=actor(), workspace_id=WORKSPACE_ID)).items
    assert len(providers) == 2
    assert sum(provider.enabled for provider in providers) == 1
    previous = await service.get_provider(actor=actor(), resource_id=original.id)
    assert not previous.enabled and previous.configuration == original.configuration

    await synchronize_local_providers(environment_sessions, catalog, {})
    assert all(
        not provider.enabled
        for provider in (await service.list_providers(actor=actor(), workspace_id=WORKSPACE_ID)).items
    )
    await synchronize_local_providers(environment_sessions, catalog, first)
    assert (await service.get_provider(actor=actor(), resource_id=original.id)).enabled


@pytest.mark.anyio
async def test_deployment_provider_cannot_be_edited_even_by_organization_admin(environment_sessions, protector):
    from a13n_service.iam.models import RoleBindingRecord
    from a13n_service.storage import transaction
    from sqlalchemy import update

    from .conftest import ORG_ID

    async with transaction(environment_sessions) as session:
        await session.execute(
            update(RoleBindingRecord).where(RoleBindingRecord.resource_type == "organization").values(role_key="admin")
        )
    catalog = build_environment_provider_catalog(builtin_keys=("direct-local",))
    await synchronize_local_providers(environment_sessions, catalog, {"direct-local": {}})
    service = EnvironmentService(environment_sessions, catalog, protector)
    provider = (await service.list_providers(actor=actor(), workspace_id=WORKSPACE_ID)).items[0]
    admin = replace(actor(), boundary_workspace_id=None, boundary_organization_id=ORG_ID)
    with pytest.raises(EnvironmentManagementError, match="read-only"):
        await service.update_provider(
            actor=admin,
            provider_id=provider.id,
            request=UpdateProviderRequest(name="Retargeted", enabled=False),
            if_match=resource_etag(provider.id, provider.updated_at),
        )
    assert (await service.get_provider(actor=actor(), resource_id=provider.id)) == provider


def test_distributed_deployment_rejects_local_providers():
    with pytest.raises(ValueError, match="single_host"):
        Settings(deployment={"mode": "distributed"}, environments={"local_providers": {"docker": {}}})
    catalog = build_environment_catalog(Settings(deployment={"mode": "distributed"}), Components())
    assert "e2b" in catalog


def test_service_rejects_local_envd():
    with pytest.raises(ValueError):
        Settings(environments={"local_providers": {"a13n.local-envd": {}}})
    with pytest.raises(ValueError, match="Local Envd"):
        Settings(environments={"provider_builtins": ["a13n.local-envd"]})
