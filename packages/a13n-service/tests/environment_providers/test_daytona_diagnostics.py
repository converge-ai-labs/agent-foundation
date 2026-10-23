"""Diagnostics preserve native outcomes and exclude sensitive inputs."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.providers.environment.daytona import diagnostics
from a13n_service.providers.environments.daytona.desktop_build import _timed_command


@pytest.mark.anyio
@pytest.mark.parametrize("error", [None, RuntimeError("private-error"), asyncio.CancelledError("private-error")])
async def test_http_timings_preserve_outcome_without_sensitive_data(monkeypatch, error):
    request = AsyncMock(return_value={"token": "private-response"}, side_effect=error)
    logger = Mock()
    monkeypatch.setattr(diagnostics.NativeHTTP, "request", request)
    monkeypatch.setattr(diagnostics, "_logger", logger)
    http = object.__new__(diagnostics.TimedHTTP)
    args = {"body": {"command": "private-command"}, "headers": {"Authorization": "private-token"}}
    with diagnostics.measure("sandbox.create"):
        if error is None:
            assert await http.request("POST", "private-id/process/execute", **args) == {"token": "private-response"}
        else:
            with pytest.raises(type(error)) as raised:
                await http.request("POST", "private-id/process/execute", **args)
            assert raised.value is error
    fields = logger.info.call_args_list[0].kwargs["extra"]
    assert fields["stage"] == "api.POST.toolbox.exec"
    assert fields["parent_stage"] == "sandbox.create"
    assert fields["outcome"] == (
        "ok" if error is None else "interrupted" if isinstance(error, asyncio.CancelledError) else "error"
    )
    assert fields["duration_ms"] >= 0
    assert "private-" not in str(logger.info.call_args_list)
    assert diagnostics._operation.get() is None
    assert diagnostics._stage.get() == "transport"
    request.assert_awaited_once_with("POST", "private-id/process/execute", params=None, missing=False, **args)


@pytest.mark.parametrize("exit_code", [0, 23])
def test_build_timer_preserves_failure_and_emits_end_marker(tmp_path, monkeypatch, exit_code):
    # Debian has GNU date; supply its numeric output on macOS as well.
    date = tmp_path / "date"
    date.write_text("#!/bin/sh\nprintf '1230000000000\\n'\n")
    date.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin:/bin")
    command = _timed_command("test.step", f"exit {exit_code}")
    result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True)
    assert result.returncode == exit_code
    assert "stage=test.step event=start" in result.stdout
    assert f"duration_ms=0 exit_code={exit_code}" in result.stdout
