"""Failure terminalization observes accepted mounts without installing candidates."""

from unittest.mock import AsyncMock

import pytest
from a13n_harness import RunBindings, SafeFailure
from a13n_service.environments.mount_runtime import RunMountRuntime
from a13n_service.interactions.run_control import RunAttemptControl
from pydantic_ai.models.function import FunctionModel

from .conftest import initial_state
from .test_run_control import (
    _context,
    _outcome_adapter,
    _RecordingAttemptExecution,
    _RecordingTerminalCommitter,
    _RecordingThreadInbox,
    _run,
    _stored_state,
)

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("harness_entered", [False, True])
async def test_failure_reads_mount_facts_before_committing(interaction_object_store, harness_entered):
    trace = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    control = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace),
    )
    mounts = AsyncMock(spec=RunMountRuntime)
    await control.bind_environment_mounts(mounts)
    terminal = _RecordingTerminalCommitter()

    def failed_model(messages, info):
        raise RuntimeError("injected model failure")

    if harness_entered:
        result = await _run(
            control=control, bindings=RunBindings.embedded(), state=stored, model=FunctionModel(failed_model)
        )
        assert result.status == "failed"
    mounts.reset_mock()

    async def observe():
        assert not terminal.failures
        trace.append("mounts:read")

    mounts.reconcile.side_effect = observe
    if harness_entered:
        await control.finalize(result, adapter=_outcome_adapter(interaction_object_store), committer=terminal)
    else:
        await control.fail_execution(terminal, SafeFailure(code="execution_failed", message="failed"))
    mounts.reconcile.assert_awaited_once()
    mounts.apply.assert_not_awaited()
    assert len(terminal.failures) == 1
    assert "mounts:read" in trace
