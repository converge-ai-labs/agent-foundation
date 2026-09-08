"""Real Worker execution resumes a retained checkpoint using a replacement build."""

import json
from importlib.metadata import EntryPoint
from unittest.mock import Mock

import pytest
from a13n_harness import AgentContext, HarnessState
from a13n_harness.plugin_factories import build_harness_plugin_factory_catalog
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
from a13n_service.agents.domain import PluginSelection
from a13n_service.digests import digest_request
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.settings import Settings
from a13n_service.storage import short_session
from anyio import create_task_group, fail_after, sleep
from pydantic import BaseModel
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from tests.agents.test_installed_plugins import Configuration, InstalledFactory, InstalledPlugin
from tests.hooks.support import seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer

from . import test_attempt_execution as acceptance
from .conftest import NOW, ORGANIZATION_ID
from .worker_helpers import worker_runtime


class Counter(BaseModel):
    value: int


class ResumingPlugin(InstalledPlugin):
    def __init__(self, plugin_id, state_version, observed):
        super().__init__(plugin_id, "build-b")
        self.state_version = state_version
        self.observed = observed

    async def for_run(self, context: AgentContext):
        state = await context.state.read(self.plugin_id, Counter, version=self.state_version)
        assert state is not None
        self.observed.append((self.code_release, state.value))
        await context.state.write(self.plugin_id, Counter(value=state.value + 1), version=self.state_version)
        return self


class ResumingFactory(InstalledFactory):
    def __init__(self, case):
        super().__init__("build-b")
        self.case = case
        self.observed = []
        if case == "configuration":

            class IncompatibleConfiguration(Configuration):
                new_required_field: str

            self.configuration_type = IncompatibleConfiguration

    def create_plugin(self, context):
        plugin = ResumingPlugin(context.plugin_id, "2" if self.case == "state" else "1", self.observed)
        self.created.append(plugin)
        return plugin


class AmbientFactory(InstalledFactory):
    @classmethod
    def plugin_key(cls) -> str:
        return "test.ambient"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case", "ambient"),
    [(case, None) for case in ("compatible", "configuration", "state", "missing")]
    + [("compatible", ambient) for ambient in ("json", "file", "invalid-json", "invalid-file", "invalid-enabled")],
)
async def test_new_worker_uses_retained_configuration_and_validates_state(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, case, ambient
):
    ambient_executions = []
    if ambient is not None:

        class AmbientPlugin(InstalledPlugin):
            async def for_run(self, context):
                ambient_executions.append(self.plugin_id)
                return self

        monkeypatch.setattr(
            AmbientFactory, "create_plugin", lambda self, context: AmbientPlugin(context.plugin_id, "ambient")
        )
        monkeypatch.setattr(
            "a13n_harness.plugin_factories._entry_points",
            lambda: (
                EntryPoint(name="test.ambient", value=f"{__name__}:AmbientFactory", group="a13n_harness.plugins"),
            ),
        )
        monkeypatch.setenv("A13N_HARNESS_PLUGIN_CONFIG_ENABLED", "invalid" if ambient == "invalid-enabled" else "true")
        monkeypatch.delenv("A13N_HARNESS_PLUGIN_CONFIG_JSON", raising=False)
        monkeypatch.delenv("A13N_HARNESS_PLUGIN_CONFIG_FILE", raising=False)
        document = json.dumps(
            {
                "schema_version": "1",
                "plugins": [
                    {"plugin_id": "ambient", "plugin_key": "test.ambient", "enabled": True, "configuration": {}}
                ],
            }
        )
        if ambient in ("file", "invalid-file"):
            config_path = tmp_path / "ambient.json"
            if ambient == "file":
                config_path.write_text(document)
            monkeypatch.setenv("A13N_HARNESS_PLUGIN_CONFIG_FILE", str(config_path))
        else:
            monkeypatch.setenv("A13N_HARNESS_PLUGIN_CONFIG_JSON", "not-json" if ambient == "invalid-json" else document)

    original_config = acceptance.effective_agent_config()
    config = original_config.model_copy(
        update={
            "resolved_plugins": (
                PluginSelection(instance_name="audit", plugin_key="test.audit", config={"label": "audit", "limit": 5}),
            )
        }
    )
    config = config.model_copy(
        update={
            "content_digest": digest_request(config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}))
        }
    )
    monkeypatch.setattr(acceptance, "effective_agent_config", lambda: config)
    await seed_hook_actor_access(interaction_sessions)
    states, run, initial = await acceptance._accept_root(interaction_sessions, interaction_object_store)
    old = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, acceptance._worker(build_id="build-a")
    )
    assert isinstance(old, ClaimedAttempt)
    authority = acceptance._authority(old)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    decision = await execution.commit_preparation_success(authority)
    await execution.enter_harness(authority, preparation=decision, harness_run_id="old-build-harness")
    checkpoint = initial.model_copy(
        update={
            "checkpoint_seq": 1,
            "checkpoint_kind": "progress",
            "last_checkpoint_run_attempt_id": old.attempt.id,
            "last_checkpoint_fence": old.attempt.attempt_number,
            "harness": HarnessState.new(
                thread_id=initial.thread_id,
                message_history=(ModelRequest(parts=[UserPromptPart("hello")]),),
                agent_context_state=AgentContextStateSnapshot(
                    entries={"audit": CapabilityState(version="1", data={"value": 7})}
                ),
            ),
        }
    )
    await execution.publish_checkpoint(authority, states, await states.read(ORGANIZATION_ID, run.id), checkpoint)
    await execution.yield_attempt(authority, reason=RunAttemptYieldReason.service_drain)

    factory = ResumingFactory(case)
    catalog = build_harness_plugin_factory_catalog(explicit_factories=() if case == "missing" else (factory,))
    requests = []

    async def respond(messages, _info):
        requests.append(messages)
        yield "resumed by build-b"

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
        with fail_after(10):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(interaction_sessions) as session:
                        current = await session.get(RunRecord, run.id)
                        if current.status in {"completed", "failed"}:
                            assert current.status == ("completed" if case == "compatible" else "failed"), (
                                current.failure_json
                            )
                            break
                    await sleep(0.01)
                await loop.drain()
                await loop.wait_stopped()
    current_state = (await states.read(ORGANIZATION_ID, run.id)).envelope
    assert current_state.effective_agent_config == initial.effective_agent_config
    assert current_state.harness.agent_context_state.entries["audit"].data == {
        "value": 8 if case == "compatible" else 7
    }
    assert ambient_executions == []
    assert factory.observed == ([("build-b", 7)] if case == "compatible" else [])
    assert len(requests) == (1 if case == "compatible" else 0)
    async with short_session(interaction_sessions) as session:
        attempts = (
            await session.scalars(
                select(RunAttemptRecord)
                .where(RunAttemptRecord.run_id == run.id)
                .order_by(RunAttemptRecord.attempt_number)
            )
        ).all()
        assert [a.worker_build_id for a in attempts] == ["build-a", "build-b"]
        assert attempts[0].status == "yielded"
        assert attempts[1].start_reason == "planned_handoff"
