import json
from unittest.mock import AsyncMock

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import BrowserUseCapability, BrowserUseCodeCapability, CodeActCapability, CodeActConfig
from a13n_harness.capabilities.browser import BrowserBackend, BrowserElement, BrowserHumanControl, BrowserObservation
from a13n_harness.capabilities.browser.actions import Click, Ref
from a13n_harness.errors import DefinitionError
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("takeover", [False, True])
async def test_programmable_browser_keeps_scoped_helpers_behind_run_code(takeover):
    backend = AsyncMock(spec=BrowserBackend)
    backend.observe.return_value = BrowserObservation("Page", "r1", (BrowserElement("node", "button Continue"),))
    if takeover:
        backend.interact.side_effect = BrowserHumanControl("Paused")
    else:
        backend.interact.return_value = '{"dispatch":"dispatched","verification":"unverified"}'
    owner = BrowserUseCodeCapability(enabled_tools=frozenset({"observe", "act"}), on_human_control=lambda: "Paused")
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        assert not any(t.name.startswith("browser_") for t in info.function_tools)
        assert "run_code" in {t.name for t in info.function_tools}
        assert "run_program" not in {t.name for t in info.function_tools}
        if calls == 1:
            code = """page = await browser_observe()
clicked = await browser_act(action={"kind":"click","target":{"kind":"ref","ref":"e1"}})
clicked["status"]"""
            yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}), tool_call_id="r1")}
            return
        result = next(p for p in messages[-1].parts if isinstance(p, ToolReturnPart) and p.tool_name == "run_code")
        assert ("human_control" if takeover else "ok") in str(result.content)
        yield "Done"

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(owner,)
    )
    result = await executable.run("Click Continue", bindings=RunBindings.embedded(browser_backend=backend))
    assert result.output_or_raise() == "Done"
    backend.interact.assert_awaited_once_with(Click(kind="click", target=Ref(kind="ref", ref="node")), revision="r1")


@pytest.mark.parametrize("other", [BrowserUseCapability(), CodeActCapability(CodeActConfig(inline=False))])
def test_incompatible_browser_code_configuration_fails_at_build(other):
    with pytest.raises(DefinitionError):
        HarnessBuilder().build(
            AgentSpec(), output_type=str, model="test", capabilities=(BrowserUseCodeCapability(), other)
        )


@pytest.mark.parametrize("mode", ["browser-use", "browser-use-code"])
async def test_browser_only_agent_reads_exact_selected_skill(mode):
    from dataclasses import replace

    from a13n_harness.capabilities.browser.skill import (
        BROWSER_SKILLS_PATH,
        BROWSER_SKILLS_ROOT,
        BrowserSkillReader,
        browser_skill_source,
    )
    from a13n_harness.capabilities.skills import SkillManager, SkillsCapability
    from a13n_harness.environment.advanced import create_environment_runtime

    from .test_skills import _runtime_mount

    owner = BrowserUseCapability() if mode == "browser-use" else BrowserUseCodeCapability()
    calls = 0
    path = f"{BROWSER_SKILLS_PATH}/{mode}/SKILL.md"

    async def model(messages, info):
        nonlocal calls
        calls += 1
        assert f'<skill name="{mode}">' in info.instructions
        assert "view" in {tool.name for tool in info.function_tools}
        if calls == 1:
            yield {0: DeltaToolCall(name="view", json_args=json.dumps({"file_path": path}), tool_call_id="skill")}
        else:
            returned = next(p for p in messages[-1].parts if isinstance(p, ToolReturnPart) and p.tool_name == "view")
            assert f"name: {mode}\n" in str(returned.content)
            yield "Done"

    runtime = create_environment_runtime(
        mounts={"browser-skills": replace(_runtime_mount(BROWSER_SKILLS_ROOT), mount_path=BROWSER_SKILLS_PATH)},
        default_mount=None,
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(owner, BrowserSkillReader(mode), SkillsCapability(SkillManager((browser_skill_source(mode),)))),
    )
    result = await executable.run(
        "Read the browser manual",
        bindings=RunBindings.embedded(browser_backend=AsyncMock(spec=BrowserBackend), environment=runtime),
    )
    assert result.output_or_raise() == "Done"


async def test_direct_call_cannot_dispatch_hidden_browser_helper():
    backend = AsyncMock(spec=BrowserBackend)
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {0: DeltaToolCall(name="browser_tabs", json_args='{"action":"new"}', tool_call_id="direct")}
        else:
            yield "Done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(BrowserUseCodeCapability(),),
    )
    result = await executable.run("Try direct dispatch", bindings=RunBindings.embedded(browser_backend=backend))
    assert result.output_or_raise() == "Done"
    backend.tabs.assert_not_awaited()


async def test_script_requires_explicit_host_grant_and_returns_json():
    backend = AsyncMock(spec=BrowserBackend)
    backend.page_script = AsyncMock(return_value={"name": "Sample", "price": 12})
    expression = "({name: document.title, price: 12})"
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        names = {t.name for t in info.function_tools}
        assert "browser_js" not in names
        assert "async def browser_js" in info.instructions
        if calls == 1:
            code = f'(await browser_js(expression={expression!r}))["value"]["price"]'
            yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}), tool_call_id="read_price")}
        else:
            returned = next(p for p in messages[-1].parts if isinstance(p, ToolReturnPart))
            assert "12" in str(returned.content)
            yield "Done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(BrowserUseCodeCapability(enabled_tools=frozenset({"js"})),),
    )
    result = await executable.run("Get price", bindings=RunBindings.embedded(browser_backend=backend))
    assert result.output_or_raise() == "Done"
    backend.page_script.assert_awaited_once_with(expression)
