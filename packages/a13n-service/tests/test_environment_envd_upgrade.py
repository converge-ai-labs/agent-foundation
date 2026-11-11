"""Managed Envd versions follow the existing durable, fenced environment lifecycle."""

from dataclasses import replace
from datetime import timedelta
from typing import Literal

import pytest
from a13n_service.distribution import OSS
from a13n_service.infra.db import now, transaction
from a13n_service.providers.environment import envd_policy
from a13n_service.providers.environment.envd_policy import EnvdRecipe
from a13n_service.providers.environment.management import Pending
from a13n_service.providers.registry import Registry
from a13n_service.runs.claim import claim as claim_run
from a13n_service.runs.environments import execution, lifecycle
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import RunRow
from a13n_service.tenancy.access import principal_for
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope

from .environments_support import FAKE, FakeProvider, FakeRecipe, backdate, interrupt, start, with_fake_providers

pytestmark = pytest.mark.anyio


class ManagedRecipe(FakeRecipe, EnvdRecipe):
    execution: Literal["envd_http"] = "envd_http"
    envd_management: Literal["managed", "preinstalled"] = "managed"


@pytest.fixture
async def managed(service, monkeypatch):
    env = await with_fake_providers(service)
    env.upgrades = []
    env.pending = False
    env.fail_start = False

    class ManagedProvider(FakeProvider):
        async def start(self, *args, **kwargs):
            if env.fail_start:
                from a13n_harness.environment.errors import EnvironmentProviderErrorCategory, provider_error

                raise provider_error("fake", "envd_upgrade_failed", EnvironmentProviderErrorCategory.INVALID)
            return await super().start(*args, **kwargs)

        def configure_envd_version(self, version):
            env.upgrades.append(version)

        async def create(self, *args, **kwargs):
            state = await super().create(*args, **kwargs)
            return Pending(state, settled=True) if env.pending else state

    async def provider(**kwargs):
        return ManagedProvider()

    definition = replace(
        FAKE,
        environment_model=ManagedRecipe,
        provider_factory=provider,
        execution=replace(FAKE.execution, environment_model=ManagedRecipe),
    )
    env.runtime = replace(env.runtime, registry=Registry.of((*OSS.providers, definition)))
    env.app.state.runtime = env.runtime
    monkeypatch.setattr(envd_policy, "VERSION", "0.8.0")
    return env


async def begin_create(env):
    receipt = await start(env)
    environment_id = receipt["run"]["environment_mounts"][0]["environment_id"]
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id, with_for_update=True)
        await lifecycle.begin(session, row, "creating")
    return receipt, environment_id


async def saved(env, environment_id):
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        return row.status, Handle.model_validate(row.handle)


async def inspect_first_use(env, receipt):
    [lease] = await claim_run(env.runtime, worker_id="upgrade-worker", worker_build="test", limit=1)
    async with transaction(env.runtime.storage) as session:
        run = await session.get_one(RunRow, lease.run_id)
        scope = WorkspaceScope(run.organization_id, run.workspace_id)
        principal = await principal_for(session, env.runtime.access, run.principal_id, confinement=scope)
        authority = ExecutionAuthority.model_validate(run.authority)
    mount = EnvironmentMount.model_validate(receipt["run"]["environment_mounts"][0])
    return lease, principal, authority, mount


async def test_pending_creation_freezes_version_across_reentry_and_publishes_only_after_readiness(managed, monkeypatch):
    env = managed
    _, environment_id = await begin_create(env)
    first = await lifecycle.claim(env.runtime, environment_id, owner="first")
    assert first is not None and first.envd_version == "0.8.0"
    env.pending = True
    await lifecycle.publish(env.runtime, first, await lifecycle.perform(env.runtime, first))
    status, handle = await saved(env, environment_id)
    assert status == "creating" and handle.envd_version is None and handle.envd_target_version == "0.8.0"
    monkeypatch.setattr(envd_policy, "VERSION", "0.9.0")
    await backdate(env, environment_id, next_operation_at=timedelta(seconds=1))
    second = await lifecycle.claim(env.runtime, environment_id, owner="second")
    assert second is not None and second.operation_id == first.operation_id and second.envd_version == "0.8.0"
    env.pending = False
    await lifecycle.publish(env.runtime, second, await lifecycle.perform(env.runtime, second))
    status, handle = await saved(env, environment_id)
    assert status == "ready" and handle.envd_version == "0.8.0" and handle.envd_target_version is None
    assert env.upgrades == ["0.8.0", "0.8.0"]


