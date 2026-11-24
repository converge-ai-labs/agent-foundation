from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from a13n_harness import AbstractHarnessPlugin, HarnessBuilder, PluginError, RunBindings
from a13n_harness.fork import _PrefixCapture
from pydantic_ai import Tool
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.usage import UsageLimits

from .test_plugins import TaskAffineEnvironment

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("convenience", [False, True])
async def test_post_run_reuses_bound_plugins_after_environment_closes(convenience):
    environment = TaskAffineEnvironment()
    calls = []
    hooks = []

    def lookup(value: str) -> str:
        pytest.fail(f"Fork dispatched a tool: {value}")

    async def stream(messages, info):
        calls.append((messages, info))
        yield "Answer"

    async def complete(messages, info):
        calls.append((messages, info))
        return ModelResponse(parts=[TextPart(" Next\nline ")])

    model = FunctionModel(
        stream_function=stream,
        function=complete,
        settings=OpenAIResponsesModelSettings(openai_native_tools=({"type": "web_search"},)),
    )

    class Plugin(AbstractHarnessPlugin):
        def __init__(self, name, data=None):
            self._name = name
            self.data = data

        @property
        def plugin_id(self):
            return self._name

        def get_capabilities(self):
            if self.plugin_id == "outer":
                return (Capability(tools=[Tool(lookup, metadata={"private": True})]),)
            return ()

        async def for_run(self, context):
            return Plugin(self.plugin_id, context.environment)

        async def wrap_run(self, exchange, call_next):
            result = await call_next(exchange)
            assert self.data is exchange.context.environment
            assert not environment.closed.is_set()
            self.data = "captured"
            hooks.append(f"wrap:{self.plugin_id}")
            return result

        async def post_run(self, context):
            assert self.data == "captured"
            assert environment.closed.is_set()
            assert context.metadata["original"] is True
            assert context.agent.definition is executable.definition
            assert context.agent.settled_model_id == model.model_name
            assert context.agent.can_replay_prefix
            assert context.result.output == "Answer"
            hooks.append(f"post:{self.plugin_id}")
            if self.plugin_id == "inner":
                assert await context.agent.complete_text("Follow up") == " Next\nline "

    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=model, plugins=(Plugin("outer"), Plugin("inner"))
    )
    kwargs = dict(
        bindings=RunBindings.embedded(environment=environment, metadata={"original": True}),
        usage_limits=UsageLimits(request_limit=1),
    )
    if convenience:
        result = await executable.run("Start", **kwargs)
    else:
        async with executable.stream("Start", **kwargs) as stream_run:
            assert stream_run.post_run is None
            async for _ in stream_run:
                pass
        assert hooks == ["wrap:inner", "wrap:outer"]
        result = stream_run.result
        session = stream_run.post_run
        assert session is not None
        await session.run()
        with pytest.raises(PluginError, match="only run once"):
            await session.run()
    assert hooks == ["wrap:inner", "wrap:outer", "post:inner", "post:outer"]
    assert result is not None and len(result.usage_records) == 1
    initial, fork = calls
    assert fork[0][:-2] == initial[0]
    assert fork[0][-1].parts[0].content == "Follow up"
    assert fork[1].function_tools == [replace(tool, metadata=None) for tool in initial[1].function_tools]
    assert fork[1].model_settings["openai_native_tools"] == ()


@pytest.mark.parametrize("cancel", [False, True])
async def test_post_run_isolates_failures_but_propagates_cancellation(cancel):
    calls = []

    class Plugin(AbstractHarnessPlugin):
        def __init__(self, name):
            self._name = name

        @property
        def plugin_id(self):
            return self._name

        async def post_run(self, context):
            calls.append(self.plugin_id)
            if self.plugin_id == "inner":
                if cancel:
                    raise asyncio.CancelledError
                raise ValueError("inner failed")
            raise TypeError("outer failed")

    async def stream(messages, info):
        yield "Answer"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        plugins=(Plugin("outer"), Plugin("inner")),
    )
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await executable.run("Start", bindings=RunBindings.embedded())
        assert calls == ["inner"]
    else:
        with pytest.raises(ExceptionGroup) as error:
            await executable.run("Start", bindings=RunBindings.embedded())
        assert [str(cause) for cause in error.value.exceptions] == ["inner failed", "outer failed"]
        assert calls == ["inner", "outer"]


@pytest.mark.parametrize(
    ("settings", "part"),
    [
        ({"openai_conversation_id": "conv-test"}, TextPart("Reply")),
        ({"openai_background": True}, TextPart("Reply")),
        ({"extra_body": {"tools": []}}, TextPart("Reply")),
        ({"anthropic_container": "container-test"}, TextPart("Reply")),
        ({"google_cached_content": "cachedContents/retrieval"}, TextPart("Reply")),
        (None, ToolCallPart("test_tool", {})),
    ],
)
async def test_capture_ignores_auxiliary_calls_and_resets_after_an_ineligible_response(settings, part):
    capture = _PrefixCapture(None)
    capture.begin("run-test")
    context = SimpleNamespace(run_id="run-test")
    request = ModelRequestContext(
        model=FunctionModel(function=lambda *_: ModelResponse(parts=[TextPart("Reply")])),
        model_id="model-test",
        messages=[],
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )
    await capture.after_model_request(
        context, request_context=request, response=ModelResponse(parts=[TextPart("Reply")])
    )
    prefix = capture.prefix
    assert prefix is not None
    excluded = replace(request, model_settings=settings)
    response = ModelResponse(parts=[part])
    await capture.after_model_request(SimpleNamespace(run_id="aux"), request_context=excluded, response=response)
    assert capture.prefix is prefix
    assert await capture.after_model_request(context, request_context=excluded, response=response) is response
    assert capture.prefix is None
