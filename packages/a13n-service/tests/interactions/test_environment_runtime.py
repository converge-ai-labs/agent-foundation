import base64
from datetime import timedelta

import pytest
from a13n_environment import build_environment_provider_catalog
from a13n_service.agents.models import AgentRecord
from a13n_service.environments.domain import (
    CreateProviderRequest,
    CreateTemplateRequest,
    ExistingEnvironmentSelection,
    NewEnvironmentSelection,
)
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.environments.service import EnvironmentService
from a13n_service.interactions.control_domain import ThreadRunSubmissionIntent
from a13n_service.interactions.environment_selection import (
    EnvironmentDefault,
    ExplicitEnvironment,
    RetainedRunEnvironment,
    select_run_environment,
)
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import short_session, transaction

from tests.hooks.support import hook_actor, seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import AGENT_ID, NOW, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _authority, _worker

pytestmark = pytest.mark.anyio


async def recipe(sessions, path, preparation):
    await seed_hook_actor_access(sessions)
    protector = SecretProtector.from_base64(encoded_key=base64.b64encode(b"e" * 32).decode(), encryption_key_id="test")
    catalog = build_environment_provider_catalog(builtin_keys=("a13n.direct-local",))
    service = EnvironmentService(sessions, catalog, protector)
    provider = await service.create_provider(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(type="a13n.direct-local", name="Local"),
    )
    template = await service.create_template(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="template",
        request=CreateTemplateRequest(
            name="Workspace",
            provider_id=provider.id,
            configuration={"root": {"path": str(path)}},
            preparation=preparation,
            retention={"idle": {"stop_after": None, "delete_after": None}},
        ),
    )
    async with transaction(sessions) as session:
        agent = await session.get(AgentRecord, AGENT_ID)
        agent.default_environment_template_id = template.id
    return (
        service,
        template,
        EnvironmentLifecycle(sessions, catalog, protector, path, clock=lambda: NOW + timedelta(seconds=2)),
    )


@pytest.mark.parametrize("preparation", ["on_run", "on_use"])
async def test_run_automatically_allocates_and_prepares_at_configured_boundary(
    interaction_sessions, interaction_object_store, tmp_path, preparation
):
    _, template, lifecycle = await recipe(interaction_sessions, tmp_path, preparation)
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    async with short_session(interaction_sessions) as session:
        stored = await session.get(RunRecord, run.id)
        environment_id = stored.environment_id
        assert environment_id and stored.environment_use_started_at is None
        environment = await session.get(EnvironmentRecord, environment_id)
        assert environment.status == "unprepared" and environment.template_revision_id == template.current_revision_id
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    environment = await prepare_run_environment(lifecycle, _authority(claim))
    assert environment is not None
    async with short_session(interaction_sessions) as session:
        stored = await session.get(RunRecord, run.id)
        assert (stored.environment_use_started_at is not None) == (preparation == "on_run")
    await environment.enter(thread_id=run.thread_id, run_id=run.id, agent_instance_id="agent-1", mount_id="workspace")
    if preparation == "on_use":
        async with short_session(interaction_sessions) as session:
            assert (await session.get(RunRecord, run.id)).environment_use_started_at is None
    await environment.ensure_ready(frozenset({"files"}))
    await environment.operations.files.write_text("/created.txt", "hello", mode="create")
    assert (tmp_path / "created.txt").read_text() == "hello"
    await environment.close()
    async with short_session(interaction_sessions) as session:
        stored = await session.get(RunRecord, run.id)
        actual = await session.get(EnvironmentRecord, environment_id)
        assert stored.environment_use_started_at is not None
        assert actual.status == "running" and actual.generation == 1
        assert actual.operation_id is None
    assert (tmp_path / "created.txt").read_text() == "hello"


