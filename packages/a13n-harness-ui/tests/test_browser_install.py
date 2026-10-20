from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui import browser_install as module
from a13n_harness_ui.browser_install import BrowserInstaller
from a13n_harness_ui.browser_setup import BrowserInstallStep, BrowserSetup

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(os.name != "posix", reason="Linux Host installer process tests")]


def plan(*commands):
    return BrowserSetup(
        platform="linux",
        supported=True,
        ready=False,
        installable=True,
        steps=tuple(
            BrowserInstallStep(title=f"Step {index}", command=command) for index, command in enumerate(commands)
        ),
    )


async def test_streams_real_command_output_and_checks_readiness(monkeypatch):
    monkeypatch.setattr(
        module, "inspect_browser", AsyncMock(return_value=BrowserSetup(platform="linux", supported=True, ready=True))
    )
    installer = BrowserInstaller()
    authority = AsyncMock()
    installer.start(plan("printf 'package installed\\n'"), authority)
    assert installer.running and installer.state.status == "installing"
    await installer.task
    assert not installer.running
    assert installer.state.status == "succeeded"
    assert "package installed" in installer.state.output
    assert authority.await_count == 2


@pytest.mark.parametrize(
    ("code", "detail"), [(7, "Review the installation output"), (137, "Check available Host memory")]
)
async def test_failed_command_stops_remaining_plan_and_preserves_bounded_output(code, detail):
    installer = BrowserInstaller()
    installer.start(plan(f"printf 'package unavailable\\n'; exit {code}", "printf 'must not run'"), AsyncMock())
    await installer.task
    assert installer.state.status == "failed"
    assert f"exit {code}" in (installer.state.error or "")
    assert detail in (installer.state.error or "")
    assert "package unavailable" in installer.state.output
    assert "must not run" not in installer.state.output
    installer.output("x" * 20000)
    assert len(installer.state.output) == 16384


async def test_verification_cannot_report_success_with_missing_components(monkeypatch):
    monkeypatch.setattr(
        module,
        "inspect_browser",
        AsyncMock(
            return_value=BrowserSetup(platform="linux", supported=True, ready=False, message="Browser not found")
        ),
    )
    installer = BrowserInstaller()
    installer.start(plan("true"), AsyncMock())
    await installer.task
    assert installer.state.status == "failed"
    assert "Browser not found" in (installer.state.error or "")


async def test_rechecks_authority_between_steps_and_can_retry_after_failure(monkeypatch):
    installer = BrowserInstaller()
    authority = AsyncMock(side_effect=[None, RuntimeError("Authority revoked")])
    installer.start(plan("true", "false"), authority)
    await installer.task
    assert installer.state.status == "failed"
    assert installer.state.error == "Authority revoked"
    monkeypatch.setattr(
        module, "inspect_browser", AsyncMock(return_value=BrowserSetup(platform="linux", supported=True, ready=True))
    )
    installer.start(plan("true"), AsyncMock())
    await installer.task
    assert installer.state.status == "succeeded" and installer.state.error is None


async def test_shutdown_terminates_install_process_group(monkeypatch):
    original = module.asyncio.create_subprocess_exec
    started = asyncio.Event()
    processes = []

    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    installer = BrowserInstaller()
    installer.start(plan("sleep 120"), AsyncMock())
    await started.wait()
    await asyncio.wait_for(installer.close(), 8)
    assert processes[0].returncode is not None
    assert installer.state.status == "failed"
    assert "interrupted" in (installer.state.error or "")
