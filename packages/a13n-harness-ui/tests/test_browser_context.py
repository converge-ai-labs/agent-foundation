"""Availability is read-only; explicit reminders share the existing handback lifecycle."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.content import request_input_content
from a13n_harness_ui.browser_context import BrowserContextCapability
from a13n_harness_ui.errors import HarnessUiError
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_host_browser import browsers as browsers

pytestmark = pytest.mark.anyio


def capability(browsers):
    return BrowserContextCapability(browsers.backend("thread-a", browsers.bindings["thread-a"].identity))


async def test_registration_coalesces_with_blocked_calls_and_does_not_touch_pages(browsers):
    context = capability(browsers)
    assert (await context.notify_on_handback())["status"] == "already_available"
    assert not browsers.handback._pending
    browsers.human_thread = "thread-b"
    browsers.handback.takeover()
    for _ in range(2):
        assert (await context.notify_on_handback())["status"] == "registered"
    browsers.handback.blocked("thread-a")
    assert len(browsers.handback._pending) == 1
    browsers.handback.suppress("thread-a")
    assert (await context.notify_on_handback())["status"] == "suppressed"
    assert not browsers.handback._pending
    browsers.runtime.send.assert_not_called()
    browsers.runtime.observe.assert_not_called()


async def test_handback_winning_during_authorization_does_not_leave_registration(browsers):
    context = capability(browsers)
    browsers.human_thread = "thread-b"
    browsers.handback.takeover()
    entered, release = asyncio.Event(), asyncio.Event()

    async def authorize(thread):
        entered.set()
        await release.wait()

    browsers.authorize = authorize
    task = asyncio.create_task(context.notify_on_handback())
    await entered.wait()
    browsers.human_thread = ""
    browsers.handback.released()
    release.set()
    assert (await task)["status"] == "already_available"
    assert not browsers.handback._pending


@pytest.mark.parametrize("failure", ["authorization", "binding", "runtime"])
async def test_unavailable_is_not_handback_and_never_registers(browsers, failure):
    context = capability(browsers)
    browsers.human_thread = "thread-b"
    browsers.handback.takeover()
    if failure == "authorization":
        browsers.authorize = AsyncMock(side_effect=HarnessUiError("Access revoked", code="denied"))
    elif failure == "binding":
        browsers.bindings.pop("thread-a")
    else:
        browsers.failed("runtime failed")
    assert (await context.notify_on_handback())["status"] == "unavailable"
    assert not browsers.handback._pending


async def test_each_model_request_observes_current_gate_without_registering(browsers):
    context = capability(browsers)
    seen = []

    async def stream(messages, info):
        latest = messages[-1]
        assert isinstance(latest, ModelRequest)
        blocks = [part.value for part in request_input_content(latest) if part.metadata.source_id == context.id]
        assert len(blocks) == 1
        seen.append(blocks[0])
        if len(seen) == 1:
            assert not browsers.handback._pending
            browsers.human_thread = ""
            browsers.handback.released()
            yield {0: DeltaToolCall(name="browser_notify_on_handback", json_args="{}", tool_call_id="notify")}
        else:
            yield "done"

    browsers.human_thread = "thread-b"
    browsers.handback.takeover()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(context,),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert "<browser-context>" in seen[0] and "Human takeover" in seen[0]
    assert "automation is available" in seen[1]
    assert not browsers.handback._pending
    browsers.runtime.send.assert_not_called()
