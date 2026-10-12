"""Child admission persists its Thread before Environment preparation can publish state."""

import asyncio
import json
import socket
from contextlib import AsyncExitStack

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection
from a13n_harness_ui.errors import EnvironmentLifecycleError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage.database import short_session
from a13n_harness_ui.storage.models import EnvironmentBindingRecord
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus
from anyio import Event, fail_after, sleep_forever
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import select

from .test_builtin_subagents import _source
from .test_device_runtime import TOKEN, daemon, device_path, requires_envd, resource

pytestmark = pytest.mark.anyio


@pytest.fixture
async def delegation_app(tmp_path, monkeypatch, request):
    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    monkeypatch.setenv("TEST_KEY", "test-model-key")
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)
    calls = {"parent": 0, "child": 0}

    async def build(self, recipe, authentication):
        async def stream(messages, info):
            if "delegate" not in {tool.name for tool in info.function_tools}:
                calls["child"] += 1
                yield "Child completed."
                return
            calls["parent"] += 1
            if calls["parent"] == 1:
                yield {
                    0: DeltaToolCall(
                        name="delegate",
                        json_args=json.dumps({"subagent_name": "explorer", "prompt": "Bounded task"}),
                        tool_call_id="delegate-1",
                    )
                }
            elif calls["parent"] == 2:
                yield {
                    0: DeltaToolCall(name="wait_subagent", json_args='{"timeout_seconds": 10}', tool_call_id="wait-1")
                }
            else:
                yield "Parent completed."

        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "_api_key_model", build)
    async with AsyncExitStack() as stack:
        live_device = getattr(request, "param", False)
        endpoint = "http://127.0.0.1:1"
        remote_root = "/remote"
        if live_device:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
            root = await stack.enter_async_context(daemon(tmp_path, "http", endpoint=endpoint))
            remote_root = device_path(root)
        path = _source(tmp_path, ("explorer",))
        (tmp_path / "devices").mkdir()
        recipe = resource("http", endpoint)
        (tmp_path / "devices/build.yaml").write_text(recipe.model_dump_json())
        app = await stack.enter_async_context(
            open_harness_ui_app(
                HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False),
                configuration_path=path,
            )
        )
        if live_device:
            with fail_after(10):
                while not (await app.device_info(recipe.id)).available:
                    await asyncio.sleep(0.03)
        thread = await app.create_thread(
            defaults=NewThreadDefaults(
                agent_id="agent-main",
                local_roots=(str(tmp_path),),
                environment_profile_id="environment-native",
                environment_bindings=(
                    EnvironmentBindingSelection(device_id=recipe.id, working_directory=remote_root, alias="build"),
                ),
                default_environment="workspace",
            )
        )
        yield app, thread, calls


async def _assert_retained_binding(app, parent, child_id):
    child = await app._store.threads.get(child_id)
    assert child is not None and child.parent_thread_id == parent.thread_id
    assert child.configuration.environment_bindings == parent.configuration.environment_bindings
    assert child.configuration.default_environment == "workspace"
    async with short_session(app._store.database.sessions) as session:
        digest = await session.scalar(
            select(EnvironmentBindingRecord.state_digest).where(
                EnvironmentBindingRecord.thread_id == child_id,
                EnvironmentBindingRecord.alias == "build",
            )
        )
    assert digest is not None


@pytest.mark.parametrize(
    "delegation_app",
    [pytest.param(False, id="unused-device"), pytest.param(True, id="http-envd", marks=requires_envd)],
    indirect=True,
)
async def test_delegation_with_inherited_remote_binding_completes(delegation_app):
    app, parent, calls = delegation_app
    receipt = await app.submit_thread(thread_id=parent.thread_id, prompt="Delegate a bounded task")
    result = await app.wait_root_operation(receipt.receipt_id)
    assert result.status is RootOperationStatus.completed, result
    page = await app.query_child_executions(parent_thread_id=parent.thread_id)
    assert len(page.executions) == 1
    execution = page.executions[0]
    assert execution.persisted_status == "succeeded"
    await _assert_retained_binding(app, parent, execution.child_thread_id)
    assert calls == {"parent": 3, "child": 1}


@pytest.mark.parametrize("stop", ["failure", "cancellation"])
async def test_unsuccessful_child_preparation_retains_state_without_starting_execution(
    delegation_app, monkeypatch, stop
):
    app, parent, calls = delegation_app
    service = app._subagent_operator._environments
    prepare = service.prepare
    prepared = Event()
    children = []

    async def interrupted(composition):
        plan = await prepare(composition)
        if composition.thread_id != parent.thread_id:
            children.append(composition.thread_id)
            prepared.set()
            if stop == "failure":
                raise EnvironmentLifecycleError("Child preparation failed.", code="test_child_preparation_failed")
            await sleep_forever()
        return plan

    monkeypatch.setattr(service, "prepare", interrupted)
    receipt = await app.submit_thread(thread_id=parent.thread_id, prompt="Delegate a bounded task")
    if stop == "cancellation":
        with fail_after(10):
            await prepared.wait()
        assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
    result = await app.wait_root_operation(receipt.receipt_id)
    expected = RootOperationStatus.failed if stop == "failure" else RootOperationStatus.cancelled
    assert result.status is expected, result
    assert len(children) == 1
    await _assert_retained_binding(app, parent, children[0])
    assert (await app.query_child_executions(parent_thread_id=parent.thread_id)).executions == ()
    assert not await app._subagent_operator.active_execution_ids()
    assert calls == {"parent": 1, "child": 0}
