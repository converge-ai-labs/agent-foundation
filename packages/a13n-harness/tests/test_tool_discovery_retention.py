from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from a13n_harness import AgentSpec, DeferredToolRequests, DeferredToolResume, HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import (
    CodeActCapability,
    CompactionCapability,
    CompactionPolicy,
    HandoffCapability,
    ToolSearchCapability,
    ToolSearchGroup,
)
from a13n_harness.capabilities.context import _COMPACTION_PROMPT
from a13n_harness.content import request_input_content
from a13n_harness.tools import discovery
from a13n_harness.toolsets.codeact import CodeActPolicyToolset, CodeActToolPolicy
from pydantic import TypeAdapter
from pydantic_ai import CallDeferred, FunctionToolset, Tool
from pydantic_ai.capabilities import AbstractCapability, Capability, ToolSearch
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelResponse,
    NativeToolSearchCallPart,
    NativeToolSearchReturnPart,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    ToolSearchReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tools import DeferredToolResults, ToolDefinition
from pydantic_ai.toolsets import ExternalToolset
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


def _scripted_model(function):
    async def stream(messages, info):
        response = function(messages, info)
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, NativeToolSearchCallPart | NativeToolSearchReturnPart):
                yield {index: part}
            elif isinstance(part, ToolCallPart):
                yield {
                    index: DeltaToolCall(
                        name=part.tool_name, json_args=part.args_as_json_str(), tool_call_id=part.tool_call_id
                    )
                }

    return FunctionModel(stream_function=stream)


def _source(effects, *, proxy=False):
    def double(value: int) -> int:
        effects.append(value)
        return value * 2

    def triple(value: int) -> int:
        return value * 3

    tools = CodeActPolicyToolset(
        FunctionToolset([Tool(double, defer_loading=not proxy), Tool(triple, defer_loading=not proxy)], id="math"),
        CodeActToolPolicy(default=True),
    )
    if proxy:
        return ToolSearchCapability(groups={"math": ToolSearchGroup(tools, "Arithmetic")})
    return Capability(toolsets=[tools])


def _call(name, args, index):
    return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=f"step-{index}")])


@pytest.mark.parametrize("names", [set(), {"lookup"}])
async def test_pruning_notice_is_model_context_not_authored_input(names):
    messages = discovery._discovery_messages(names, [], None, "retained-test")
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))
    items = request_input_content(restored[0])
    assert len(items) == 1
    assert isinstance(items[0].value, TextContent)
    assert items[0].value.content == discovery._NOTICE
    assert items[0].metadata.display is False
    assert items[0].metadata.source_id == discovery._DISCOVERY_ID


