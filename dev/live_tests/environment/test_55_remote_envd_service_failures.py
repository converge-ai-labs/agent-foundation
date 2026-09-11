"""Real remote carrier loss and daemon restart through Service and Harness."""

import asyncio
import os
import signal
from pathlib import Path

import pytest

from ..infrastructure.management_support import last_tool_result
from ..infrastructure.round_two_lab import REPOSITORY, open_lab
from .environment_backends import EnvironmentBackend

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", params=["http-envd", "websocket-envd"])
async def remote_service(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for real remote Service faults")
    binary = Path(os.environ.get("A13N_ENVD_TEST_BINARY", REPOSITORY / "target/debug/a13n-envd")).resolve()
    assert binary.is_file(), "Build a13n-envd before running live remote Service tests"
    async with open_lab(suite="management", websocket_envd=request.param == "websocket-envd") as lab:
        yield EnvironmentBackend(lab, request.param, binary, network_faults=True)


def shell(script):
    return {
        "tool": "shell_exec",
        "arguments": {"command": script, "cwd": "/workspace", "yield_time_seconds": 10},
    }


@pytest.mark.parametrize("fault", ["connection-loss", "daemon-restart"])
async def test_service_reports_unknown_effect_without_replay_and_accepts_fresh_use(remote_service, fault):
    backend, journey = remote_service, remote_service.journey
    live = journey.live
    async with backend.target() as target:
        environment = await target.allocate()
        case = await journey.case(steps=[shell("printf ONCE >> effect; sleep 8; printf DONE")])
        receipt = await journey.start(case, environment={"environment_id": environment["id"]})
        effect = target.root / "effect"
        await live.wait(lambda: asyncio.to_thread(effect.exists), bool, "Dispatched native command changed state")
        before = await live.request("GET", f"/api/v1/environments/{environment['id']}")
        original_daemon = target.process
        connections_before = target.proxy.connections
        if fault == "connection-loss":
            target.proxy.cut()
        else:
            original_daemon.send_signal(signal.SIGKILL)
            await asyncio.wait_for(original_daemon.wait(), 10)
        try:
            result = await live.finish(receipt["run_id"], outcome="failed")
            assert result["failure"]["code"] == "attempt_execution_failed", result
            failure = last_tool_result(journey.observations(case)[-1])
            assert failure["ok"] is False, failure
            # Loss may hit the current status read or fence the next read.
            assert failure["error"]["code"] in {"environment_unavailable", "environment_provider_failure"}, failure
            assert effect.read_bytes() == b"ONCE"
        finally:
            target.proxy.restore()
        if fault == "daemon-restart" or backend.kind == "http-envd":
            # HTTP cannot take over an abandoned admitted Session. The fixture
            # acts as the external operator and restarts that owned daemon.
            await target.restart_daemon()
        else:
            assert original_daemon.returncode is None, "Service must preserve the external daemon"
        if backend.kind == "websocket-envd":
            # The daemon backs off up to 30 seconds after prolonged outage;
            # wait for its actual new carrier before requesting a fresh lease.
            await live.wait(
                lambda: asyncio.to_thread(lambda: target.proxy.connections),
                lambda count: count > connections_before,
                "External daemon reconnected after backoff",
            )
        recovery = await journey.case(steps=[shell("cat effect; printf RECOVERED > recovered")])
        receipt = await journey.start(recovery, environment={"environment_id": environment["id"]})
        recovered = await live.finish(receipt["run_id"])
        assert recovered["status"] == "completed"
        assert last_tool_result(journey.observations(recovery)[-1])["ok"] is True
        assert effect.read_bytes() == b"ONCE"
        assert (target.root / "recovered").read_bytes() == b"RECOVERED"
        after = await live.request("GET", f"/api/v1/environments/{environment['id']}")
        assert after["generation"] == before["generation"]
        assert after["provider_id"] == before["provider_id"]
        assert target.process.returncode is None