async def test_first_use_claims_one_starting_operation_and_stale_publisher_cannot_record_an_upgrade(
    managed, monkeypatch
):
    env = managed
    receipt, environment_id = await begin_create(env)
    await lifecycle.advance(env.runtime, environment_id, owner="initial")
    monkeypatch.setattr(envd_policy, "VERSION", "0.9.0")
    args = await inspect_first_use(env, receipt)
    target, operation = await execution._inspect(env.runtime, *args)
    assert target is None and operation is not None and operation.phase == "starting"
    assert operation.envd_version == "0.9.0"
    assert await execution._inspect(env.runtime, *args) == (None, None)
    outcome = await lifecycle.perform(env.runtime, operation)
    await backdate(env, environment_id, lease_expires_at=timedelta(seconds=1))
    current = await lifecycle.claim(env.runtime, environment_id, owner="recovery")
    assert current is not None and current.operation_id == operation.operation_id
    await lifecycle.publish(env.runtime, operation, outcome)
    status, handle = await saved(env, environment_id)
    assert status == "starting" and handle.envd_version == "0.8.0"
    await lifecycle.publish(env.runtime, current, await lifecycle.perform(env.runtime, current))
    assert (await saved(env, environment_id))[1].envd_version == "0.9.0"
    target, operation = await execution._inspect(env.runtime, *args)
    assert target is not None and operation is None


@pytest.mark.parametrize("policy,applied", [("preinstalled", None), ("managed", "0.9.0")])
async def test_first_use_does_not_modify_preinstalled_or_newer_instances(managed, policy, applied):
    env = managed
    receipt, environment_id = await begin_create(env)
    await lifecycle.advance(env.runtime, environment_id, owner="initial")
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id, with_for_update=True)
        handle = Handle.model_validate(row.handle)
        row.handle = handle.model_copy(
            update={"recipe": {**handle.recipe, "envd_management": policy}, "envd_version": applied}
        ).model_dump(mode="json")
        generation = row.generation
    args = await inspect_first_use(env, receipt)
    target, operation = await execution._inspect(env.runtime, *args)
    assert target is not None and operation is None
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        assert row.generation == generation
    assert env.upgrades == ["0.8.0"]


async def test_first_use_serves_existing_runtime_during_human_desktop_reservation(managed, monkeypatch):
    from a13n_harness.environment.models import EnvironmentState
    from a13n_service.runs.environments.desktop.tables import DesktopControlRow

    env = managed
    receipt, environment_id = await begin_create(env)
    await lifecycle.advance(env.runtime, environment_id, owner="initial")
    monkeypatch.setattr(envd_policy, "VERSION", "0.9.0")
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id, with_for_update=True)
        handle = Handle.model_validate(row.handle)
        state = EnvironmentState(provider_key="docker", state_version="1", state={"container_id": "native-desktop"})
        row.handle = handle.model_copy(update={"state": state}).model_dump(mode="json")
        session.add(
            DesktopControlRow(id="dctrl-upgrade", target_key="d" * 64, control="human", environment_id=environment_id)
        )
    args = await inspect_first_use(env, receipt)
    target, operation = await execution._inspect(env.runtime, *args)
    assert target is not None and operation is None
    assert (await saved(env, environment_id))[0] == "ready"
    async with transaction(env.runtime.storage) as session:
        control = await session.get_one(DesktopControlRow, "dctrl-upgrade")
        control.control = "automation"
    target, operation = await execution._inspect(env.runtime, *args)
    assert target is None and operation is not None and operation.envd_version == "0.9.0"


