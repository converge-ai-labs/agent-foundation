"""Host fallback is limited to definite pre-acceptance handoff rejection."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from a13n_harness.errors import RunError
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.steering import steer_stream

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("immediate", [False, True])
async def test_steering_preserves_successful_delivery_mode(immediate):
    stream = SimpleNamespace(steer=AsyncMock(return_value="enqueue-test"))
    assert await steer_stream(stream, "Guidance", immediate=immediate) == "enqueue-test"
    stream.steer.assert_awaited_once_with("Guidance", immediate=immediate)


@pytest.mark.parametrize(
    ("immediate", "error"),
    [
        (False, RunError("Unsupported", code="tool_handoff_unsupported")),
        (True, RunError("Inactive", code="run_not_active")),
        (True, RunError("Unbound", code="tool_execution_unbound")),
        (True, RuntimeError("Unknown outcome")),
    ],
)
async def test_steering_does_not_retry_other_failures(immediate, error):
    stream = SimpleNamespace(steer=AsyncMock(side_effect=error))
    with pytest.raises(type(error)) as caught:
        await steer_stream(stream, "Guidance", immediate=immediate)
    assert caught.value is error
    stream.steer.assert_awaited_once_with("Guidance", immediate=immediate)


async def test_steering_does_not_retry_failed_queued_fallback():
    failure = RunError("Ended before enqueue", code="run_not_active")
    stream = SimpleNamespace(
        steer=AsyncMock(side_effect=[RunError("Unsupported", code="tool_handoff_unsupported"), failure])
    )
    with pytest.raises(RunError) as caught:
        await steer_stream(stream, "Guidance", immediate=True)
    assert caught.value is failure
    assert stream.steer.await_args_list == [call("Guidance", immediate=True), call("Guidance", immediate=False)]


async def test_child_steering_uses_queued_fallback_on_the_same_authorized_stream(tmp_path, monkeypatch):
    path = _write_configuration(tmp_path)
    stream = SimpleNamespace(
        steer=AsyncMock(side_effect=[RunError("Unsupported", code="tool_handoff_unsupported"), "enqueue-test"])
    )
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        operator = app._subagent_operator
        head = object()
        require = AsyncMock(return_value=head)
        active = AsyncMock(return_value=SimpleNamespace(stream=stream))
        monkeypatch.setattr(operator, "_require_execution_for_parent", require)
        monkeypatch.setattr(operator, "_active_segment", active)
        result = await operator.steer_execution(
            parent_thread_id="parent", execution_id="child", message="Guidance", immediate=True
        )
        assert result.accepted and result.enqueue_id == "enqueue-test"
        require.assert_awaited_once_with("parent", "child")
        active.assert_awaited_once_with(head)
        assert stream.steer.await_args_list == [call("Guidance", immediate=True), call("Guidance", immediate=False)]