@pytest.mark.parametrize("replacement", ["summarize", "compact"])
async def test_native_discovery_retains_lru_and_immediate_codeact_directory(monkeypatch, replacement):
    monkeypatch.setattr(discovery, "_MAX_TOOLS", 1)
    effects = []
    snapshots = []
    calls = [
        ("search_tools", {"queries": ["double"]}),
        ("search_tools", {"queries": ["triple"]}),
        ("run_code", {"code": "await double(value=20)"}),
    ]
    if replacement == "summarize":
        calls.append(("summarize", {"content": "Continue arithmetic"}))
    calls.append(("run_code", {"code": "await double(value=21)"}))
    index = 0
    replaced = False

    def model(messages, info):
        nonlocal index, replaced
        if any(_COMPACTION_PROMPT == part.content for part in messages[-1].parts if isinstance(part, UserPromptPart)):
            return ModelResponse(parts=[TextPart("Continue arithmetic")])
        snapshots.append(deepcopy(messages))
        if index == len(calls):
            return ModelResponse(parts=[TextPart("done")])
        if index == len(calls) - 1:
            replaced = True
            assert sum(isinstance(part, ToolSearchReturnPart) for msg in messages for part in msg.parts) == 1
            for tool in info.function_tools:
                if tool.name in {"run_code", "run_program"}:
                    assert "async def double" in tool.description
                    assert "async def triple" not in tool.description
            assert not any(
                isinstance(part, ToolReturnPart) and part.tool_name == "run_code"
                for msg in messages
                for part in msg.parts
            )
        name, args = calls[index]
        response = _call(name, args, index)
        index += 1
        return response

    class TriggerCompaction(AbstractCapability):
        async def after_model_request(self, ctx, *, request_context, response):
            if replacement == "compact" and any(
                isinstance(part, ToolCallPart) and part.tool_call_id == "step-2" for part in response.parts
            ):
                response.usage = RequestUsage(input_tokens=3000, output_tokens=10)
            return response

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=_scripted_model(model),
        output_type=str,
        capabilities=(
            _source(effects),
            ToolSearch(strategy="keywords"),
            CodeActCapability(),
            HandoffCapability(),
            CompactionCapability(CompactionPolicy(trigger_tokens=2000)),
            TriggerCompaction(),
        ),
    )
    result = await executable.run("Work on arithmetic", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert replaced
    assert effects == [20, 21]
    assert result.state is not None
    restored = HarnessState.model_validate_json(result.state.model_dump_json())
    assert ModelMessagesTypeAdapter.dump_json(list(restored.message_history)) == ModelMessagesTypeAdapter.dump_json(
        list(result.state.message_history)
    )


@pytest.mark.parametrize("nested", [False, True])
async def test_proxy_discovery_survives_summary_and_state_roundtrip(nested):
    effects = []
    index = 0
    observed = []
    search = ("discover_tools", {"query": "double"})
    if nested:
        search = ("run_code", {"code": "await discover_tools(query='double')"})
    calls = [
        search,
        ("run_code", {"code": "await call_tool(group='math', tool='double', arguments={'value': 20})"}),
        ("summarize", {"content": "Continue arithmetic"}),
    ]

    def model(messages, info):
        nonlocal index
        observed.append(deepcopy(messages))
        if index == len(calls):
            return ModelResponse(parts=[TextPart("saved")])
        name, args = calls[index]
        index += 1
        return _call(name, args, index)

    capabilities = (_source(effects, proxy=True), CodeActCapability(), HandoffCapability())
    executable = HarnessBuilder().build(
        AgentSpec(), model=_scripted_model(model), output_type=str, capabilities=capabilities
    )
    result = await executable.run("Find arithmetic tools", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "saved"
    text = ModelMessagesTypeAdapter.dump_json(observed[-1]).decode()
    assert "parameters_json_schema" in text and "double" in text
    assert "retained-tool-discovery" in text
    assert "no search was executed" in text
    assert "Search again" in text
    assert not any(
        isinstance(part, ToolCallPart) and part.tool_name == "run_code" for msg in observed[-1] for part in msg.parts
    )
    state = HarnessState.model_validate_json(result.state.model_dump_json())
    resumed = 0

    def resume(messages, info):
        nonlocal resumed
        resumed += 1
        if resumed == 1:
            return _call("summarize", {"content": "Continue once more"}, 10)
        if resumed == 2:
            assert "parameters_json_schema" in ModelMessagesTypeAdapter.dump_json(messages).decode()
            return _call(
                "run_code", {"code": "await call_tool(group='math', tool='double', arguments={'value': 21})"}, 11
            )
        return ModelResponse(parts=[TextPart("done")])

    executable = HarnessBuilder().build(
        AgentSpec(), model=_scripted_model(resume), output_type=str, capabilities=capabilities
    )
    result = await executable.run("Continue", previous_state=state, bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert effects == [20, 21]


async def _run_calls(capabilities, calls, *, previous_state=None):
    snapshots = []
    index = 0

    def model(messages, info):
        nonlocal index
        snapshots.append((deepcopy(messages), deepcopy(info)))
        if index == len(calls):
            return ModelResponse(parts=[TextPart("done")])
        name, args = calls[index]
        index += 1
        return _call(name, args, index)

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=_scripted_model(model),
        output_type=str,
        capabilities=capabilities,
    )
    result = await executable.run("Continue", previous_state=previous_state, bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    return result, snapshots


@pytest.mark.parametrize("proxy", [False, True])
async def test_search_without_target_calls_retains_nothing(monkeypatch, proxy):
    monkeypatch.setattr(discovery, "_MAX_TOOLS", 1)
    capabilities = (_source([], proxy=proxy), ToolSearch(strategy="keywords"), CodeActCapability(), HandoffCapability())
    search = ("discover_tools", {"query": ""}) if proxy else ("search_tools", {"queries": ["double triple"]})
    _, snapshots = await _run_calls(capabilities, [search, ("summarize", {"content": "Continue"})])
    messages, info = snapshots[-1]
    assert not any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts)
    for tool in info.function_tools:
        if tool.name in {"run_code", "run_program"}:
            assert "async def double" not in tool.description
            assert "async def triple" not in tool.description
    assert not info.model_request_parameters.revealed_tool_names


async def test_byte_budget_counts_native_schema_and_both_codeact_directories(monkeypatch):
    def large(value: int) -> int:
        return value

    source = Capability(
        toolsets=[
            CodeActPolicyToolset(
                FunctionToolset([Tool(large, description="多" * 1500, defer_loading=True)]),
                CodeActToolPolicy(default=True),
            )
        ]
    )
    monkeypatch.setattr(discovery, "_MAX_CONTEXT_BYTES", 10_000)
    for codeact, retained in [(False, True), (True, False)]:
        capabilities = (
            source,
            ToolSearch(strategy="keywords"),
            HandoffCapability(),
            *((CodeActCapability(),) if codeact else ()),
        )
        _, snapshots = await _run_calls(
            capabilities,
            [
                ("search_tools", {"queries": ["large"]}),
                ("large", {"value": 1}),
                ("summarize", {"content": "Continue"}),
            ],
        )
        messages, info = snapshots[-1]
        assert ("large" in info.model_request_parameters.revealed_tool_names) is retained
        assert any(isinstance(part, ToolSearchReturnPart) for message in messages for part in message.parts) is retained


@pytest.mark.parametrize("change", ["removed", "identity", "schema", "instructions"])
async def test_proxy_revalidation_drops_stale_evidence(change):
    def lookup(value: int) -> int:
        return value

    def grouped(*, source_id="source", description="Lookup an integer", instructions="Original guidance"):
        return ToolSearchCapability(
            groups={
                "db": ToolSearchGroup(
                    FunctionToolset([Tool(lookup, description=description)], id=source_id, instructions=instructions),
                    "Database",
                )
            }
        )

    first, _ = await _run_calls(
        (grouped(), HandoffCapability()),
        [
            ("discover_tools", {"query": "lookup"}),
            ("call_tool", {"group": "db", "tool": "lookup", "arguments": {"value": 1}}),
        ],
    )
    state = HarnessState.model_validate_json(first.state.model_dump_json())
    updated = {
        "removed": Capability(),
        "identity": grouped(source_id="different-source"),
        "schema": grouped(description="Changed contract"),
        "instructions": grouped(instructions="New guidance"),
    }[change]
    _, snapshots = await _run_calls(
        (updated, HandoffCapability()), [("summarize", {"content": "Continue"})], previous_state=state
    )
    results = [part for message in snapshots[-1][0] for part in message.parts if isinstance(part, ToolReturnPart)]
    if change in {"removed", "identity"}:
        assert not results
    else:
        entry = results[0].content["tools"][0]
        if change == "schema":
            assert entry["description"] == "Changed contract"
        else:
            assert entry["instructions"] == ["New guidance"]


async def test_native_evidence_roundtrip_revalidates_identity_and_executes():
    effects = []
    source = _source(effects)
    capabilities = (source, ToolSearch(strategy="keywords"), CodeActCapability(), HandoffCapability())
    first, _ = await _run_calls(capabilities, [("search_tools", {"queries": ["double"]}), ("double", {"value": 20})])
    state = HarnessState.model_validate_json(first.state.model_dump_json())
    _, snapshots = await _run_calls(
        capabilities,
        [
            ("summarize", {"content": "Continue"}),
            ("run_code", {"code": "await double(value=21)"}),
        ],
        previous_state=state,
    )
    assert effects == [20, 21]
    assert "double" in snapshots[1][1].model_request_parameters.revealed_tool_names

    def double(value: int) -> int:
        raise AssertionError("Replaced source must not be rediscovered from stale evidence")

    changed = Capability(toolsets=[FunctionToolset([Tool(double, defer_loading=True)], id="new-source")])
    _, snapshots = await _run_calls(
        (changed, ToolSearch(strategy="keywords"), HandoffCapability()),
        [
            ("summarize", {"content": "Continue"}),
        ],
        previous_state=state,
    )
    assert not snapshots[-1][1].model_request_parameters.revealed_tool_names


async def test_loaded_capability_is_not_retained_or_advertised_by_codeact():
    def lookup() -> int:
        return 42

    source = Capability(
        id="database",
        description="Database tools",
        defer_loading=True,
        toolsets=[
            CodeActPolicyToolset(FunctionToolset([lookup]), CodeActToolPolicy(default=True)),
        ],
    )
    _, snapshots = await _run_calls(
        (source, CodeActCapability(), HandoffCapability()),
        [
            ("load_capability", {"id": "database"}),
            ("summarize", {"content": "Continue"}),
        ],
    )
    for tool in snapshots[-1][1].function_tools:
        if tool.name in {"run_code", "run_program"}:
            assert "async def lookup" not in tool.description
    assert not snapshots[-1][1].model_request_parameters.revealed_tool_names


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_provider_native_discovery_final_answer_roundtrip_and_wire_projection(provider):
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
    from pydantic_ai.providers.anthropic import AnthropicProvider
    from pydantic_ai.providers.openai import OpenAIProvider

    effects = []
    capabilities = (_source(effects), ToolSearch(), CodeActCapability(), HandoffCapability())

    def discover(messages, info):
        return ModelResponse(
            parts=[
                NativeToolSearchCallPart(
                    args={"queries": ["double"]}, tool_call_id="search-native", provider_name=provider
                ),
                NativeToolSearchReturnPart(
                    content={"discovered_tools": [{"name": "double"}]},
                    tool_call_id="search-native",
                    provider_name=provider,
                ),
                TextPart("Found arithmetic"),
            ]
        )

    executable = HarnessBuilder().build(
        AgentSpec(), model=_scripted_model(discover), output_type=str, capabilities=capabilities
    )
    first = await executable.run("Find arithmetic", bindings=RunBindings.embedded())
    assert first.output_or_raise() == "Found arithmetic"
    state = HarnessState.model_validate_json(first.state.model_dump_json())
    native = next(
        part
        for message in state.message_history
        for part in message.parts
        if isinstance(part, NativeToolSearchReturnPart)
    )
    assert native.metadata is None
    assert state.agent_context_state.get(discovery._DISCOVERY_ID) is None
    _, snapshots = await _run_calls(
        capabilities,
        [
            ("double", {"value": 20}),
            ("summarize", {"content": "Continue arithmetic"}),
            ("run_code", {"code": "await double(value=21)"}),
        ],
        previous_state=state,
    )
    assert effects == [20, 21]
    messages, info = snapshots[2]
    assert any(
        isinstance(part, ToolSearchReturnPart | NativeToolSearchReturnPart)
        for message in messages
        for part in message.parts
    ), [(type(message).__name__, [type(part).__name__ for part in message.parts]) for message in messages]
    for message in messages:
        if isinstance(message, ModelResponse):
            assert message.provider_response_id is None and message.usage.total_tokens == 0
    parameters = info.model_request_parameters
    chat = OpenAIChatModel("gpt-4.1", provider=OpenAIProvider(api_key="test-only"))
    responses = OpenAIResponsesModel("gpt-5.4", provider=OpenAIProvider(api_key="test-only"))
    claude = AnthropicModel("claude-sonnet-4-6", provider=AnthropicProvider(api_key="test-only"))
    assert parameters.revealed_tool_names == {"double"}
    for model in (chat, responses, claude):
        settings, prepared = model.prepare_request({}, parameters)
        projected = model.prepare_messages(messages, prepared)
        if isinstance(model, OpenAIChatModel):
            wire_messages = await model._map_messages(projected, prepared, model_settings=settings or {})
            assert prepared.visibility_of("triple") == "withheld"
            assert prepared.visibility_of("double") == "visible"
        elif isinstance(model, OpenAIResponsesModel):
            wire_messages = await model._map_messages(projected, settings or {}, prepared)
        else:
            wire_messages = await model._map_message(projected, prepared, settings or {})
        wire = json.dumps(wire_messages, default=str)
        assert "double" in wire
        assert "triple" not in wire
        assert "search-native" not in wire
        assert "retained-" in wire
        assert "Search again" in wire

    # Named-native-only Anthropic keeps the synthetic exchange without a local fallback.
    named = replace(
        parameters,
        function_tools=[tool for tool in parameters.function_tools if tool.name != "search_tools"],
        native_tools=list(ToolSearch(strategy="bm25").get_native_tools()),
    )
    settings, prepared = claude.prepare_request({}, named)
    wire = json.dumps(await claude._map_message(claude.prepare_messages(messages, prepared), prepared, settings or {}))
    assert "tool_reference" in wire and "retained-" in wire and "Search again" in wire
    assert "search_tools" not in {tool.name for tool in prepared.function_tools}
    from pydantic_ai.exceptions import UserError

    with pytest.raises(UserError):
        chat.prepare_request({}, named)
    _, prepared = responses.prepare_request({}, named)
    with pytest.raises(UserError):
        responses._get_native_tools(prepared)


async def test_nested_proxy_state_deduplicates_calls_and_eviction_does_not_resurrect(monkeypatch):
    monkeypatch.setattr(discovery, "_MAX_STATE_BYTES", 1200)
    capabilities = (_source([], proxy=True), CodeActCapability(), HandoffCapability())
    first, _ = await _run_calls(
        capabilities,
        [
            ("run_code", {"code": "await discover_tools(query='double')"}),
            (
                "run_code",
                {
                    "code": "for i in range(40):\n    await call_tool(group='math', tool='double', arguments={'value': i})"
                },
            ),
        ],
    )
    data = first.state.agent_context_state.get(discovery._DISCOVERY_ID).data
    assert data == {"called": [{"name": "math__double", "tool_id": "tool/math/double"}]}
    assert len(json.dumps(data).encode()) < 1200
    monkeypatch.setattr(discovery, "_MAX_CONTEXT_BYTES", 1)
    second, snapshots = await _run_calls(
        capabilities, [("summarize", {"content": "Continue"})], previous_state=first.state
    )
    assert not any(isinstance(p, ToolReturnPart) for msg in snapshots[-1][0] for p in msg.parts)
    assert second.state.agent_context_state.get(discovery._DISCOVERY_ID).data == {"called": []}
    monkeypatch.setattr(discovery, "_MAX_CONTEXT_BYTES", 65536)
    _, snapshots = await _run_calls(capabilities, [("summarize", {"content": "Continue"})], previous_state=second.state)
    assert not any(isinstance(p, ToolReturnPart) for msg in snapshots[-1][0] for p in msg.parts)


async def test_overlapping_native_searches_rebuild_each_called_schema_once():
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    def alpha() -> int:
        return 1

    def beta() -> int:
        return 2

    def gamma() -> int:
        return 3

    source = Capability(
        tools=[
            Tool(alpha, description="a" * 40_000, defer_loading=True),
            Tool(beta, defer_loading=True),
            Tool(gamma, defer_loading=True),
        ]
    )
    _, snapshots = await _run_calls(
        (source, ToolSearch(strategy="keywords"), HandoffCapability()),
        [
            ("search_tools", {"queries": ["alpha beta"]}),
            ("alpha", {}),
            ("beta", {}),
            ("search_tools", {"queries": ["alpha gamma"]}),
            ("alpha", {}),
            ("gamma", {}),
            ("summarize", {"content": "Continue"}),
        ],
    )
    messages, info = snapshots[-1]
    retained = [part for message in messages for part in message.parts if isinstance(part, ToolSearchReturnPart)]
    assert len(retained) == 1
    assert {match["name"] for match in retained[0].discovered_tools} == {"alpha", "beta", "gamma"}
    model = OpenAIResponsesModel("gpt-5.4", provider=OpenAIProvider(api_key="test-only"))
    settings, parameters = model.prepare_request({}, info.model_request_parameters)
    _, wire = await model._map_messages(messages, settings or {}, parameters)
    outputs = [item for item in wire if item.get("type") == "tool_search_output"]
    assert len(outputs) == 1
    assert len(json.dumps(outputs).encode()) < discovery._MAX_CONTEXT_BYTES


@pytest.mark.parametrize("mode", ["native", "proxy", "nested"])
async def test_multi_tool_search_retains_only_called_member(monkeypatch, mode):
    monkeypatch.setattr(discovery, "_MAX_TOOLS", 1)
    proxy = mode != "native"
    capabilities = (_source([], proxy=proxy), ToolSearch(strategy="keywords"), CodeActCapability(), HandoffCapability())
    if mode == "native":
        calls = [("search_tools", {"queries": ["double triple"]}), ("double", {"value": 1})]
    elif mode == "proxy":
        calls = [
            ("discover_tools", {"query": ""}),
            ("call_tool", {"group": "math", "tool": "double", "arguments": {"value": 1}}),
        ]
    else:
        calls = [
            (
                "run_code",
                {
                    "code": "await discover_tools(query='')\nawait call_tool(group='math', tool='double', arguments={'value': 1})"
                },
            )
        ]
    calls.append(("summarize", {"content": "Continue"}))
    _, snapshots = await _run_calls(capabilities, calls)
    messages, info = snapshots[-1]
    results = [p for msg in messages for p in msg.parts if isinstance(p, ToolReturnPart)]
    assert len(results) == 1
    retained_calls = [p for msg in messages for p in msg.parts if isinstance(p, ToolCallPart)]
    assert len(retained_calls) == 1
    marker = "[query omitted during context replacement]"
    assert retained_calls[0].args_as_dict() == ({"query": marker, "detail": "full"} if proxy else {"queries": [marker]})
    assert "Original search queries were not retained" in ModelMessagesTypeAdapter.dump_json(messages).decode()
    if proxy:
        assert [entry["tool"] for entry in results[0].content["tools"]] == ["double"]
        assert results[0].content["partial"] is True
        assert not info.model_request_parameters.revealed_tool_names
    else:
        assert info.model_request_parameters.revealed_tool_names == {"double"}
    assert "triple" not in ModelMessagesTypeAdapter.dump_json(messages).decode()


async def test_search_noise_cannot_evict_a_called_tool(monkeypatch):
    monkeypatch.setattr(discovery, "_MAX_TOOLS", 1)
    result, snapshots = await _run_calls(
        (_source([]), ToolSearch(strategy="keywords"), CodeActCapability(), HandoffCapability()),
        [
            ("search_tools", {"queries": ["double"]}),
            ("double", {"value": 1}),
            *[("search_tools", {"queries": ["triple"]}) for _ in range(39)],
            ("summarize", {"content": "Continue"}),
        ],
    )
    assert snapshots[-1][1].model_request_parameters.revealed_tool_names == {"double"}
    assert result.state.agent_context_state.get(discovery._DISCOVERY_ID).data == {
        "called": [{"name": "double", "tool_id": "tool/math/double"}]
    }


async def test_call_recency_controls_eviction(monkeypatch):
    monkeypatch.setattr(discovery, "_MAX_TOOLS", 1)
    _, snapshots = await _run_calls(
        (_source([]), ToolSearch(strategy="keywords"), HandoffCapability()),
        [
            ("search_tools", {"queries": ["double triple"]}),
            ("double", {"value": 1}),
            ("triple", {"value": 1}),
            ("search_tools", {"queries": ["double"]}),
            ("summarize", {"content": "Continue"}),
        ],
    )
    assert snapshots[-1][1].model_request_parameters.revealed_tool_names == {"triple"}


@pytest.mark.parametrize("nested", [False, True])
async def test_wrapped_proxy_search_does_not_break_called_tool_retention(nested):
    def lookup() -> int:
        return 42

    description = "Detailed lookup contract. " * 900
    source = CodeActPolicyToolset(
        FunctionToolset([Tool(lookup, description=description)], id="lookup-source"),
        CodeActToolPolicy(default=True),
    )
    capabilities = (
        ToolSearchCapability(groups={"db": ToolSearchGroup(source, "Database")}),
        CodeActCapability(),
        HandoffCapability(),
    )
    search = ("discover_tools", {"query": "lookup", "detail": "full"})
    if nested:
        search = ("run_code", {"code": "await discover_tools(query='lookup', detail='full')"})
    result, snapshots = await _run_calls(
        capabilities,
        [
            search,
            ("call_tool", {"group": "db", "tool": "lookup", "arguments": {}}),
            ("summarize", {"content": "Continue"}),
        ],
    )
    if not nested:
        original = next(p for msg in snapshots[1][0] for p in msg.parts if isinstance(p, ToolReturnPart))
        assert "result" in original.content  # Generic output bounding wraps this large search.
    retained = next(p for msg in snapshots[-1][0] for p in msg.parts if isinstance(p, ToolReturnPart))
    assert retained.content["tools"][0]["description"] == description
    assert len(json.dumps(result.state.agent_context_state.get(discovery._DISCOVERY_ID).data)) < 200


async def test_failed_argument_validation_does_not_admit_a_tool():
    result, snapshots = await _run_calls(
        (_source([]), ToolSearch(strategy="keywords"), HandoffCapability()),
        [
            ("search_tools", {"queries": ["double"]}),
            ("double", {"value": "not-an-integer"}),
            ("summarize", {"content": "Continue"}),
        ],
    )
    assert not snapshots[-1][1].model_request_parameters.revealed_tool_names
    assert result.state.agent_context_state.get(discovery._DISCOVERY_ID) is None


@pytest.mark.parametrize("approved", [False, True])
async def test_approval_only_retains_tools_that_execute(approved):
    effects = []

    def lookup() -> int:
        effects.append("executed")
        return 42

    class ResolveApproval(AbstractCapability):
        async def handle_deferred_tool_calls(self, ctx, *, requests):
            return DeferredToolResults(approvals={call.tool_call_id: approved for call in requests.approvals})

    result, snapshots = await _run_calls(
        (
            Capability(tools=[Tool(lookup, defer_loading=True, requires_approval=True)]),
            ToolSearch(strategy="keywords"),
            HandoffCapability(),
            ResolveApproval(),
        ),
        [
            ("search_tools", {"queries": ["lookup"]}),
            ("lookup", {}),
            ("summarize", {"content": "Continue"}),
        ],
    )
    assert effects == (["executed"] if approved else [])
    assert snapshots[-1][1].model_request_parameters.revealed_tool_names == ({"lookup"} if approved else set())
    if not approved:
        assert result.state.agent_context_state.get(discovery._DISCOVERY_ID) is None


@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize("runtime_function", [False, True])
@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("failed", [False, True])
async def test_deferred_completion_retains_actual_tool_but_failure_does_not(proxy, runtime_function, inline, failed):
    returned = ToolFailed("External execution failed") if failed else {"value": 42}

    class ResolveExternal(AbstractCapability):
        async def handle_deferred_tool_calls(self, ctx, *, requests):
            if inline:
                return DeferredToolResults(calls={call.tool_call_id: returned for call in requests.calls})
            return None

    effects = []

    def lookup():
        effects.append("started")
        raise CallDeferred()

    source = (
        FunctionToolset([Tool(lookup, defer_loading=not proxy)], id="external-source")
        if runtime_function
        else ExternalToolset([ToolDefinition(name="lookup", defer_loading=not proxy)], id="external-source")
    )
    index = 0
    snapshots = []
    calls = [
        ("discover_tools", {"query": "lookup"}) if proxy else ("search_tools", {"queries": ["lookup"]}),
        ("call_tool", {"group": "editor", "tool": "lookup", "arguments": {}}) if proxy else ("lookup", {}),
        ("summarize", {"content": "Continue after external result"}),
    ]

    def model(messages, info):
        nonlocal index
        snapshots.append((deepcopy(messages), info))
        if index == len(calls):
            return ModelResponse(parts=[TextPart("done")])
        name, args = calls[index]
        index += 1
        return _call(name, args, index)

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=_scripted_model(model),
        output_type=str,
        capabilities=(
            *(
                (ToolSearchCapability(groups={"editor": ToolSearchGroup(source, "Editor tools")}),)
                if proxy
                else (Capability(toolsets=[source]), ToolSearch(strategy="keywords"))
            ),
            HandoffCapability(),
            ResolveExternal(),
        ),
    )
    result = await executable.run("Look up a value")
    if not inline:
        assert result.deferred is not None
        assert result.state.agent_context_state.get(discovery._DISCOVERY_ID) is None
        state = HarnessState.model_validate_json(result.state.model_dump_json())
        adapter = TypeAdapter(DeferredToolRequests)
        pending = adapter.validate_json(adapter.dump_json(result.deferred))
        result = await executable.run(
            previous_state=state,
            deferred_resume=DeferredToolResume(
                pending,
                pending.build_results(calls={pending.calls[0].target_call.tool_call_id: returned}),
            ),
        )
    assert result.output_or_raise() == "done"
    assert effects == (["started"] if runtime_function else [])
    returns = [
        part
        for message in snapshots[-2][0]
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id == "step-2"
    ]
    assert len(returns) == 1
    assert returns[0].tool_name == ("call_tool" if proxy else "lookup")
    assert returns[0].outcome == ("failed" if failed else "success")
    if proxy:
        entries = [
            entry
            for message in snapshots[-1][0]
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict) and part.content.get("retained")
            for entry in part.content["tools"]
        ]
        assert [entry["tool"] for entry in entries] == ([] if failed else ["lookup"])
        if not failed:
            assert entries[0]["group"] == "editor"
            assert entries[0]["parameters_json_schema"]["type"] == "object"
    else:
        assert snapshots[-1][1].model_request_parameters.revealed_tool_names == (set() if failed else {"lookup"})
    saved = result.state.agent_context_state.get(discovery._DISCOVERY_ID)
    if failed:
        assert saved is None
    else:
        assert saved.data["called"] == [
            {"name": "editor__lookup" if proxy else "lookup", "tool_id": "tool/external-source/lookup"}
        ]


async def test_proxy_call_without_search_reconstructs_current_schema():
    _, snapshots = await _run_calls(
        (_source([], proxy=True), HandoffCapability()),
        [
            ("call_tool", {"group": "math", "tool": "double", "arguments": {"value": 1}}),
            ("summarize", {"content": "Continue"}),
        ],
    )
    retained = next(p for msg in snapshots[-1][0] for p in msg.parts if isinstance(p, ToolReturnPart))
    assert [entry["tool"] for entry in retained.content["tools"]] == ["double"]
    assert retained.content["tools"][0]["parameters_json_schema"]["required"] == ["value"]


@pytest.mark.parametrize("nested", [False, True])
async def test_describe_without_execution_retains_nothing(nested):
    describe = ("describe_tool", {"group": "math", "tool": "double"})
    if nested:
        describe = ("run_code", {"code": "await describe_tool(group='math', tool='double')"})
    result, snapshots = await _run_calls(
        (_source([], proxy=True), CodeActCapability(), HandoffCapability()),
        [describe, ("summarize", {"content": "Continue"})],
    )
    assert not any(isinstance(p, ToolReturnPart) for msg in snapshots[-1][0] for p in msg.parts)
    assert result.state.agent_context_state.get(discovery._DISCOVERY_ID) is None