async def test_first_use_defers_upgrade_while_another_run_uses_the_instance(managed, monkeypatch):
    env = managed
    receipt, environment_id = await begin_create(env)
    await lifecycle.advance(env.runtime, environment_id, owner="initial")
    other = await start(env, environments=[{"name": "workspace", "environment_id": environment_id}])
    monkeypatch.setattr(envd_policy, "VERSION", "0.9.0")
    args = await inspect_first_use(env, receipt)
    target, operation = await execution._inspect(env.runtime, *args)
    assert target is not None and operation is None
    assert (await saved(env, environment_id))[0] == "ready"
    await interrupt(env, other["run"]["id"])
    target, operation = await execution._inspect(env.runtime, *args)
    assert target is None and operation is not None and operation.envd_version == "0.9.0"


async def failed_upgrade(env, monkeypatch, *, policy="managed"):
    receipt, environment_id = await begin_create(env)
    await lifecycle.advance(env.runtime, environment_id, owner="initial")
    monkeypatch.setattr(envd_policy, "VERSION", "0.9.0")
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id, with_for_update=True)
        from a13n_service.resources.environment_templates.schemas import Reclamation

        row.reclamation = Reclamation(idle_delete_seconds=60).model_dump(mode="json")
        if policy == "preinstalled":
            handle = Handle.model_validate(row.handle)
            row.handle = handle.model_copy(
                update={"recipe": {**handle.recipe, "envd_management": policy}, "envd_version": None}
            ).model_dump(mode="json")
        await lifecycle.begin(session, row, "starting")
    env.fail_start = True
    operation = await lifecycle.claim(env.runtime, environment_id, owner="failed-upgrade")
    outcome = await lifecycle.perform(env.runtime, operation)
    await lifecycle.publish(env.runtime, operation, outcome)
    await interrupt(env, receipt["run"]["id"])
    monkeypatch.setattr(envd_policy, "VERSION", "0.10.0")
    return environment_id, operation, outcome


@pytest.mark.parametrize("policy", ["managed", "preinstalled"])
async def test_failed_start_does_not_reclaim_a_previously_ready_instance(managed, monkeypatch, policy):
    from a13n_service.runs.environments.maintenance import maintain_environments

    env = managed
    environment_id, _, _ = await failed_upgrade(env, monkeypatch, policy=policy)
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        assert row.last_used_at is not None
        assert row.failure["permanent"] and row.preparation_failed_at is None
    await backdate(
        env,
        environment_id,
        last_used_at=timedelta(days=1),
        preparation_deadline=timedelta(days=1),
    )
    await maintain_environments(env.runtime, owner="retention")
    status, handle = await saved(env, environment_id)
    assert status == "starting" and handle.envd_version == ("0.8.0" if policy == "managed" else None)
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        assert row.preparation_failed_at is None


async def test_explicit_upgrade_retry_retains_target_and_version_but_fences_the_failed_attempt(managed, monkeypatch):
    env = managed
    environment_id, failed, outcome = await failed_upgrade(env, monkeypatch)
    url = f"{env.api}/environments/{environment_id}"
    before = (await saved(env, environment_id))[1]
    progress = (await env.client.get(url + "/creation")).json()
    assert progress["stage"] == "failed" and progress["retryable"]
    current = await env.client.get(url)
    retried = await env.client.post(url + "/retry-creation", headers={"If-Match": current.headers["etag"]})
    assert retried.status_code == 202, retried.text
    status, handle = await saved(env, environment_id)
    assert status == "starting" and handle.state == before.state
    assert handle.envd_target_version == "0.9.0" and handle.envd_version == "0.8.0"
    operation = await lifecycle.claim(env.runtime, environment_id, owner="retry")
    assert operation.operation_id != failed.operation_id and operation.envd_version == "0.9.0"
    await lifecycle.publish(env.runtime, failed, outcome)
    env.fail_start = False
    await lifecycle.publish(env.runtime, operation, await lifecycle.perform(env.runtime, operation))
    status, handle = await saved(env, environment_id)
    assert status == "ready" and handle.envd_target_version is None and handle.state == before.state


