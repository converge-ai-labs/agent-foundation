from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from a13n_service.assets.service import AssetService
from a13n_service.endpoint_policy import EndpointPolicyError
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.harness_runtime import ImmediateHarnessInput
from a13n_service.interactions.input import AgentInputError, AssetBinarySource, PathBinarySource, UrlBinarySource
from a13n_service.interactions.input_runtime import AttemptInputRuntime
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.state import RunPayloadEnvelope

from tests.lifecycle_support import test_lifecycle_writer

from ..models.conftest import actor
from .conftest import NOW, ORGANIZATION_ID, effective_agent_config
from .test_preparation import _claimed

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def _runtime(sessions, objects, monkeypatch):
    states, run, authority = await _claimed(sessions, objects)
    monkeypatch.setattr("a13n_service.interactions.input_runtime.utc_now", lambda: NOW)
    assets = Mock(spec=AssetService)
    payloads = RunPayloadStore(objects)
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(
            httpx2.AsyncClient(
                transport=httpx2.MockTransport(
                    lambda request: httpx2.Response(200, content=b"test", headers={"content-type": "text/plain"})
                )
            )
        )
        runtime = AttemptInputRuntime(
            sessions, payloads, states, assets, http, stack, lambda: authority, max_binary_bytes=4
        )
        yield runtime, run, authority, payloads, assets


@pytest.mark.parametrize("storage", ["inline", "object"])
async def test_materializes_verified_root_input_after_heartbeat(
    interaction_sessions, interaction_object_store, monkeypatch, storage
):
    async with _runtime(interaction_sessions, interaction_object_store, monkeypatch) as (
        runtime,
        run,
        authority,
        payloads,
        _,
    ):
        value = {"schema_version": "1", "content": [{"type": "text", "text": "hello"}]}
        if storage == "object":
            reference = await payloads.create(
                ORGANIZATION_ID,
                RunPayloadEnvelope(
                    schema_version="1", run_id=run.id, payload_kind="input", payload_schema_version="1", payload=value
                ),
            )
            run = run.model_copy(update={"input": None, "input_object": reference})
        else:
            run = run.model_copy(update={"input": value})
        await AttemptExecutionService(
            interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()
        ).heartbeat(authority, lease_duration=timedelta(seconds=30))
        source, deferred = await runtime.prepare(
            run, actor(), effective_agent_config().input_adapter, input_pending=True
        )
        assert source == ImmediateHarnessInput("hello")
        assert deferred is None


async def test_applied_input_is_not_read_again(interaction_sessions, interaction_object_store, monkeypatch):
    async with _runtime(interaction_sessions, interaction_object_store, monkeypatch) as (runtime, run, _, _, assets):
        source, deferred = await runtime.prepare(
            run.model_copy(update={"input": {"invalid": "unread"}}),
            actor(),
            effective_agent_config().input_adapter,
            input_pending=False,
        )
        assert source == ImmediateHarnessInput()
        assert deferred is None
        assets.require_for_use.assert_not_called()
        environment = Mock()
        await runtime.for_run(SimpleNamespace(deps=SimpleNamespace(environment=environment)))
        environment.files.mkdir = AsyncMock()
        environment.files.write_bytes_stream = AsyncMock()
        await runtime.replace("/workspace/.a13n/inputs/input-1/content-0", _chunks())
        environment.files.write_bytes_stream.assert_awaited_once()


async def _chunks():
    yield b"test"


async def test_private_url_is_rejected_before_request(interaction_sessions, interaction_object_store, monkeypatch):
    async with _runtime(interaction_sessions, interaction_object_store, monkeypatch) as (runtime, _, _, _, _):
        with pytest.raises(EndpointPolicyError):
            await runtime.open(UrlBinarySource(url="http://127.0.0.1/secret"), max_bytes=4)


async def test_asset_reader_requires_use_and_bounds_size_before_acquisition(
    interaction_sessions, interaction_object_store, monkeypatch
):
    async with _runtime(interaction_sessions, interaction_object_store, monkeypatch) as (runtime, run, _, _, assets):
        await runtime.prepare(run, actor(), effective_agent_config().input_adapter, input_pending=False)
        assets.require_for_use.return_value = SimpleNamespace(size_bytes=5)
        with pytest.raises(AgentInputError, match="size limit"):
            await runtime.open(AssetBinarySource(asset_id="ast_1234567890abcdef"), max_bytes=4)
        assets.prepare_content_for_use.assert_not_called()


async def test_path_input_cannot_select_another_mount(interaction_sessions, interaction_object_store, monkeypatch):
    async with _runtime(interaction_sessions, interaction_object_store, monkeypatch) as (runtime, _, _, _, _):
        with pytest.raises(AgentInputError, match="Environment"):
            await runtime.open(PathBinarySource(environment_binding="another", path="file.txt"), max_bytes=4)
