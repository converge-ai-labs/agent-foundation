"""Managed outbound identity is private, lifecycle-bound and separate from client enrollment."""

import json

import pytest
from a13n_harness.environment.models import EnvironmentState
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments import lifecycle, outbound
from a13n_service.runs.environments.clients.service import authenticate, validate_attachment
from a13n_service.runs.environments.clients.tables import ClientSettingsRow
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio


async def reserve_outbound(service, runs_kit, monkeypatch, origin="https://control.example.test", *, runtime=None):
    await runs_kit.pause_sweeps(service)
    provider = await service.client.post(
        f"{service.api}/environment-providers", json={"type": "docker", "name": "Outbound Docker"}
    )
    assert provider.status_code == 201, provider.text
    template = await service.client.post(
        f"{service.api}/environment-templates",
        json={
            "name": "Outbound",
            "provider_id": provider.json()["id"],
            "config": {"recipe": {"execution": "envd_ws", "image": "test-envd"}},
        },
    )
    assert template.status_code == 201, template.text
    created = await service.client.post(f"{service.api}/environments", json={"template_id": template.json()["id"]})
    assert created.status_code == 201, created.text
    identity = created.json()["id"]
    with monkeypatch.context() as patch:
        patch.setitem(service.runtime.settings.server.__dict__, "public_url", origin)
        operation = await lifecycle.claim(runtime or service.runtime, identity, owner="test-managed-outbound")
    assert operation is not None, (await service.client.get(f"{service.api}/environments/{identity}")).text
    return identity, operation


def managed_state(operation):
    from a13n_harness.providers.environment.docker.shared import _fingerprint
    from a13n_service.providers.environment.recipes.docker import DockerRecipe

    return EnvironmentState(
        provider_key="docker",
        state_version="1",
        state={
            "environment_id": operation.environment_id,
            "container_id": "managed-container",
            "configuration_fingerprint": _fingerprint(DockerRecipe.model_validate(operation.target.recipe)),
        },
    )


async def test_managed_binding_is_private_stable_and_revoked_with_lifecycle(service, runs_kit, monkeypatch):
    identity, operation = await reserve_outbound(service, runs_kit, monkeypatch)
    bootstrap = operation.target.websocket
    assert bootstrap is not None
    token = bootstrap.credential.get_secret_value()
    attachment = await authenticate(service.runtime, identity, token)
    assert attachment.device_id == identity
    with pytest.raises(ServiceError):
        await authenticate(service.runtime, identity, "incorrect")
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, identity)
        binding = Handle.model_validate(row.handle).outbound
        assert binding is not None
        assert token not in json.dumps(row.handle)
        assert await session.scalar(select(func.count()).select_from(ClientSettingsRow)) == 0
        assert outbound.restore(service.runtime, row, binding).credential == bootstrap.credential
        with pytest.raises(ServiceError):
            await outbound.admitted(session, service.runtime, row, executing=True)
    view = await service.client.get(f"{service.api}/environments/{identity}")
    assert token not in view.text and "outbound" not in view.text and "token_hash" not in view.text
    await lifecycle.publish(service.runtime, operation, lifecycle.Outcome(state=managed_state(operation)))
    await validate_attachment(service.runtime, attachment)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, identity)
        await outbound.admitted(session, service.runtime, row, executing=True)
        await lifecycle.begin(session, row, "stopping")
    with pytest.raises(ServiceError):
        await validate_attachment(service.runtime, attachment)
    with pytest.raises(ServiceError) as stopped:
        await authenticate(service.runtime, identity, token)
    assert stopped.value.code == "unavailable"


async def test_managed_binding_rechecks_provider_status(service, runs_kit, monkeypatch):
    identity, operation = await reserve_outbound(service, runs_kit, monkeypatch)
    bootstrap = operation.target.websocket
    assert bootstrap is not None
    attachment = await authenticate(service.runtime, identity, bootstrap.credential.get_secret_value())
    async with transaction(service.runtime.storage) as session:
        provider = await session.get_one(
            EnvironmentProviderRow, (await session.get_one(EnvironmentRow, identity)).provider_id
        )
        provider.enabled = False
    with pytest.raises(ServiceError):
        await validate_attachment(service.runtime, attachment)


