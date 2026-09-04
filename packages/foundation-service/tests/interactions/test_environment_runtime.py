import base64
from datetime import timedelta

import pytest
from a13n_environment_provider import build_environment_provider_catalog
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
from a13n_service.environments.selection import select_run_environment
from a13n_service.environments.service import EnvironmentService
from a13n_service.interactions import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.control_domain import ThreadRunSubmissionIntent
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import short_session, transaction

from tests.hooks.support import hook_actor, seed_hook_actor_access

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
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW + timedelta(seconds=1)).claim(
        run.id, _worker()
    )
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
        selected = await select_run_environment(session, run=later, workspace_id=WORKSPACE_ID)
        assert selected.environment_id == other.id != first.environment_id
        retry = later.model_copy(update={"retry_of_run_id": first.id})
        selected = await select_run_environment(session, run=retry, workspace_id=WORKSPACE_ID)
        assert selected.environment_id == first.environment_id
        assert (
            await select_run_environment(session, run=later, workspace_id=WORKSPACE_ID, choice=None)
        ).environment_id is None
        new = await select_run_environment(
            session, run=later, workspace_id=WORKSPACE_ID, choice=NewEnvironmentSelection(template_id=template.id)
        )
        assert new.environment_id not in {first.environment_id, other.id}
        reused = await select_run_environment(
            session,
            run=later,
            workspace_id=WORKSPACE_ID,
            choice=ExistingEnvironmentSelection(environment_id=first.environment_id),
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