async def test_switching_defaults_does_not_retarget_retry_or_reuse_template_allocations(
    interaction_sessions, interaction_object_store, tmp_path
):
    service, template, _ = await recipe(interaction_sessions, tmp_path, "on_use")
    _, first, _ = await _accept_root(interaction_sessions, interaction_object_store)
    other = await service.create_environment(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        request=NewEnvironmentSelection(template_id=template.id),
        idempotency_key="other",
    )
    async with transaction(interaction_sessions) as session:
        first = (await session.get(RunRecord, first.id)).to_resource()
        thread = await session.get(ThreadRecord, first.thread_id)
        thread.default_environment_id = other.id
        later = first.model_copy(
            update={"id": "run_second1234567890", "environment_id": None, "environment_access": None}
        )
        selected = await select_run_environment(
            session, run=later, workspace_id=WORKSPACE_ID, intent=EnvironmentDefault.thread
        )
        assert selected.environment_id == other.id != first.environment_id
        retry = later.model_copy(update={"retry_of_run_id": first.id})
        selected = await select_run_environment(
            session, run=retry, workspace_id=WORKSPACE_ID, intent=RetainedRunEnvironment(first.id, first.thread_id)
        )
        assert selected.environment_id == first.environment_id
        assert (
            await select_run_environment(
                session, run=later, workspace_id=WORKSPACE_ID, intent=ExplicitEnvironment(None)
            )
        ).environment_id is None
        new = await select_run_environment(
            session,
            run=later,
            workspace_id=WORKSPACE_ID,
            intent=ExplicitEnvironment(NewEnvironmentSelection(template_id=template.id)),
        )
        assert new.environment_id not in {first.environment_id, other.id}
        reused = await select_run_environment(
            session,
            run=later,
            workspace_id=WORKSPACE_ID,
            intent=ExplicitEnvironment(ExistingEnvironmentSelection(environment_id=first.environment_id)),
        )
        assert reused.environment_id == first.environment_id


async def test_queued_choice_roundtrip_preserves_omitted_and_null():
    omitted = ThreadRunSubmissionIntent.model_validate(
        {"input": {"schema_version": "1", "content": [{"type": "text", "text": "hi"}]}}
    )
    explicit = omitted.model_copy(update={"environment": None})
    assert "environment" not in omitted.retained_payload()
    assert explicit.retained_payload()["environment"] is None
    assert (
        ThreadRunSubmissionIntent.model_validate(omitted.retained_payload()).model_fields_set
        != ThreadRunSubmissionIntent.model_validate(explicit.retained_payload()).model_fields_set
    )


async def test_lazy_unused_environment_closes_without_preparation(
    interaction_sessions, interaction_object_store, tmp_path
):
    _, _, lifecycle = await recipe(interaction_sessions, tmp_path, "on_use")
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    environment = await prepare_run_environment(lifecycle, _authority(claim))
    await environment.enter(thread_id=run.thread_id, run_id=run.id, agent_instance_id="agent-1", mount_id="workspace")
    assert environment.dump_state() is None
    await environment.close()
    async with short_session(interaction_sessions) as session:
        stored = await session.get(RunRecord, run.id)
        actual = await session.get(EnvironmentRecord, stored.environment_id)
        assert stored.environment_use_started_at is None
        assert actual.status == "unprepared" and actual.generation == 0


