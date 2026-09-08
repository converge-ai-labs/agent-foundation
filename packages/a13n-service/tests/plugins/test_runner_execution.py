from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.plugins import runner_process
from a13n_service.plugins.runner_bootstrap import BootstrappedPluginRuntime
from a13n_service.plugins.runtime import PluginRuntimeLock, default_runtime_target, installed_harness_version
from a13n_service.process import runner as runner_resources
from a13n_service.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("stop", ["DRAIN", "RETIRE"])
async def test_runner_ready_is_warm_and_drain_acknowledges_joined_execution(monkeypatch, tmp_path, stop):
    lock = PluginRuntimeLock(
        mode="runner",
        runtime_target=default_runtime_target(),
        worker_release="test",
        harness_version=installed_harness_version(),
        digest="0" * 64,
    )
    runtime = BootstrappedPluginRuntime(lock, HarnessPluginFactoryCatalog(()))
    worker = Mock()
    worker.execution_loop.drain = AsyncMock()
    worker.execution_loop.retire_if_idle = AsyncMock(side_effect=[False, True])
    events = []

    @asynccontextmanager
    async def open_worker(settings, selected):
        assert isinstance(settings, Settings)
        assert selected is runtime
        events.append("worker_started")
        try:
            yield worker
        finally:
            events.append("worker_joined")

    monkeypatch.setattr(runner_resources, "open_runner_worker", open_worker)
    monkeypatch.setattr(runner_process, "bootstrap_materialized_runtime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(runner_process, "_consume_control_environment", lambda: ("127.0.0.1", 1234, "token", "gen"))
    monkeypatch.setenv("A13N_SERVICE_RUNNER_EXECUTION_SETTINGS", Settings(_env_file=None).model_dump_json())
    writer = Mock()
    writer.wait_closed = AsyncMock()
    monkeypatch.setattr(runner_process.asyncio, "open_connection", AsyncMock(return_value=(Mock(), writer)))
    commands = iter(
        [
            {"type": "WELCOME", "generation": "gen"},
            {"type": "ACTIVATE", "runtime_version": 1},
            {"type": "ACTIVATE", "runtime_version": 1},
            *(
                [{"type": "RETIRE"}, {"type": "RETIRE"}]
                if stop == "RETIRE"
                else [{"type": "DRAIN", "reason": "service_drain"}]
            ),
            {"type": "SHUTDOWN"},
        ]
    )

    async def read(reader):
        command = next(commands)
        if command["type"] == "ACTIVATE" and "ACTIVE" not in events:
            assert events == ["HELLO", "READY"]
        return command

    async def write(writer, message_type, **fields):
        if message_type == "DRAINED":
            assert events[-1] == "worker_joined"
        if message_type == "RETIRED":
            assert (events[-1] == "worker_joined") is fields["retired"]
        events.append(message_type)

    monkeypatch.setattr(runner_process, "read_runner_message", read)
    monkeypatch.setattr(runner_process, "write_runner_message", write)
    await runner_process.run_runner_process(tmp_path, lock.digest)
    assert events == [
        "HELLO",
        "READY",
        "worker_started",
        "ACTIVE",
        "ACTIVE",
        *(["RETIRED", "worker_joined", "RETIRED"] if stop == "RETIRE" else ["worker_joined", "DRAINED"]),
        "EXITING",
    ]
    if stop == "DRAIN":
        worker.execution_loop.drain.assert_awaited_once_with(RunAttemptYieldReason.service_drain)
    else:
        assert worker.execution_loop.retire_if_idle.await_count == 2
    writer.close.assert_called_once()


async def test_runner_rejects_process_local_storage_before_opening_resources(monkeypatch):
    storage = Mock()
    monkeypatch.setattr(runner_resources, "open_storage", storage)
    with pytest.raises(ValueError, match="shared PostgreSQL, Redis, and S3"):
        async with runner_resources.open_runner_worker(Settings(_env_file=None, object_backend="local"), Mock()):
            pytest.fail("A Runner must not share the single-process object adapter")
    storage.assert_not_called()
