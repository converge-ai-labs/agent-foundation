from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from a13n_ui.errors import RuntimeGenerationError
from a13n_ui.runtime_generations import (
    RuntimeExitReason,
    RuntimeGenerationService,
    RuntimeGenerationState,
)
from a13n_ui.runtime_settings import RuntimeGenerationSettings

pytestmark = pytest.mark.anyio


def _settings() -> RuntimeGenerationSettings:
    return RuntimeGenerationSettings(
        startup_timeout_seconds=2.0,
        command_timeout_seconds=1.0,
        drain_timeout_seconds=1.0,
        terminate_timeout_seconds=1.0,
        kill_timeout_seconds=1.0,
    )


@asynccontextmanager
async def _service() -> AsyncIterator[RuntimeGenerationService]:
    service = RuntimeGenerationService(_settings())
    try:
        yield service
    finally:
        await service.close()


async def test_starts_with_distinct_process_and_runtime_readiness() -> None:
    async with _service() as service:
        active = await service.start()
        status = await service.status()

        assert active.state is RuntimeGenerationState.active
        assert active.process_id is not None
        assert active.readiness is not None
        assert active.readiness.protocol_version == "1"
        assert status.active_generation_id == active.generation_id
        assert status.candidate_generation_id is None


async def test_restart_promotes_candidate_before_old_runner_exits() -> None:
    async with _service() as service:
        first = await service.start()
        result = await service.restart()
        status = await service.status()

        assert result.previous_generation_id == first.generation_id
        assert result.active.generation_id != first.generation_id
        assert result.active.state is RuntimeGenerationState.active
        retained = {item.generation_id: item for item in status.generations}
        assert retained[first.generation_id].state is RuntimeGenerationState.exited
        assert retained[first.generation_id].exit_reason is RuntimeExitReason.graceful
        assert status.active_generation_id == result.active.generation_id


async def test_failed_generation_observations_remain_bounded() -> None:
    service = RuntimeGenerationService(
        RuntimeGenerationSettings(
            startup_timeout_seconds=0.2,
            command_timeout_seconds=0.1,
            drain_timeout_seconds=0.1,
            terminate_timeout_seconds=0.1,
            kill_timeout_seconds=0.1,
            retained_generations=2,
        ),
        command=(sys.executable, "-c", "raise SystemExit(9)"),
    )
    try:
        for _attempt in range(6):
            with pytest.raises(RuntimeGenerationError):
                await service.start()
        assert len(service._observations) == 2
        assert len((await service.status()).generations) == 2
    finally:
        await service.close()


async def test_failed_candidate_leaves_previous_runner_selected() -> None:
    async with _service() as service:
        first = await service.start()
        service._command = (sys.executable, "-c", "raise SystemExit(9)")

        with pytest.raises(RuntimeGenerationError):
            await service.restart()

        status = await service.status()
        assert status.active_generation_id == first.generation_id
        assert any(item.code == "runtime_start_failed" for item in status.diagnostics)


async def test_wrong_launch_token_never_reaches_ready() -> None:
    script = """
import asyncio, json, os
async def main():
    reader, writer = await asyncio.open_connection(
        os.environ['A13N_UI_RUNNER_CONTROL_HOST'],
        int(os.environ['A13N_UI_RUNNER_CONTROL_PORT']),
    )
    value = {
        'version': '1',
        'type': 'HELLO',
        'generation_id': os.environ['A13N_UI_RUNNER_GENERATION_ID'],
        'token': 'wrong-token',
    }
    writer.write((json.dumps(value) + '\\n').encode())
    await writer.drain()
    await asyncio.sleep(10)
asyncio.run(main())
"""
    service = RuntimeGenerationService(
        RuntimeGenerationSettings(
            startup_timeout_seconds=0.2,
            command_timeout_seconds=0.1,
            drain_timeout_seconds=0.1,
            terminate_timeout_seconds=0.2,
            kill_timeout_seconds=0.2,
        ),
        command=(sys.executable, "-c", script),
    )
    try:
        with pytest.raises(RuntimeGenerationError) as failed:
            await service.start()
        assert failed.value.code == "runtime_start_failed"
        assert (await service.status()).active_generation_id is None
    finally:
        await service.close()


async def test_drain_timeout_escalates_without_losing_new_active_runner() -> None:
    fixture = Path(__file__).with_name("fixture_runtime_runner.py")
    service = RuntimeGenerationService(
        RuntimeGenerationSettings(
            startup_timeout_seconds=2.0,
            command_timeout_seconds=0.1,
            drain_timeout_seconds=0.1,
            terminate_timeout_seconds=0.2,
            kill_timeout_seconds=0.2,
        ),
        command=(sys.executable, str(fixture)),
        environment={"A13N_UI_TEST_RUNNER_BEHAVIOR": "stall_drain"},
    )
    try:
        first = await service.start()
        restarted = await service.restart()
        status = await service.status()

        retained = {item.generation_id: item for item in status.generations}
        assert status.active_generation_id == restarted.active.generation_id
        assert retained[first.generation_id].exit_reason in {
            RuntimeExitReason.terminated,
            RuntimeExitReason.killed,
        }
        assert any(item.code == "runtime_drain_failed" for item in status.diagnostics)
    finally:
        await service.close()


async def test_cancelled_candidate_is_aborted_without_changing_active_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _service() as service:
        first = await service.start()
        candidate_ready = asyncio.Event()

        async def wait_for_cancellation(candidate: object) -> None:
            candidate_ready.set()
            await asyncio.sleep(60)

        monkeypatch.setattr(service, "_prepare_and_activate", wait_for_cancellation)
        restart = asyncio.create_task(service.restart())
        await candidate_ready.wait()
        restart.cancel()
        with pytest.raises(asyncio.CancelledError):
            await restart

        status = await service.status()
        assert status.active_generation_id == first.generation_id
        assert status.candidate_generation_id is None
        assert any(
            item.generation_id != first.generation_id
            and item.state is RuntimeGenerationState.exited
            and item.exit_reason is RuntimeExitReason.aborted
            for item in status.generations
        )


async def test_unexpected_runner_exit_removes_active_routing() -> None:
    async with _service() as service:
        active = await service.start()
        assert service._active is not None
        service._active.process.kill()
        await service._active.process.wait()
        await asyncio.sleep(0)

        status = await service.status()
        assert status.active_generation_id is None
        retained = {item.generation_id: item for item in status.generations}
        assert retained[active.generation_id].exit_reason is RuntimeExitReason.unexpected
        assert any(item.code == "runtime_unexpected_exit" for item in status.diagnostics)

        await service.restart()
        after_restart = {item.generation_id: item for item in (await service.status()).generations}
        assert after_restart[active.generation_id].exit_reason is RuntimeExitReason.unexpected


async def test_restart_and_close_are_serialized_and_close_is_idempotent() -> None:
    service = RuntimeGenerationService(_settings())
    await service.start()

    restart = asyncio.create_task(service.restart())
    await asyncio.sleep(0)
    close = asyncio.create_task(service.close())
    result = await restart
    await close
    await service.close()

    assert result.active.state is RuntimeGenerationState.active
    assert (await service.status()).active_generation_id is None
