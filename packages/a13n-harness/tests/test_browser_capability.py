from unittest.mock import AsyncMock

import pytest
from a13n_harness.capabilities import BrowserUseCapability
from a13n_harness.capabilities.browser import BrowserElement, BrowserError, BrowserObservation
from pydantic_ai.exceptions import ModelRetry

pytestmark = pytest.mark.anyio


async def test_native_tools_keep_compact_refs_local_and_invalidate_after_navigation():
    backend = AsyncMock()
    backend.observe.return_value = BrowserObservation(
        "Page", "revision-a", (BrowserElement("opaque-target", "button Continue"),)
    )
    backend.act.return_value = "done"
    owner = BrowserUseCapability(backend)
    tools = owner.get_toolset().tools

    async def call(name, **kwargs):
        return await tools[name].function_schema.call(kwargs, None)

    assert "[e1] button Continue" in await call("browser_observe")
    await call("browser_click", ref="e1")
    backend.act.assert_awaited_with("click", target="opaque-target", revision="revision-a")
    await call("browser_navigate", url="https://example.com")
    with pytest.raises(ModelRetry):
        await call("browser_click", ref="e1")
    another = BrowserUseCapability(backend).get_toolset().tools
    with pytest.raises(ModelRetry):
        await another["browser_click"].function_schema.call({"ref": "e1"}, None)


async def test_human_takeover_errors_are_tool_results_and_do_not_crash_the_run():
    backend = AsyncMock()
    backend.observe.side_effect = BrowserError("Human control. Wait for handback.")
    backend.screenshot.side_effect = BrowserError("Human control. Wait for handback.")
    backend.act.side_effect = BrowserError("Human control. Wait for handback.")
    tools = BrowserUseCapability(backend).get_toolset().tools
    for name, args in [("browser_observe", {}), ("browser_screenshot", {}), ("browser_press", {"key": "Enter"})]:
        assert "Human control" in await tools[name].function_schema.call(args, None)


async def test_browser_tools_compose_in_the_native_harness_model_loop():
    from a13n_harness import AgentSpec, HarnessBuilder
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    backend = AsyncMock()
    backend.observe.return_value = BrowserObservation(
        "Page", "revision-a", (BrowserElement("continue-target", "button Continue"),)
    )
    backend.act.return_value = "Clicked. Observe to verify."
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        assert "browser_observe" in {tool.name for tool in info.function_tools}
        if calls == 1:
            yield {0: DeltaToolCall(name="browser_observe", json_args="{}", tool_call_id="observe")}
            return
        if calls == 2:
            yield {0: DeltaToolCall(name="browser_click", json_args='{"ref":"e1"}', tool_call_id="click")}
            return
        yield "Done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        capabilities=(BrowserUseCapability(backend),),
        output_type=str,
    )
    result = await executable.run("Click Continue")
    assert result.output_or_raise() == "Done"
    backend.act.assert_awaited_once_with("click", target="continue-target", revision="revision-a")


async def test_restrictive_host_policy_cannot_be_bypassed_by_browser_traffic():
    from types import SimpleNamespace

    from a13n_harness.configuration import RunConfiguration
    from a13n_harness.errors import DefinitionError

    backend = AsyncMock()
    ctx = SimpleNamespace(deps=SimpleNamespace(configuration=RunConfiguration(allowed_hosts={"example.com"})))
    with pytest.raises(DefinitionError, match="restrictive allowed_hosts"):
        await BrowserUseCapability(backend).for_run(ctx)
    backend.observe.assert_not_awaited()


async def test_human_control_waits_without_polling_or_replaying_the_pending_action():
    import asyncio

    from a13n_harness.capabilities.browser import BrowserHumanControl

    released = asyncio.Event()
    backend = AsyncMock()
    backend.act.side_effect = BrowserHumanControl("Human control")
    backend.wait_for_handback.side_effect = released.wait
    backend.observe.return_value = BrowserObservation("Signed in", "new-revision", ())
    tools = BrowserUseCapability(backend).get_toolset().tools
    pending = asyncio.create_task(tools["browser_press"].function_schema.call({"key": "Enter"}, None))
    await asyncio.sleep(0)
    assert not pending.done()
    backend.act.assert_awaited_once()
    backend.observe.assert_not_awaited()
    released.set()
    assert "fresh browser_observe" in await pending
    backend.act.assert_awaited_once()  # No silent replay of the pre-takeover action.
    assert "Signed in" in await tools["browser_observe"].function_schema.call({}, None)
