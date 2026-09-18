"""Concurrent Service Runs prepare the same immutable Skills before model work."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from a13n_environment import build_environment_provider_catalog
from a13n_environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.digests import digest_request
from a13n_service.environments.domain import ExistingEnvironmentSelection
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.domain import Session, Thread, ThreadOriginKind, ThreadRole
from a13n_service.interactions.environment_selection import ExplicitEnvironment
from a13n_service.interactions.initialization import RunStateSeed, initialize_start_state
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.settings import Settings
from a13n_service.skills.domain import SkillRevisionLock
from a13n_service.skills.materialization import EnvironmentSkillMaterializer
from a13n_service.skills.objects import SkillPackageStore
from a13n_service.skills.runtime import SkillRuntimePreparer
from a13n_service.storage import short_session, transaction
from anyio import Event, create_task_group, fail_after, sleep
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer
from tests.memory.selection_support import ordinary_memory
from tests.skills.test_runtime import DEPLOY_REVISION_ID, DEPLOY_SKILL_ID, _add_skill, _package

from . import test_attempt_execution as acceptance
from . import worker_helpers
from .conftest import NOW, ORGANIZATION_ID, WORKSPACE_ID
from .test_environment_runtime import template_config

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("preparation", ["on_run", "on_use"])
async def test_workers_complete_shared_environment_skill_preparation_on_first_attempt(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, preparation, caplog
):
    # Concurrent Environment leases require PostgreSQL row locks, as in lifecycle tests.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    await template_config(interaction_sessions, workspace, preparation)
    package = _package("deploy", "Deploy safely.", (("scripts/deploy.sh", b"#!/bin/sh\n"),))
    packages = SkillPackageStore(interaction_object_store)
    await packages.publish(organization_id=ORGANIZATION_ID, workspace_id=WORKSPACE_ID, package=package)
    async with transaction(interaction_sessions) as session:
        _add_skill(session, DEPLOY_SKILL_ID, DEPLOY_REVISION_ID, "Deploy", package)
    lock = SkillRevisionLock(
        skill_id=DEPLOY_SKILL_ID,
        skill_revision_id=DEPLOY_REVISION_ID,
        skill_key="deploy",
        version=1,
        content_digest=package.manifest.content_digest,
    )
    config = acceptance.effective_agent_config().model_copy(update={"skills": (lock,)})
    config = config.model_copy(
        update={
            "content_digest": digest_request(config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}))
        }
    )
    monkeypatch.setattr(acceptance, "effective_agent_config", lambda: config)
    states, first, _ = await acceptance._accept_root(interaction_sessions, interaction_object_store, max_attempts=1)
    async with short_session(interaction_sessions) as session:
        environment_id = (await session.get(RunRecord, first.id)).environment_id
    assert environment_id is not None
    second = first.model_copy(
        update={
            "id": "run_6666666666666666",
            "thread_id": "thread_6666666666666666",
            "session_id": "sess_6666666666666666",
        }
    )
    second_state = initialize_start_state(
        RunStateSeed(
            run_id=second.id,
            agent_id=second.agent_id,
            agent_revision_id=second.agent_revision_id,
            effective_agent_config=config,
        ),
        thread_id=second.thread_id,
    )
    await RunAcceptanceService(
        interaction_sessions,
        states,
        RunPayloadStore(interaction_object_store),
        InlineHookValidator(EndpointPolicy()),
        bindings=ordinary_memory(interaction_sessions),
        clock=lambda: NOW,
        lifecycle=test_lifecycle_writer(),
    ).accept_new_thread(
        session=Session(
            id=second.session_id,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            created_at=NOW,
            updated_at=NOW,
        ),
        thread=Thread(
            id=second.thread_id,
            version=1,
            queue_version=0,
            organization_id=second.organization_id,
            session_id=second.session_id,
            role=ThreadRole.root,
            origin_kind=ThreadOriginKind.new,
            current_run_id=second.id,
            created_at=NOW,
            updated_at=NOW,
        ),
        run=second,
        state=second_state,
        environment=ExplicitEnvironment(ExistingEnvironmentSelection(environment_id=environment_id)),
    )

    # Keep real Worker wiring, replacing only the helper's placeholder dependencies.
    catalog = build_environment_provider_catalog(builtin_keys=("direct-local",))
    monkeypatch.setattr(worker_helpers, "EnvironmentProviderCatalog", lambda: catalog)
    monkeypatch.setattr(
        "a13n_service.process.worker.SkillRuntimePreparer",
        lambda sessions, _packages: SkillRuntimePreparer(sessions, packages),
    )
    materializers = set()
    materialize = EnvironmentSkillMaterializer.materialize

    async def observe_materializer(self, files):
        materializers.add(self)
        await materialize(self, files=files)

    monkeypatch.setattr(EnvironmentSkillMaterializer, "materialize", observe_materializer)
    arrivals = []
    both_staged = Event()
    move = LocalFileOperator.move

    async def publish_together(self, source, destination, *, replace=False):
        if destination.endswith("/SKILL.md"):
            arrivals.append((self, source, destination))
            if len(arrivals) == 2:
                both_staged.set()
            await both_staged.wait()
        return await move(self, source, destination, replace=replace)

    monkeypatch.setattr(LocalFileOperator, "move", publish_together)
    requests = []

    async def respond(messages, _info):
        assert both_staged.is_set()
        assert len(materializers) == 2
        root = workspace / "environments" / environment_id / Path(arrivals[0][2]).parent.parent.relative_to("/")
        completion = json.loads((root / ".a13n-service-complete.json").read_bytes())
        assert completion["packages"] == [lock.model_dump(mode="json")]
        for item in package.files:
            assert (root / package.manifest.content_digest / item.path).read_bytes() == item.content
        assert "Deploy safely." in repr(messages)
        requests.append(messages)
        yield "Skills are ready."

    model_factory = Mock(spec=NativeModelFactory)
    model_factory.build.return_value = FunctionModel(stream_function=respond)
    settings = Settings(worker={"concurrency": 2, "poll_interval_seconds": 0.01})
    async with worker_helpers.worker_runtime(
        interaction_sessions,
        interaction_object_store,
        tmp_path,
        monkeypatch,
        settings=settings,
        model_factory=model_factory,
    ) as (runtime, _shared):
        loop = runtime.execution_loop
        with fail_after(15):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(interaction_sessions) as session:
                        runs = (
                            await session.scalars(select(RunRecord).where(RunRecord.id.in_((first.id, second.id))))
                        ).all()
                        assert len(runs) == 2
                        assert not any(run.status == "failed" for run in runs), (
                            [run.failure_json for run in runs],
                            [getattr(record, "error_type", None) for record in caplog.records],
                            arrivals,
                        )
                        if all(run.status == "completed" for run in runs):
                            break
                    await sleep(0.01)
                await loop.drain()
                await loop.wait_stopped()

    assert len(arrivals) == len(requests) == 2
    assert arrivals[0][0] is not arrivals[1][0]
    assert arrivals[0][1] != arrivals[1][1]
    assert arrivals[0][2] == arrivals[1][2]
    async with short_session(interaction_sessions) as session:
        runs = (await session.scalars(select(RunRecord).where(RunRecord.id.in_((first.id, second.id))))).all()
        assert {run.environment_id for run in runs} == {environment_id}
        attempts = (
            await session.scalars(select(RunAttemptRecord).where(RunAttemptRecord.run_id.in_((first.id, second.id))))
        ).all()
        assert len(attempts) == 2
        assert all(attempt.attempt_number == 1 and attempt.status == "succeeded" for attempt in attempts)
    for run in (first, second):
        state = (await states.read(ORGANIZATION_ID, run.id)).envelope
        assert state.effective_agent_config.skills == (lock,)
        assert state.outcome_candidate is not None