async def test_reconnect_preserves_backing_generation_after_cleanup_failure(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    from a13n_environment import EnvironmentError
    from a13n_environment.direct_local.provider import DirectLocalEnvironment

    _, _, lifecycle = await recipe(interaction_sessions, tmp_path, "on_run")
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    environment = await prepare_run_environment(lifecycle, _authority(claim))
    await environment.enter(thread_id=run.thread_id, run_id=run.id, agent_instance_id="agent-1", mount_id="workspace")
    original = environment.descriptor.backing_identity
    real_close = DirectLocalEnvironment._close
    cleanup_failed = False

    async def close_once(self):
        nonlocal cleanup_failed
        await real_close(self)
        if not cleanup_failed:
            cleanup_failed = True
            raise RuntimeError("Connection cleanup failed")

    monkeypatch.setattr(DirectLocalEnvironment, "_close", close_once)
    real_ready = DirectLocalEnvironment._ensure_ready
    failed = False

    async def disconnect_once(self, operations):
        nonlocal failed
        if not failed:
            failed = True
            raise EnvironmentError("Lost connection", code="environment_unavailable")
        await real_ready(self, operations)

    monkeypatch.setattr(DirectLocalEnvironment, "_ensure_ready", disconnect_once)
    with pytest.raises(EnvironmentError) as caught:
        await environment.ensure_ready(frozenset({"files"}))
    assert caught.value.code == "environment_connection_refreshed"
    assert environment.descriptor.backing_identity == original
    await environment.ensure_ready(frozenset({"files"}))
    await environment.close()


async def test_worker_cannot_claim_environment_from_another_host(
    interaction_sessions, interaction_object_store, tmp_path
):
    from a13n_service.environments.models import EnvironmentProviderRecord

    _, _, _ = await recipe(interaction_sessions, tmp_path, "on_use")
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    async with transaction(interaction_sessions) as session:
        stored = await session.get(RunRecord, run.id)
        selected = await session.get(EnvironmentRecord, stored.environment_id)
        provider = await session.get(EnvironmentProviderRecord, selected.provider_id)
        provider.configuration = {"host_id": "another-worker-host"}
    scheduler = AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    )
    assert await scheduler.claim(run.id, _worker()) is None


