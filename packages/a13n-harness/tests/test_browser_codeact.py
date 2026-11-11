"""Browser tools use the existing CodeAct catalog and final tool dispatcher."""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from a13n_harness import RunBindings
from a13n_harness.capabilities import BrowserUseCapability, CodeActConfig
from a13n_harness.capabilities.browser import (
    BrowserBackend,
    BrowserElement,
    BrowserHumanControl,
    BrowserObservation,
    BrowserScreenshot,
    BrowserTab,
)
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
from a13n_harness.tools._output import tool_execution_value
from PIL import Image
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import BinaryContent, ToolReturnPart

from .test_codeact_state import _code, _run

pytestmark = pytest.mark.anyio
BROWSER_KEYS = ("observe", "read", "navigate", "act", "wait", "tabs", "dialog", "files", "screenshot")


@pytest.fixture
def backend():
    backend = AsyncMock(spec=BrowserBackend)
    backend.observe.return_value = BrowserObservation(
        "Page", "revision-a", (BrowserElement("opaque-button", "button Continue"),)
    )
    backend.interact.return_value = "Clicked"
    backend.navigate.return_value = '{"dispatch":"dispatched","verification":"passed"}'
    backend.read.return_value = '{"items":["Alpha","Beta"]}'
    backend.tabs.return_value = (BrowserTab("page-a", "Page A", "about:blank", True),)
    backend.wait.return_value = '{"verification":"passed"}'
    backend.dialog.return_value = "No dialog"
    backend.downloads.return_value = '{"downloads":[]}'
    image = BytesIO()
    Image.new("RGB", (8, 8), "red").save(image, format="JPEG")
    backend.screenshot.return_value = BrowserScreenshot(image.getvalue(), "opaque-screenshot", 800, 600)
    return backend


def value(part):
    assert isinstance(part, ToolReturnPart)
    return tool_execution_value(part.content, part.metadata)


@pytest.mark.parametrize("embedded", [False, True])
async def test_all_browser_tools_enter_catalog_and_dispatch_through_hooks(backend, embedded):
    executions = []

    class Hooks(AbstractCapability):
        async def before_tool_execute(self, ctx, *, call, tool_def, args):
            executions.append(call.tool_name)
            return args

    _, seen, contexts = await _run(
        [
            _code("""
import json
await browser_tabs()
await browser_navigate(url='https://example.test')
await browser_observe()
await browser_act(action={'kind':'click','target':{'kind':'ref','ref':'e1'}})
await browser_wait(condition={'kind':'text','text':'done'})
read = json.loads(await browser_read(kind='links'))
await browser_dialog()
await browser_files()
shot = await browser_screenshot()
{'count': len(read['items']), 'shot': shot['screenshot']}
""")
        ],
        capabilities=(BrowserUseCapability(backend if embedded else None), Hooks()),
        bindings=RunBindings.embedded(browser_backend=None if embedded else backend),
    )
    assert all(f"### `browser_{key}`" in contexts[0] for key in BROWSER_KEYS)
    assert value(seen[0]) == {"count": 2, "shot": "s1"}
    assert executions == [
        "run_code",
        "browser_tabs",
        "browser_navigate",
        "browser_observe",
        "browser_act",
        "browser_wait",
        "browser_read",
        "browser_dialog",
        "browser_files",
        "browser_screenshot",
    ]
    assert backend.interact.call_args.kwargs["revision"] == "revision-a"
    assert backend.interact.call_args.args[0].target.ref == "opaque-button"


async def test_direct_and_codeact_calls_share_refs_and_navigation_invalidation(backend):
    _, seen, _ = await _run(
        [
            ("browser_observe", {}),
            _code("await browser_act(action={'kind':'click','target':{'kind':'ref','ref':'e1'}})"),
            ("browser_navigate", {"url": "https://example.test"}),
            _code("await browser_act(action={'kind':'click','target':{'kind':'ref','ref':'e1'}})"),
            _code("await browser_observe()"),
            ("browser_act", {"action": {"kind": "click", "target": {"kind": "ref", "ref": "e2"}}}),
        ],
        capabilities=(BrowserUseCapability(),),
        bindings=RunBindings.embedded(browser_backend=backend),
    )
    assert seen[1].outcome == "success"
    assert seen[3].outcome == "failed"
    assert "ModelRetry" in str(seen[3].content)
    assert seen[-1].outcome == "success"
    assert backend.interact.await_count == 2


@pytest.mark.parametrize("enabled", [None, frozenset({"read", "tabs"})])
async def test_catalog_does_not_grant_missing_or_disabled_browser_tools(backend, enabled):
    _, seen, contexts = await _run(
        [_code("await browser_act(action={})")],
        capabilities=() if enabled is None else (BrowserUseCapability(enabled_tools=enabled),),
        bindings=RunBindings.embedded(browser_backend=backend) if enabled is not None else None,
    )
    assert ("### `browser_read`" in contexts[0]) is (enabled is not None)
    assert "### `browser_act`" not in contexts[0]
    assert seen[0].outcome == "failed"
    backend.interact.assert_not_awaited()


