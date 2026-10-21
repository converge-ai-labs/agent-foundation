"""Desktop timings remain isolated and do not disclose provider or guest data."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.modal import diagnostics
from a13n_harness.providers.environment.modal.shared import execute_command


@pytest.mark.anyio
async def test_command_failure_records_stage_without_command_output_or_exception(caplog):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    target = SimpleNamespace(
        exec=SimpleNamespace(
            aio=AsyncMock(
                return_value=SimpleNamespace(
                    stdout=SimpleNamespace(
                        read=SimpleNamespace(aio=AsyncMock(side_effect=RuntimeError("secret-output")))
                    )
                )
            )
        )
    )
    with pytest.raises(EnvironmentProviderError), diagnostics.desktop_operation("dtop-example", "grant"):
        await execute_command(target, ["secret-command"], 25)
    assert [(r.stage, r.outcome) for r in caplog.records] == [
        ("command_start", "ok"),
        ("command_stdout", "error"),
        ("dispatch", "error"),
    ]
    assert all(r.desktop_operation_id == "dtop-example" and r.duration_ms >= 0 for r in caplog.records)
    assert "secret" not in repr([r.__dict__ for r in caplog.records])
    with diagnostics.measure("outside"):
        pass
    assert len(caplog.records) == 3


@pytest.mark.anyio
async def test_concurrent_dispatches_keep_their_operation_identity(caplog):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first():
        with diagnostics.desktop_operation("dtop-first", "describe"), diagnostics.measure("sandbox_from_id"):
            entered.set()
            await release.wait()

    async def second():
        await entered.wait()
        with diagnostics.desktop_operation("dtop-second", "renew"), diagnostics.measure("command_start"):
            release.set()

    await asyncio.gather(first(), second())
    assert {(r.desktop_operation_id, r.desktop_operation, r.stage) for r in caplog.records} == {
        ("dtop-first", "describe", "sandbox_from_id"),
        ("dtop-first", "describe", "dispatch"),
        ("dtop-second", "renew", "command_start"),
        ("dtop-second", "renew", "dispatch"),
    }


def test_cancellation_is_logged_and_propagated(caplog):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    with pytest.raises(asyncio.CancelledError), diagnostics.desktop_operation("dtop-cancelled", "grant"):
        with diagnostics.measure("command_start"):
            raise asyncio.CancelledError("secret-cancellation")
    assert [r.outcome for r in caplog.records] == ["interrupted", "interrupted"]
    assert "secret-cancellation" not in caplog.text