async def test_close_during_readiness_never_recovers_or_leaks_connection(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    import asyncio
    from unittest.mock import AsyncMock

    from a13n_environment import EnvironmentError
    from a13n_environment.direct_local.provider import DirectLocalEnvironment

    _, _, lifecycle = await recipe(interaction_sessions, tmp_path, "on_run")
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    environment = await prepare_run_environment(lifecycle, _authority(claim))
    await environment.enter(thread_id=run.thread_id, run_id=run.id, agent_instance_id="agent-1", mount_id="workspace")
    started, release = asyncio.Event(), asyncio.Event()

    async def unavailable(self, operations):
        started.set()
        await release.wait()
        raise EnvironmentError("Disconnected", code="environment_unavailable")

    execute = AsyncMock(wraps=lifecycle.execute)
    monkeypatch.setattr(lifecycle, "execute", execute)
    monkeypatch.setattr(DirectLocalEnvironment, "_ensure_ready", unavailable)
    pending = asyncio.create_task(environment.ensure_ready(frozenset({"files"})))
    await started.wait()
    await environment.close()
    release.set()
    with pytest.raises(RuntimeError, match="closed"):
        await pending
    execute.assert_not_awaited()
    await environment.close()


async def test_concurrent_lazy_use_prepares_once(interaction_sessions, interaction_object_store, tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    _, _, lifecycle = await recipe(interaction_sessions, tmp_path, "on_use")
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    environment = await prepare_run_environment(lifecycle, _authority(claim))
    await environment.enter(thread_id=run.thread_id, run_id=run.id, agent_instance_id="agent-1", mount_id="workspace")
    entered, release = asyncio.Event(), asyncio.Event()
    execute = lifecycle.execute

    async def blocked(operation, *, recovering=None):
        entered.set()
        await release.wait()
        return await execute(operation, recovering=recovering)

    execute_spy = AsyncMock(side_effect=blocked)
    monkeypatch.setattr(lifecycle, "execute", execute_spy)
    first = asyncio.create_task(environment.ensure_ready(frozenset({"files"})))
    await entered.wait()
    second = asyncio.create_task(environment.ensure_ready(frozenset({"files"})))
    release.set()
    await asyncio.gather(first, second)
    execute_spy.assert_awaited_once()
    await environment.close()


async def test_cancelled_delegate_entry_closes_acquired_connection(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    import asyncio
    from unittest.mock import AsyncMock

    from a13n_environment.direct_local.provider import DirectLocalEnvironment

    _, _, lifecycle = await recipe(interaction_sessions, tmp_path, "on_use")
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    environment = await prepare_run_environment(lifecycle, _authority(claim))
    close = AsyncMock()
    monkeypatch.setattr(DirectLocalEnvironment, "enter", AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(DirectLocalEnvironment, "_close", close)
    with pytest.raises(asyncio.CancelledError):
        await environment.prepare()
    close.assert_awaited_once()
    await environment.close()
    async with short_session(interaction_sessions) as session:
        stored = await session.get(EnvironmentRecord, environment.environment_id)
        assert stored.generation == 1 and stored.operation_id is None


async def test_postgresql_concurrent_thread_key_allocates_one_environment(
    postgres_interaction_sessions, tmp_path, monkeypatch
):
    import asyncio

    from a13n_service.interactions import thread_creation
    from a13n_service.interactions.thread_domain import CreateThreadRequest
    from sqlalchemy import func, select

    _, template, _ = await recipe(postgres_interaction_sessions, tmp_path, "on_use")
    read = thread_creation.load_replay
    barrier = asyncio.Barrier(2)
    calls = 0

    async def synchronized_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        initial = calls <= 2
        if initial:
            await barrier.wait()
        return await read(*args, **kwargs)

    monkeypatch.setattr(thread_creation, "load_replay", synchronized_read)
    body = CreateThreadRequest(environment=NewEnvironmentSelection(template_id=template.id))
    first, second = await asyncio.gather(
        *(
            thread_creation.allocate_thread(
                postgres_interaction_sessions,
                actor=hook_actor(),
                workspace_id=WORKSPACE_ID,
                body=body,
                idempotency_key="concurrent-thread",
            )
            for _ in range(2)
        )
    )
    assert first == second
    async with short_session(postgres_interaction_sessions) as session:
        assert await session.scalar(select(func.count()).select_from(EnvironmentRecord)) == 1
    assert not (tmp_path / "workspace").exists()


def test_environment_modules_import_independently():
    import subprocess
    import sys

    for module in ("environments.runtime", "environments.selection", "interactions.attempt_executor"):
        subprocess.run([sys.executable, "-c", f"import a13n_service.{module}"], check=True, capture_output=True)


async def test_postgresql_environment_lease_fences_competing_workers(
    postgres_interaction_sessions, interaction_object_store, tmp_path
):
    import asyncio

    from a13n_service.environments.lifecycle import EnvironmentOperationBusy

    _, _, lifecycle = await recipe(postgres_interaction_sessions, tmp_path, "on_use")
    _, run, _ = await _accept_root(postgres_interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        postgres_interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    attempt = _authority(claim)
    async with short_session(postgres_interaction_sessions) as session:
        environment_id = (await session.get(RunRecord, run.id)).environment_id
    results = await asyncio.gather(
        lifecycle.acquire(environment_id, "prepare", attempt=attempt),
        lifecycle.acquire(environment_id, "prepare", attempt=attempt),
        return_exceptions=True,
    )
    assert sum(isinstance(result, EnvironmentOperationBusy) for result in results) == 1
    old = next(result for result in results if not isinstance(result, BaseException))
    async with transaction(postgres_interaction_sessions) as session:
        row = await session.get(EnvironmentRecord, environment_id)
        row.operation_expires_at = NOW
    current = await lifecycle.acquire(environment_id, "prepare", attempt=attempt)
    assert current.operation_id == old.operation_id and current.fence > old.fence
    with pytest.raises(RuntimeError, match="authority changed"):
        await lifecycle.publish(old, None)
    result = await lifecycle.execute(current)
    await result.environment.close()


async def test_postgresql_shared_approval_wait_is_idle_only_after_last_active_user(
    postgres_interaction_sessions, interaction_object_store, tmp_path
):
    from a13n_service.environments.retention import refresh_retention
    from a13n_service.interactions.models import SessionRecord
    from a13n_service.interactions.records import run_record, thread_record
    from sqlalchemy import select

    from .test_acceptance import _accepted_run
    from .test_attempt_execution import _wait_for_approval

    await recipe(postgres_interaction_sessions, tmp_path, "on_use")
    run, _ = await _wait_for_approval(postgres_interaction_sessions, interaction_object_store)
    now = NOW + timedelta(seconds=5)
    async with transaction(postgres_interaction_sessions) as session:
        original = await session.get(RunRecord, run.id)
        assert original.status == "waiting" and original.wait_reason == "approval"
        original.environment_use_started_at = now
        thread = await session.get(ThreadRecord, run.thread_id)
        parent_session = await session.get(SessionRecord, thread.session_id)
        shared_session = SessionRecord(
            id="session_shared12345678",
            organization_id=parent_session.organization_id,
            workspace_id=parent_session.workspace_id,
            created_at=now,
            updated_at=now,
        )
        session.add(shared_session)
        await session.flush()
        shared_thread = thread.to_resource().model_copy(
            update={
                "id": "thread_shared1234567890",
                "session_id": shared_session.id,
                "current_run_id": None,
                "head_run_id": None,
            }
        )
        session.add(thread_record(shared_thread))
        await session.flush()
        shared_run = _accepted_run(
            run_id="run_shared123456789012",
            thread_id=shared_thread.id,
            idempotency_key="shared",
            request_fingerprint="a" * 64,
        ).model_copy(
            update={
                "session_id": shared_session.id,
                "environment_id": original.environment_id,
                "environment_access": original.environment_access,
            }
        )
        shared = run_record(shared_run)
        shared.status = "running"
        shared.attempts_started = shared.recovery_attempts_started = 1
        shared.environment_use_started_at = now
        session.add(shared)
        environment = await session.scalar(
            select(EnvironmentRecord).where(EnvironmentRecord.id == original.environment_id).with_for_update()
        )
        assert await refresh_retention(session, environment, now) == "active"
        shared.status = "cancelled"
        shared.failure_json = {"code": "cancelled", "message": "User cancelled"}
        shared.sealed_at = now + timedelta(seconds=5)
        assert await refresh_retention(session, environment, now + timedelta(seconds=5)) == "idle"
        since = environment.condition_since
        assert await refresh_retention(session, environment, now + timedelta(seconds=20)) == "idle"
        assert environment.condition_since == since


async def test_registered_external_environment_uses_connection_configuration(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    from a13n_service.environments.domain import RegisterEnvironmentRequest, TemplateConfiguration

    service, _, lifecycle = await recipe(interaction_sessions, tmp_path, "on_use")
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    async with short_session(interaction_sessions) as session:
        stored = await session.get(RunRecord, run.id)
        managed = await session.get(EnvironmentRecord, stored.environment_id)
        provider_id = managed.provider_id
    external = await service.create_environment(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        request=RegisterEnvironmentRequest(provider_id=provider_id, configuration={"root": {"path": str(tmp_path)}}),
        idempotency_key="external",
    )
    async with transaction(interaction_sessions) as session:
        stored = await session.get(RunRecord, run.id)
        stored.environment_id = external.id
    original = lifecycle.construct

    async def external_construct(operation):
        assert not isinstance(operation.configuration, TemplateConfiguration)
        assert not hasattr(operation.configuration, "retention")
        return await original(operation)

    construct = AsyncMock(side_effect=external_construct)
    monkeypatch.setattr(lifecycle, "construct", construct)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    environment = await prepare_run_environment(lifecycle, _authority(claim))
    await environment.enter(thread_id=run.thread_id, run_id=run.id, agent_instance_id="agent-1", mount_id="workspace")
    await environment.ensure_ready(frozenset({"files"}))
    await environment.close()
    construct.assert_awaited_once()
    await lifecycle.maintain(external.id)
    construct.assert_awaited_once()
    with pytest.raises(ValueError, match="externally owned"):
        await lifecycle.acquire(external.id, "delete")


async def test_missing_managed_recipe_never_falls_back_to_external_configuration():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_service.environments.configuration import load_configuration

    session = SimpleNamespace(get=AsyncMock(return_value=None))
    for revision_id in (None, "envrev-missing"):
        row = SimpleNamespace(
            ownership="managed",
            template_revision_id=revision_id,
            external_configuration={"configuration": {"root": {"path": "/tmp"}}},
        )
        with pytest.raises(ValueError, match="Managed Environment recipe"):
            await load_configuration(session, row)
