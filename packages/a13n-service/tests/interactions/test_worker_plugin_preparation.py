"""Real Worker execution only starts after its plugin configuration is durable."""

import json
from unittest.mock import Mock

import pytest
from a13n_harness.plugin_factories import build_harness_plugin_factory_catalog
from a13n_service.agents.domain import PluginSelection
from a13n_service.agents.plugin_preparation import prepare_agent_plugins
from a13n_service.digests import digest_request
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.settings import Settings
from a13n_service.storage import ObjectStoreUnavailable, short_session
from anyio import create_task_group, fail_after, sleep
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from tests.agents.test_installed_plugins import Configuration, InstalledFactory, InstalledPlugin
from tests.hooks.support import seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer

from . import test_attempt_execution as acceptance
from .conftest import NOW, ORGANIZATION_ID
from .worker_helpers import worker_runtime

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "phase", ["fresh", "prepared-before-input", "lost-response", "write-failure", "unconfirmed-write"]
)
async def test_worker_preserves_initial_input_and_uses_durable_plugin_defaults(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, phase
):
    original = acceptance.effective_agent_config()
    config = original.model_copy(
        update={"plugins": (PluginSelection(instance_name="audit", plugin_key="test.audit", config={}),)}
    )
    config = config.model_copy(
        update={
            "content_digest": digest_request(config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}))
        }
    )
    monkeypatch.setattr(acceptance, "effective_agent_config", lambda: config)
    await seed_hook_actor_access(interaction_sessions)
    states, run, initial = await acceptance._accept_root(interaction_sessions, interaction_object_store)
    if phase == "prepared-before-input":
        claim = await AttemptScheduler(
            interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()
        ).claim(run.id, acceptance._worker(build_id="build-a"))
        assert isinstance(claim, ClaimedAttempt)
        state = await states.claim_writer(await states.read_run(run), attempt_number=claim.attempt.attempt_number)
        old_catalog = build_harness_plugin_factory_catalog(explicit_factories=(InstalledFactory(),))
        await states.prepare_plugins(
            state, prepare_agent_plugins(old_catalog, config), attempt_number=claim.attempt.attempt_number
        )
        execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
        await execution.yield_attempt(acceptance._authority(claim), reason=RunAttemptYieldReason.service_drain)

    expected_limit = 5 if phase == "prepared-before-input" else 60
    observed = []
    created = []
    requests = []
    publications = []
    failed_write = False
    failed_read = False
    put = interaction_object_store.put
    stat = interaction_object_store.stat

    async def intercept_put(key, body, **kwargs):
        nonlocal failed_write
        envelope = json.loads(body)
        is_preparation = envelope.get("checkpoint_seq") == 0 and envelope.get("prepared_plugins") is not None
        if is_preparation:
            publications.append(envelope)
            if phase in {"lost-response", "write-failure", "unconfirmed-write"} and not failed_write:
                failed_write = True
                assert created == []
                assert observed == []
                assert requests == []
                if phase != "write-failure":
                    await put(key, body, **kwargs)
                raise ObjectStoreUnavailable("preparation response lost")
        return await put(key, body, **kwargs)

    async def intercept_stat(*args, **kwargs):
        nonlocal failed_read
        if phase == "unconfirmed-write" and failed_write and not failed_read:
            failed_read = True
            raise ObjectStoreUnavailable("preparation reconciliation unavailable")
        return await stat(*args, **kwargs)

    monkeypatch.setattr(interaction_object_store, "put", intercept_put)
    monkeypatch.setattr(interaction_object_store, "stat", intercept_stat)

    class NewConfiguration(Configuration):
        limit: int = 60

    class ObservingPlugin(InstalledPlugin):
        async def for_run(self, context):
            current = await states.read(ORGANIZATION_ID, run.id)
            assert current.envelope.prepared_plugins.plugins[0].config["limit"] == expected_limit
            assert not current.envelope.initial_input_applied
            observed.append(self.plugin_id)
            return self

    class Factory(InstalledFactory):
        def create_plugin(self, context):
            created.append(dict(context.configuration))
            return ObservingPlugin(context.plugin_id, "build-b")

    factory = Factory("build-b", NewConfiguration)
    catalog = build_harness_plugin_factory_catalog(explicit_factories=(factory,))

    async def respond(messages, _info):
        requests.append(messages)
        yield "prepared and executed"

    model_factory = Mock(spec=NativeModelFactory)
    model_factory.build.return_value = FunctionModel(stream_function=respond)
    settings = Settings(
        _env_file=None, build_version="build-b", worker_concurrency=1, worker_poll_interval_seconds=0.01
    )
    async with worker_runtime(
        interaction_sessions,
        interaction_object_store,
        tmp_path,
        monkeypatch,
        settings=settings,
        model_factory=model_factory,
        plugin_catalog=catalog,
    ) as (runtime, _shared):
        loop = runtime.execution_loop
        with fail_after(15):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(interaction_sessions) as session:
                        current = await session.get(RunRecord, run.id)
                        if current.status in {"completed", "failed"}:
                            assert current.status == "completed", current.failure_json
                            break
                    await sleep(0.01)
                await loop.drain()
                await loop.wait_stopped()
    state = (await states.read(ORGANIZATION_ID, run.id)).envelope
    assert state.effective_agent_config == initial.effective_agent_config
    assert state.prepared_plugins.plugins[0].config == {"label": "audit", "limit": expected_limit}
    assert len(requests) == 1
    prompts = [
        part.content
        for message in requests[0]
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    ]
    assert prompts == ["hello"]
    assert observed == ["audit"]
    assert created == [{"label": "audit", "limit": expected_limit}]
    async with short_session(interaction_sessions) as session:
        attempts = (
            await session.scalars(
                select(RunAttemptRecord)
                .where(RunAttemptRecord.run_id == run.id)
                .order_by(RunAttemptRecord.attempt_number)
            )
        ).all()
        assert len(attempts) == (2 if phase in {"prepared-before-input", "write-failure", "unconfirmed-write"} else 1)
        if len(attempts) == 2:
            assert attempts[0].harness_run_id is None
        assert attempts[-1].worker_build_id == "build-b"