async def test_screenshot_metadata_and_native_image_survive_codeact_and_visual_action(backend):
    _, seen, _ = await _run(
        [
            _code("shot = await browser_screenshot()\nshot"),
            _code(
                "await browser_act(action={'kind':'click','target':{'kind':'visual','screenshot':shot['screenshot'],'x':0.5,'y':0.5}})"
            ),
        ],
        capabilities=(BrowserUseCapability(),),
        bindings=RunBindings.embedded(browser_backend=backend),
    )
    assert value(seen[0]) == {"screenshot": "s1", "width_css": 800, "height_css": 600, "visual_action": True}
    images = [item for item in seen[0].content if isinstance(item, BinaryContent)]
    assert len(images) == 1 and images[0].data == backend.screenshot.return_value.data
    assert backend.interact.call_args.args[0].target.screenshot == "opaque-screenshot"
    assert seen[1].outcome == "success"


@pytest.mark.parametrize("mode", ["deny", "ask"])
async def test_nested_browser_calls_retain_tool_permissions(backend, mode):
    _, seen, _ = await _run(
        [_code("await browser_navigate(url='https://example.test')")],
        capabilities=(
            BrowserUseCapability(),
            ToolPermissionsCapability(ToolPermissions(default="allow", rules={"browser.navigate": mode})),
        ),
        bindings=RunBindings.embedded(browser_backend=backend),
    )
    assert seen[0].outcome == "failed"
    backend.navigate.assert_not_awaited()


async def test_current_run_backend_overrides_embedded_backend(backend):
    stale = AsyncMock(spec=BrowserBackend)
    _, seen, _ = await _run(
        [_code("await browser_read()")],
        capabilities=(BrowserUseCapability(stale),),
        bindings=RunBindings.embedded(browser_backend=backend),
    )
    assert value(seen[0]) == backend.read.return_value
    stale.read.assert_not_awaited()
    backend.read.assert_awaited_once()


@pytest.mark.parametrize("immediate", [False, True])
async def test_takeover_keeps_host_handback_policy_without_replaying_action(backend, immediate):
    backend.read.side_effect = BrowserHumanControl("Human control")
    guidance = "Registered. Stop browser work."
    _, seen, _ = await _run(
        [_code("await browser_read()")],
        capabilities=(BrowserUseCapability(on_human_control=(lambda: guidance) if immediate else None),),
        bindings=RunBindings.embedded(browser_backend=backend),
    )
    if immediate:
        assert value(seen[0]) == guidance
        backend.wait_for_handback.assert_not_awaited()
    else:
        assert "pending action was not executed" in value(seen[0])
        backend.wait_for_handback.assert_awaited_once()
    backend.read.assert_awaited_once()


async def test_runner_timeout_cancels_nested_browser_work_without_replay(backend):
    cancelled = []

    async def slow(*args, **kwargs):
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.append(True)

    backend.read.side_effect = slow
    _, seen, _ = await _run(
        [_code("await browser_read()")],
        capabilities=(BrowserUseCapability(),),
        bindings=RunBindings.embedded(browser_backend=backend),
        config=CodeActConfig(timeout_seconds=0.1),
    )
    assert seen[0].outcome == "failed"
    assert cancelled == [True]
    backend.read.assert_awaited_once()


async def test_grouped_browser_tools_remain_callable_through_existing_proxy(backend):
    import json

    from a13n_harness import AgentSpec, HarnessBuilder
    from a13n_harness.capabilities import CodeActCapability, ToolSearchPlan, ToolSearchSelection
    from pydantic_ai.messages import ModelRequest
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert "### `call_tool`" in info.instructions
            assert "### `browser_read`" not in info.instructions
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args=json.dumps(
                        {"code": "await call_tool(group='browser', tool='browser_read', arguments={'kind':'links'})"}
                    ),
                    tool_call_id="read",
                )
            }
        else:
            part = next(
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_call_id == "read"
            )
            assert value(part) == backend.read.return_value
            yield "done"

    browser = BrowserUseCapability()
    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(browser, CodeActCapability()),
        tool_search=ToolSearchPlan(groups={"browser": ToolSearchSelection("Browser", capabilities=(browser,))}),
    )
    result = await executable.run("read", bindings=RunBindings.embedded(browser_backend=backend))
    assert result.output_or_raise() == "done"
    backend.read.assert_awaited_once()


async def test_nested_browser_action_uses_original_typed_validation(backend):
    _, seen, _ = await _run(
        [_code("await browser_act(action={'kind':'click','target':{'ref':'e1'}})")],
        capabilities=(BrowserUseCapability(),),
        bindings=RunBindings.embedded(browser_backend=backend),
    )
    assert seen[0].outcome == "failed"
    backend.interact.assert_not_awaited()