@pytest.mark.parametrize("blocker", ["unknown", "unsettled", "lease", "stale"])
async def test_upgrade_retry_refuses_unconfirmed_or_stale_attempts(managed, monkeypatch, blocker):
    env = managed
    environment_id, _, _ = await failed_upgrade(env, monkeypatch)
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id, with_for_update=True)
        if blocker == "unknown":
            row.failure = {**row.failure, "certainty": "unknown"}
        elif blocker == "unsettled":
            row.operation_unsettled = True
        elif blocker == "lease":
            row.lease_owner = "active-worker"
            row.lease_token_hash = "a" * 64
            row.lease_expires_at = await now(session) + timedelta(seconds=60)
    url = f"{env.api}/environments/{environment_id}"
    current = await env.client.get(url)
    response = await env.client.post(
        url + "/retry-creation",
        headers={
            "If-Match": f'"{environment_id}:0"' if blocker == "stale" else current.headers["etag"],
        },
    )
    assert response.status_code == (412 if blocker == "stale" else 409), response.text


async def test_retained_failed_upgrade_does_not_starve_newer_maintenance_work(managed, monkeypatch):
    from a13n_service.runs.environments.maintenance import maintain_environments

    env = managed
    failed_id, _, _ = await failed_upgrade(env, monkeypatch)
    env.runtime = replace(
        env.runtime,
        settings=env.runtime.settings.model_copy(
            update={"environments": env.runtime.settings.environments.model_copy(update={"batch": 1})}
        ),
    )
    _, newer_id = await begin_create(env)
    await backdate(env, failed_id, updated_at=timedelta(days=1), next_operation_at=timedelta(days=1))
    await maintain_environments(env.runtime, owner="maintenance")
    assert (await saved(env, newer_id))[0] == "ready"
    assert (await saved(env, failed_id))[0] == "starting"
    current = await env.client.get(f"{env.api}/environments/{failed_id}")
    retried = await env.client.post(
        f"{env.api}/environments/{failed_id}/retry-creation", headers={"If-Match": current.headers["etag"]}
    )
    assert retried.status_code == 202, retried.text
    env.fail_start = False
    await maintain_environments(env.runtime, owner="retry")
    assert (await saved(env, failed_id))[0] == "ready"


async def test_confirmed_loss_requires_replacement_instead_of_upgrade_retry(managed, monkeypatch):
    env = managed
    environment_id, _, _ = await failed_upgrade(env, monkeypatch)
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id, with_for_update=True)
        row.failure = {
            **row.failure,
            "code": lifecycle.LOST.code,
            "certainty": lifecycle.LOST.certainty,
            "permanent": True,
        }
        assert lifecycle.confirmed_loss(row)
        before = row.handle
    url = f"{env.api}/environments/{environment_id}"
    progress = (await env.client.get(url + "/creation")).json()
    assert not progress["retryable"]
    current = await env.client.get(url)
    response = await env.client.post(url + "/retry-creation", headers={"If-Match": current.headers["etag"]})
    assert response.status_code == 409, response.text
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        assert lifecycle.confirmed_loss(row) and row.handle == before


@pytest.mark.parametrize("blocker", ["provider_identity_changed", "missing_state", "missing_target"])
async def test_progress_and_retry_agree_when_failed_start_cannot_be_retried(managed, monkeypatch, blocker):
    env = managed
    environment_id, _, _ = await failed_upgrade(env, monkeypatch)
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id, with_for_update=True)
        if blocker == "provider_identity_changed":
            row.failure = {**row.failure, "code": blocker}
        else:
            handle = Handle.model_validate(row.handle)
            field = "state" if blocker == "missing_state" else "envd_target_version"
            row.handle = handle.model_copy(update={field: None}).model_dump(mode="json")
        before = row.handle
    url = f"{env.api}/environments/{environment_id}"
    progress = (await env.client.get(url + "/creation")).json()
    assert progress["stage"] == "failed" and not progress["retryable"]
    current = await env.client.get(url)
    response = await env.client.post(url + "/retry-creation", headers={"If-Match": current.headers["etag"]})
    assert response.status_code == 409, response.text
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        assert row.handle == before
