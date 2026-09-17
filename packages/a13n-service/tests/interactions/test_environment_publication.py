"""First-use publication preserves target evidence before exposing Run operations."""

import asyncio
from datetime import timedelta

import pytest
from a13n_environment import EnvironmentProviderError, EnvironmentState
from a13n_environment.errors import EnvironmentProviderErrorCategory, EnvironmentProviderOutcomeCertainty
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.storage import short_session
from sqlalchemy import select

from tests.environments.test_lifecycle import Target
from tests.environments.test_outcomes import interrupt_publication
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_environment_runtime import template_config
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


async def accepted_environment(sessions, object_store, path):
    _, _, lifecycle = await template_config(sessions, path, "on_use")
    _, run, _ = await _accept_root(sessions, object_store)
    claim = await AttemptScheduler(
        sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    environment = await prepare_run_environment(lifecycle, await prepare_permissions(sessions, run, _authority(claim)))
    assert environment is not None
    return lifecycle, environment, run


@pytest.mark.parametrize("after_commit", [False, True])
async def test_first_use_exposes_one_published_generation_after_write_interruption(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, after_commit
):
    lifecycle, environment, run = await accepted_environment(interaction_sessions, interaction_object_store, tmp_path)
    construct = lifecycle.construct
    constructions = []

    async def counted(operation):
        constructions.append(operation.operation_id)
        return await construct(operation)

    monkeypatch.setattr(lifecycle, "construct", counted)
    attempts = interrupt_publication(monkeypatch, lifecycle, after_commit=after_commit)
    await environment.enter(
        thread_id=run.thread_id, run_id=run.id, agent_instance_id="agent-test", mount_id="workspace"
    )
    try:
        await environment.ensure_ready(frozenset({"files"}))
        await environment.operations.files.write_text("/published.txt", "ready", mode="create")
        assert (
            environment.backing_generation == 1
            and (tmp_path / "environments" / environment.environment_id / "published.txt").read_text() == "ready"
        )
    finally:
        await environment.close()
    assert len(constructions) == 1 and len(attempts) == 2 and attempts[0] is attempts[1]
    async with short_session(interaction_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.environment_id)
        audits = tuple(
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == row.id))
        )
        assert row.status == "running" and row.generation == 1 and row.target_identity is not None
        assert row.operation_id is None and row.last_error is None
        assert (await session.get(RunRecord, run.id)).environment_use_started_at is not None
        assert len(audits) == 1 and audits[0].outcome == "success"


@pytest.mark.parametrize("failure", ["construction", "readiness", "cancelled"])
async def test_first_preparation_failure_retains_known_target_and_dispatch_certainty(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, failure
):
    lifecycle, environment, _ = await accepted_environment(interaction_sessions, interaction_object_store, tmp_path)
    state = EnvironmentState(provider_key="a13n.direct-local", state_version="1", state={"target": "allocated"})
    events = []

    class AllocatedTarget(Target):
        async def _prepare(self, **kwargs):
            self._cache_state(state)
            events.append("allocated")
            if failure == "cancelled":
                raise asyncio.CancelledError("Cancelled after allocation")
            raise EnvironmentProviderError(
                "Readiness failed after allocation",
                code="environment_provider_failure",
                category=EnvironmentProviderErrorCategory.PROVIDER_FAILURE,
                certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            )

    async def construct(operation):
        if failure == "construction":
            raise ValueError("Construction failed before allocation")
        return AllocatedTarget(None, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    monkeypatch.setattr(lifecycle.catalog.require("a13n.direct-local"), "target_identity", lambda **kwargs: "allocated")
    attempts = interrupt_publication(monkeypatch, lifecycle, after_commit=True)
    with pytest.raises(BaseException, match=r"[Cc]ancelled|[Cc]onstruction|Readiness") as error:
        await environment.prepare()
    if failure == "cancelled":
        assert isinstance(error.value, asyncio.CancelledError)
    assert len(attempts) == 2 and attempts[0] is attempts[1]
    async with short_session(interaction_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.environment_id)
        if failure == "construction":
            assert row.status == "unprepared" and row.state is None and row.generation == 0
            assert row.operation_id is None and events == []
        else:
            assert row.status == "unavailable" and row.state == state.model_dump(mode="json")
            assert row.target_identity is not None and row.generation == 1
            assert (row.operation_id is not None) == (failure == "cancelled")
            assert events == ["allocated", "close"]
        audits = tuple(
            await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == row.id))
        )
        assert len(audits) == 1 and audits[0].outcome == "failure"
    await environment.close()