@pytest.mark.parametrize("enabled", [True, False])
async def test_pre_hook_carrier_survives_retry_without_granting_execution(service, runs_kit, monkeypatch, enabled):
    from dataclasses import replace
    from datetime import timedelta

    from a13n_service.app_plugins import AppCatalog, AppManifest
    from a13n_service.app_sdk.hooks import EnvironmentLifecycleHook
    from a13n_service.infra.db import now
    from a13n_service.resources.providers.service import read_provider
    from a13n_service.runs.environments.clients.routing import LifecycleCaller
    from a13n_service.tenancy.tables import OrganizationRow

    async def before_stop(context):
        pass

    app_id = "test.archive"
    runtime = replace(
        service.runtime,
        apps=AppCatalog((AppManifest(app_id, "Archive", "fixture", "1"),)),
        environment_hooks=((app_id, EnvironmentLifecycleHook("before_stop", before_stop, scope="organization")),),
    )
    identity, initial = await reserve_outbound(service, runs_kit, monkeypatch, runtime=runtime)
    await lifecycle.publish(runtime, initial, lifecycle.Outcome(state=managed_state(initial)))
    assert initial.target.websocket is not None
    attachment = await authenticate(runtime, identity, initial.target.websocket.credential.get_secret_value())
    async with transaction(runtime.storage) as db:
        org = await db.get_one(OrganizationRow, service.tenant.organization_id)
        org.settings = {**org.settings, "app_plugins": {"enabled": [app_id] if enabled else []}}
        row = await db.get_one(EnvironmentRow, identity)
        await lifecycle.begin(db, row, "stopping", environment_hooks=runtime.environment_hooks)
    await validate_attachment(runtime, attachment)
    claim = await lifecycle.claim(runtime, identity, owner="before-stop")
    assert claim is not None
    if not enabled:
        assert claim.hooks is None
        with pytest.raises(ServiceError) as closed:
            await validate_attachment(runtime, attachment)
        assert closed.value.code == "unavailable"
        return
    assert claim.hooks is not None
    proof = LifecycleCaller(operation_id=claim.operation_id, generation=claim.generation, token=claim.token)

    async def verify(valid=None, rejected=()):
        async with short_session(runtime.storage) as db:
            row = await db.get_one(EnvironmentRow, identity)
            provider = await read_provider(db, EnvironmentProviderRow, row.provider_id)
            at = await now(db)
            if valid is not None:
                outbound.binding_for(runtime.registry, row, provider, executing=True, lifecycle=valid, at=at)
            for candidate in (None, *rejected):
                with pytest.raises(ServiceError):
                    outbound.binding_for(runtime.registry, row, provider, executing=True, lifecycle=candidate, at=at)

    await verify(
        proof,
        (
            proof.model_copy(update={"token": "wrong"}),
            proof.model_copy(update={"generation": claim.generation + 1}),
            proof.model_copy(update={"operation_id": "wrong"}),
        ),
    )
    async with transaction(runtime.storage) as db:
        row = await db.get_one(EnvironmentRow, identity)
        row.lease_expires_at = (await now(db)) - timedelta(seconds=1)
    await validate_attachment(runtime, attachment)
    await verify(rejected=(proof,))
    next_claim = await lifecycle.claim(runtime, identity, owner="retry-stop")
    assert next_claim is not None and next_claim.operation_id == claim.operation_id
    next_proof = proof.model_copy(update={"token": next_claim.token, "generation": next_claim.generation})
    await verify(next_proof, (proof,))
    await lifecycle.publish(
        runtime,
        next_claim,
        lifecycle.Outcome(
            state=managed_state(initial),
            fault=lifecycle.Fault("archive_pending", "Archive pending", "known", False),
            hook_abort=True,
        ),
    )
    await validate_attachment(runtime, attachment)
    await verify(rejected=(next_proof,))
