from __future__ import annotations

import json
from dataclasses import replace

import pytest
from a13n_harness import DeferredToolRequests, DeferredToolResume, DefinitionError, HarnessBuilder, RunError
from a13n_harness.capabilities import ProxyToolSearchConfig, ToolSearchCapability, ToolSearchGroup
from pydantic import TypeAdapter
from pydantic_ai import Tool, ToolApproved, ToolDefinition, ToolFailed
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability, HandleDeferredToolCalls
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import CombinedToolset, ExternalToolset, FunctionToolset

pytestmark = pytest.mark.anyio


def _build(source, *, proxy=True, handler=None, calls=None, extra=(), plan=False, model_override=None):
    calls = calls or [
        ("invoke", {"group": "tools", "tool": "change", "arguments": {"value": 3}})
        if proxy
        else ("change", {"value": 3})
    ]

    async def model(messages, info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if len(returns) == len(calls):
            yield "done"
        else:
            yield {
                i: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"model-{i}")
                for i, (name, args) in enumerate(calls)
            }

    capabilities = [
        ToolSearchCapability(
            groups={"tools": ToolSearchGroup(source, "Test operations")},
            config=ProxyToolSearchConfig(call_name="invoke"),
        )
        if proxy
        else Capability(toolsets=[source])
    ]
    if handler is not None:
        capabilities.append(HandleDeferredToolCalls(handler))
    if plan:
        from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection

        capabilities = [source]
        search = ToolSearchPlan(
            groups={"tools": ToolSearchSelection("Test operations", capabilities=(source,))},
            config=ProxyToolSearchConfig(call_name="invoke"),
        )
    else:
        search = None
    return HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model_override or model),
        capabilities=[*capabilities, *extra],
        tool_search=search,
    )


def _roundtrip(requests):
    adapter = TypeAdapter(DeferredToolRequests)
    return adapter.validate_json(adapter.dump_json(requests))


@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize("decision", [True, False, ToolApproved(override_args={"value": "7"})])
async def test_approval_roundtrip_resumes_the_target_once(proxy, decision):
    effects = []

    def change(value: int) -> int:
        effects.append(value)
        return value * 2

    def build():
        return _build(FunctionToolset([Tool(change, requires_approval=True)], id="operations"), proxy=proxy)

    first = await build().run("go")
    assert first.status == "suspended"
    assert first.state is not None and first.deferred is not None
    assert effects == []
    requests = _roundtrip(first.deferred)
    item = requests.approvals[0]
    assert item.model_call.tool_call_id == "model-0"
    assert item.forwarded is proxy
    assert item.target_call.tool_name == ("tools__change" if proxy else "change")
    assert item.target_identity == "tool/operations/change"
    resumed = await build().run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            requests, requests.build_results(approvals={item.target_call.tool_call_id: decision})
        ),
    )
    assert resumed.output_or_raise() == "done"
    assert effects == ([] if decision is False else [7 if isinstance(decision, ToolApproved) else 3])
    returns = [
        part
        for message in resumed.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(returns) == 1
    assert returns[0].tool_call_id == "model-0"
    assert returns[0].tool_name == ("invoke" if proxy else "change")
    assert returns[0].outcome == ("denied" if decision is False else "success")
    assert resumed.usage.tool_calls == (0 if decision is False else 2 if proxy else 1)


@pytest.mark.parametrize("value", ["A123", ToolFailed("Client cancelled")])
async def test_runtime_deferred_result_resumes_original_call_without_dispatch(value):
    from pydantic_ai import CallDeferred

    effects = []

    def change(value: int):
        effects.append(value)
        raise CallDeferred()

    source = FunctionToolset([change], id="operations")
    first = await _build(source).run("go")
    assert first.status == "suspended" and first.deferred is not None
    requests = _roundtrip(first.deferred)
    item = requests.calls[0]
    assert item.target_call.tool_name == "tools__change"
    assert item.model_call.tool_name == "invoke"
    assert item.target_call.tool_call_id != item.model_call.tool_call_id
    resumed = await _build(source).run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            requests, requests.build_results(calls={item.target_call.tool_call_id: value})
        ),
    )
    assert resumed.output_or_raise() == "done"
    assert resumed.usage.tool_calls == 0
    assert effects == [3]
    result = next(
        part
        for message in resumed.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )
    assert result.tool_call_id == "model-0" and result.tool_name == "invoke"
    assert result.outcome == ("failed" if isinstance(value, ToolFailed) else "success")


async def test_unresolved_inline_handler_observes_only_actual_target_once():
    seen = []

    def change(value: int):
        raise AssertionError("Unapproved effect")

    async def handler(ctx, requests):
        seen.extend(requests.approvals)
        return None

    first = await _build(FunctionToolset([Tool(change, requires_approval=True)]), handler=handler).run("go")
    assert first.status == "suspended" and first.deferred is not None
    assert seen == [first.deferred.approvals[0].target_call]


@pytest.mark.parametrize("change", ["missing", "identity", "association"])
async def test_resume_rejects_changed_target_before_effect(change):
    effects = []

    def change_tool(value: int):
        effects.append(value)
        return value

    tool = Tool(change_tool, name="change", requires_approval=True)
    first = await _build(FunctionToolset([tool], id="operations")).run("go")
    assert first.deferred is not None
    requests = first.deferred
    if change == "association":
        item = requests.approvals[0]
        requests.approvals[0] = replace(item, target_call=replace(item.target_call, args={"value": 9}))
    source = FunctionToolset(
        [] if change == "missing" else [tool], id="other" if change == "identity" else "operations"
    )
    with pytest.raises(DefinitionError) as error:
        await _build(source).run(
            previous_state=first.state,
            deferred_resume=DeferredToolResume(requests, requests.build_results(approve_all=True)),
        )
    assert error.value.code == "deferred_surface_mismatch"
    assert effects == []


async def test_wrong_reply_id_and_consumed_reply_are_rejected():
    def change(value: int):
        return value

    executable = _build(FunctionToolset([Tool(change, requires_approval=True)]))
    first = await executable.run("go")
    assert first.deferred is not None
    requests = first.deferred
    with pytest.raises(ValueError):
        requests.build_results(approvals={"model-0": True})
    resume = DeferredToolResume(requests, requests.build_results(approve_all=True))
    second = await executable.run(previous_state=first.state, deferred_resume=resume)
    assert second.status == "completed"
    with pytest.raises(RunError):
        executable.stream(previous_state=second.state, deferred_resume=resume)


@pytest.mark.parametrize("name", ["change", "tools__change", "_change"])
@pytest.mark.parametrize("value", ["selected", ToolFailed("Client cancelled")])
async def test_grouped_client_declaration_waits_and_resumes(name, value):
    from a13n_harness.tools.client import (
        ClientToolDefinition,
        ClientToolsCapability,
        ClientToolsetDefinition,
        ClientToolsSpec,
    )

    source = ClientToolsCapability(
        spec=ClientToolsSpec(
            default_toolsets=(
                ClientToolsetDefinition(
                    toolset_id="editor",
                    tools=(
                        ClientToolDefinition(
                            name=name,
                            description="Read selection",
                            parameters_json_schema={"type": "object"},
                            metadata={"ui": "editor"},
                        ),
                    ),
                ),
            )
        )
    )
    from a13n_harness.tools.client import CLIENT_TOOL_MARKER_KEY, client_tool_name

    calls = [("invoke", {"group": "tools", "tool": name, "arguments": {}})]
    first = await _build(source, plan=True, calls=calls).run("go")
    assert first.status == "suspended" and first.deferred is not None
    requests = _roundtrip(first.deferred)
    item = requests.calls[0]
    target = item.target_call
    assert target.tool_name == f"tools__{name}"
    assert item.model_call.tool_name == "invoke"
    assert item.target_identity == f"tool/editor/{name}"
    assert target.tool_call_id != item.model_call.tool_call_id
    assert requests.metadata[target.tool_call_id] == {
        CLIENT_TOOL_MARKER_KEY: {"declared_name": name, "toolset_id": "editor"}
    }
    assert client_tool_name(target, requests.metadata[target.tool_call_id]) == name
    resumed = await _build(source, plan=True, calls=calls).run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, requests.build_results(calls={target.tool_call_id: value})),
    )
    assert resumed.output_or_raise() == "done"
    assert resumed.usage.tool_calls == 0
    result = next(
        part
        for message in resumed.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )
    assert result.tool_call_id == "model-0" and result.tool_name == "invoke"
    assert result.outcome == ("failed" if isinstance(value, ToolFailed) else "success")


@pytest.mark.parametrize(
    "resume_surface, value, accepted",
    [
        ("same", "selected", True),
        ("different-id", "selected", False),
        ("different-name", "selected", False),
        ("empty", "selected", False),
        ("empty", ToolFailed("Client disconnected"), True),
    ],
)
async def test_grouped_client_run_override_survives_serialized_resume(resume_surface, value, accepted):
    from a13n_harness import HarnessState, RunBindings
    from a13n_harness.tools.client import (
        ClientToolDefinition,
        ClientToolsCapability,
        ClientToolsetDefinition,
        ClientToolsSpec,
    )

    def toolset(toolset_id="editor", name="selection"):
        return ClientToolsetDefinition(
            toolset_id=toolset_id,
            tools=(
                ClientToolDefinition(
                    name=name, description="Read selection", parameters_json_schema={"type": "object"}
                ),
            ),
        )

    def build():
        source = ClientToolsCapability(
            spec=ClientToolsSpec(default_toolsets=(toolset(name="default_selection"),), allow_run_override=True)
        )
        return _build(source, plan=True, calls=[("invoke", {"group": "tools", "tool": "selection", "arguments": {}})])

    first = await build().run("go", bindings=RunBindings.embedded(client_toolsets=(toolset(),)))
    assert first.status == "suspended" and first.deferred is not None and first.state is not None
    requests = _roundtrip(first.deferred)
    state = HarnessState.model_validate_json(first.state.model_dump_json())
    (item,) = requests.calls
    assert item.target_identity == "tool/editor/selection"
    assert item.target_call.tool_name == "tools__selection"
    assert item.model_call.tool_call_id == "model-0"
    assert item.target_call.tool_call_id != item.model_call.tool_call_id
    toolsets = {
        "same": (toolset(),),
        "different-id": (toolset(toolset_id="other-editor"),),
        "different-name": (toolset(name="other_selection"),),
        "empty": (),
    }[resume_surface]
    executable = build()
    bindings = RunBindings.embedded(client_toolsets=toolsets)
    resume = DeferredToolResume(requests, requests.build_results(calls={item.target_call.tool_call_id: value}))
    if not accepted:
        with pytest.raises(DefinitionError) as error:
            await executable.run(previous_state=state, bindings=bindings, deferred_resume=resume)
        assert error.value.code == "deferred_surface_mismatch"
        return

    result = await executable.run(previous_state=state, bindings=bindings, deferred_resume=resume)
    assert result.output_or_raise() == "done"
    assert result.usage.tool_calls == 0
    returns = [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert [(part.tool_call_id, part.tool_name, part.content, part.outcome) for part in returns] == [
        (
            "model-0",
            "invoke",
            value.message if isinstance(value, ToolFailed) else value,
            "failed" if isinstance(value, ToolFailed) else "success",
        )
    ]


async def test_grouped_client_batch_correlates_results_after_serialized_resume():
    from a13n_harness import HarnessState
    from a13n_harness.tools.client import (
        ClientToolDefinition,
        ClientToolsCapability,
        ClientToolsetDefinition,
        ClientToolsSpec,
    )

    names = ("selection", "document", "clipboard")

    def build():
        source = ClientToolsCapability(
            spec=ClientToolsSpec(
                default_toolsets=(
                    ClientToolsetDefinition(
                        toolset_id="editor",
                        tools=tuple(
                            ClientToolDefinition(name=name, description=name, parameters_json_schema={"type": "object"})
                            for name in names
                        ),
                    ),
                )
            )
        )
        calls = [("invoke", {"group": "tools", "tool": name, "arguments": {}}) for name in names]
        return _build(source, plan=True, calls=calls)

    first = await build().run("go")
    assert first.status == "suspended" and first.deferred is not None and first.state is not None
    requests = _roundtrip(first.deferred)
    state = HarnessState.model_validate_json(first.state.model_dump_json())
    assert len(requests.calls) == 3 and not requests.approvals
    assert len({item.target_call.tool_call_id for item in requests.calls}) == 3
    assert [(item.model_call.tool_call_id, item.target_identity) for item in requests.calls] == [
        (f"model-{i}", f"tool/editor/{name}") for i, name in enumerate(names)
    ]
    values = {
        "tools__selection": "selected text",
        "tools__document": {"title": "Draft"},
        "tools__clipboard": ToolFailed("Clipboard unavailable"),
    }
    # Supply the complete batch in reverse order: target IDs, not insertion order, own correlation.
    results = requests.build_results(
        calls={item.target_call.tool_call_id: values[item.target_call.tool_name] for item in reversed(requests.calls)}
    )
    resumed = await build().run(previous_state=state, deferred_resume=DeferredToolResume(requests, results))
    assert resumed.output_or_raise() == "done"
    assert resumed.usage.tool_calls == 0
    returns = [
        part
        for message in resumed.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert [(part.tool_call_id, part.tool_name, part.content, part.outcome) for part in returns] == [
        ("model-0", "invoke", "selected text", "success"),
        ("model-1", "invoke", {"title": "Draft"}, "success"),
        ("model-2", "invoke", "Clipboard unavailable", "failed"),
    ]


@pytest.mark.parametrize(
    "declaration, policy, rules, suspended",
    [
        ("allow", "inherit", {}, True),
        ("deny", "inherit", {}, False),
        ("allow", "deny", {}, False),
        ("deny", "allow", {}, True),
        ("deny", "inherit", {"*": "allow"}, True),
        ("allow", "inherit", {"*": "allow", "tool/editor/*": "deny"}, False),
        ("deny", "inherit", {"*": "deny", "tool/editor/*": "deny", "tool/editor/selection": "allow"}, True),
        ("allow", "inherit", {"tool/tool-search-controls/invoke": "deny"}, False),
    ],
)
async def test_grouped_client_permission_uses_target_identity(declaration, policy, rules, suspended):
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
    from a13n_harness.tools.client import (
        ClientToolDefinition,
        ClientToolsCapability,
        ClientToolsetDefinition,
        ClientToolsSpec,
    )

    source = ClientToolsCapability(
        spec=ClientToolsSpec(
            default_toolsets=(
                ClientToolsetDefinition(
                    toolset_id="editor",
                    tools=(
                        ClientToolDefinition(
                            name="selection",
                            description="Read selection",
                            parameters_json_schema={"type": "object"},
                            permission=declaration,
                        ),
                    ),
                ),
            )
        )
    )
    result = await _build(
        source,
        plan=True,
        calls=[("invoke", {"group": "tools", "tool": "selection", "arguments": {}})],
        # Allow the envelope independently so target-denial cases reach the client declaration.
        extra=(
            ToolPermissionsCapability(
                ToolPermissions(default=policy, rules={"tool/tool-search-controls/invoke": "allow", **rules})
            ),
        ),
    ).run("go")
    if suspended:
        assert result.status == "suspended" and result.deferred is not None
        (item,) = result.deferred.calls
        assert item.target_identity == "tool/editor/selection"
        assert item.target_call.tool_name == "tools__selection"
        assert not result.deferred.approvals
    else:
        assert result.output_or_raise() == "done" and result.deferred is None
        returns = [
            part
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert [(part.tool_call_id, part.outcome, part.content) for part in returns] == [
            ("model-0", "failed", "Tool invocation is denied by its permission configuration.")
        ]


@pytest.mark.parametrize("external_first", [False, True])
async def test_proxy_uses_uniform_names_for_function_and_external_targets(external_first):
    def change(value: int):
        return value * 2

    sources = [
        FunctionToolset([change], id="operations"),
        ExternalToolset([ToolDefinition(name="tools__change")], id="editor"),
    ]
    if external_first:
        sources.reverse()
    calls = [
        ("invoke", {"group": "tools", "tool": "change", "arguments": {"value": 3}}),
        ("invoke", {"group": "tools", "tool": "tools__change", "arguments": {}}),
    ]
    executable = _build(CombinedToolset(sources), calls=calls)
    first = await executable.run("go")
    assert first.status == "suspended" and first.deferred is not None
    item = first.deferred.calls[0]
    assert item.target_call.tool_name == "tools__tools__change"
    assert first.deferred.metadata[item.target_call.tool_call_id]["a13n.harness.client-tool"] == {
        "declared_name": "tools__change",
        "toolset_id": "editor",
    }
    resumed = await executable.run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            first.deferred, first.deferred.build_results(calls={item.target_call.tool_call_id: "selected"})
        ),
    )
    assert resumed.output_or_raise() == "done"
    returns = [
        part
        for message in resumed.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert [part.content for part in returns] == [6, "selected"]


async def test_forwarded_approval_rechecks_current_permission():
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

    effects = []

    def change(value: int):
        effects.append(value)
        return value

    executable = _build(FunctionToolset([Tool(change, requires_approval=True)], id="operations"))
    first = await executable.run("go")
    assert first.deferred is not None
    denied = _build(
        FunctionToolset([Tool(change, requires_approval=True)], id="operations"),
        extra=(ToolPermissionsCapability(ToolPermissions(rules={"tool/operations/change": "deny"})),),
    )
    resumed = await denied.run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(first.deferred, first.deferred.build_results(approve_all=True)),
    )
    assert resumed.output_or_raise() == "done"
    assert effects == []


async def test_runtime_deferred_target_retains_function_identity():
    from pydantic_ai import CallDeferred

    def change(value: int):
        raise CallDeferred(metadata={"receipt": "job-1"})

    source = FunctionToolset([change], id="operations")
    first = await _build(source).run("go")
    assert first.deferred is not None
    item = first.deferred.calls[0]
    assert first.deferred.metadata[item.target_call.tool_call_id] == {
        "receipt": "job-1",
        "a13n.harness.deferred-function-id": "tool/operations/change",
    }
    resumed = await _build(source).run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            first.deferred, first.deferred.build_results(calls={item.target_call.tool_call_id: "done"})
        ),
    )
    assert resumed.output_or_raise() == "done"


@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize("stage", ["arguments", "validated", "execution", "wrapper"])
@pytest.mark.parametrize("remount", ["unchanged", "identity", "category"])
async def test_inline_approval_then_runtime_deferral_resumes_without_repeating_effect(proxy, stage, remount):
    from a13n_harness import HarnessState
    from pydantic_ai import CallDeferred

    effects, approvals = [], []

    def defer():
        raise CallDeferred(metadata={"receipt": "job-1", "a13n.harness.deferred-function-id": "not-the-target"})

    def validate(ctx, value):
        if ctx.tool_call_approved and stage == "arguments":
            defer()

    class Validation(Capability):
        async def after_tool_validate(self, ctx, *, call, tool_def, args):
            if ctx.tool_call_approved and call.tool_name.endswith("change") and stage == "validated":
                defer()
            return args

        async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
            result = await handler(args)
            if call.tool_name.endswith("change") and stage == "wrapper":
                defer()
            return result

    def change(value: int):
        effects.append(value)
        if stage == "execution":
            defer()
        return value

    async def handler(ctx, requests):
        approvals.append(requests)
        for call in requests.approvals:
            requests.metadata[call.tool_call_id] = {
                "handler": "approved",
                "a13n.harness.deferred-function-id": "not-the-target",
            }
        return requests.build_results(approve_all=True)

    def build(*, resumed=False):
        source = (
            ExternalToolset([ToolDefinition(name="change")], id="operations")
            if resumed and remount == "category"
            else FunctionToolset(
                [Tool(change, requires_approval=True, args_validator=validate)],
                id="replacement" if resumed and remount == "identity" else "operations",
            )
        )
        return _build(source, proxy=proxy, handler=handler, extra=(Validation(),))

    first = await build().run("go")
    assert first.status == "suspended" and first.deferred is not None
    requests = _roundtrip(first.deferred)
    item = requests.calls[0]
    assert requests.metadata[item.target_call.tool_call_id]["receipt"] == "job-1"
    assert requests.metadata[item.target_call.tool_call_id]["handler"] == "approved"
    assert requests.metadata[item.target_call.tool_call_id]["a13n.harness.deferred-function-id"] == (
        "tool/operations/change"
    )
    state_adapter = TypeAdapter(HarnessState)
    state = state_adapter.validate_json(state_adapter.dump_json(first.state))
    resume = DeferredToolResume(requests, requests.build_results(calls={item.target_call.tool_call_id: "job finished"}))
    if remount == "unchanged":
        resumed = await build(resumed=True).run(previous_state=state, deferred_resume=resume)
        assert resumed.output_or_raise() == "done"
    else:
        with pytest.raises(DefinitionError) as error:
            await build(resumed=True).run(previous_state=state, deferred_resume=resume)
        assert error.value.code == "deferred_surface_mismatch"
    assert effects == ([3] if stage in {"execution", "wrapper"} else [])
    assert len(approvals) == 1 and approvals[0].approvals == [item.target_call]


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize(
    ("proxy", "scenario"),
    [
        (proxy, scenario)
        for proxy in (False, True)
        for scenario in ("external-failure", "external-success", "function-failure")
    ],
)
async def test_missing_target_only_accepts_external_failure(proxy, recovery, scenario):
    from pydantic_ai import CallDeferred

    effects = []

    def change(value: int):
        effects.append(value)
        raise CallDeferred()

    source = (
        FunctionToolset([change], id="operations")
        if scenario == "function-failure"
        else ExternalToolset([ToolDefinition(name="change")], id="editor")
    )
    first = await _build(source, proxy=proxy).run("go")
    assert first.deferred is not None
    requests = _roundtrip(first.deferred)
    item = requests.calls[0]
    value = "finished" if scenario == "external-success" else ToolFailed("Client disconnected")
    resume = DeferredToolResume(
        requests, requests.build_results(calls={item.target_call.tool_call_id: value}), recovery=recovery
    )
    rebuilt = _build(FunctionToolset([], id="empty"), proxy=proxy)
    if scenario != "external-failure":
        with pytest.raises(DefinitionError) as error:
            await rebuilt.run(previous_state=first.state, deferred_resume=resume)
        assert error.value.code == "deferred_surface_mismatch"
    else:
        resumed = await rebuilt.run(previous_state=first.state, deferred_resume=resume)
        assert resumed.output_or_raise() == "done"
        returns = [
            part
            for message in resumed.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert len(returns) == 1
        assert returns[0].tool_call_id == item.model_call.tool_call_id
        assert returns[0].outcome == "failed" and returns[0].content == "Client disconnected"
        assert resumed.usage.tool_calls == 0
    assert effects == ([3] if scenario == "function-failure" else [])


@pytest.mark.parametrize("damage", ["partial", "duplicate", "category", "direct-mismatch"])
async def test_mixed_batch_rejects_invalid_correlations(damage):
    from a13n_harness import DeferredToolResults

    def change(value: int):
        raise AssertionError("Unapproved effect")

    source = FunctionToolset([Tool(change, requires_approval=True)], id="operations")
    external = Capability(toolsets=[ExternalToolset([ToolDefinition(name="select")], id="editor")])
    calls = [("invoke", {"group": "tools", "tool": "change", "arguments": {"value": 3}}), ("select", {})]
    executable = _build(source, calls=calls, extra=(external,))
    first = await executable.run("go")
    assert first.deferred is not None
    requests = first.deferred
    call, approval = requests.calls[0], requests.approvals[0]
    results = DeferredToolResults(
        calls={call.target_call.tool_call_id: "selected"}, approvals={approval.target_call.tool_call_id: True}
    )
    if damage == "partial":
        results.calls.clear()
    elif damage == "duplicate":
        requests.calls.append(call)
    elif damage == "category":
        requests.approvals.append(call)
    else:
        requests.calls[0] = replace(call, target_call=replace(call.target_call, args={"changed": True}))
    with pytest.raises(RunError):
        executable.stream(previous_state=first.state, deferred_resume=DeferredToolResume(requests, results))


@pytest.mark.parametrize("external_disconnected", [False, True])
@pytest.mark.parametrize("grouped_external", [False, True])
async def test_cancelled_mixed_resume_retains_external_fact_without_replaying_approval(
    external_disconnected, grouped_external
):
    import asyncio

    from a13n_harness import HarnessEvent, HarnessState
    from pydantic_ai.capabilities import AbstractCapability

    started = asyncio.Event()
    effects = []

    class Pause(AbstractCapability):
        async def before_tool_execute(self, ctx, *, call, tool_def, args):
            if call.tool_name == "tools__change":
                started.set()
                await asyncio.Event().wait()
            return args

    def change(value: int):
        effects.append(value)
        return value

    source = FunctionToolset([Tool(change, requires_approval=True)], id="operations")
    external = ExternalToolset([ToolDefinition(name="select")], id="editor")
    calls = [
        ("invoke", {"group": "tools", "tool": "change", "arguments": {"value": 3}}),
        ("invoke", {"group": "tools", "tool": "select", "arguments": {}}) if grouped_external else ("select", {}),
    ]

    def build(*, disconnected=False, extra=()):
        grouped = CombinedToolset([source, external]) if grouped_external and not disconnected else source
        direct = (Capability(toolsets=[external]),) if not grouped_external and not disconnected else ()
        return _build(grouped, calls=calls, extra=(*direct, *extra))

    first = await build().run("go")
    assert first.deferred is not None
    requests = _roundtrip(first.deferred)
    external_result = ToolFailed("Client disconnected") if external_disconnected else "selected once"
    accepted = DeferredToolResume(
        requests,
        requests.build_results(approve_all=True, calls={requests.calls[0].target_call.tool_call_id: external_result}),
    )
    async with build(extra=(Pause(),)).stream(previous_state=first.state, deferred_resume=accepted) as stream:

        async def drain():
            async for item in stream:
                if not isinstance(item, HarnessEvent):
                    return item.result
            raise AssertionError("Missing terminal")

        task = asyncio.create_task(drain())
        await asyncio.wait_for(started.wait(), 5)
        stream.cancel()
        cancelled = await asyncio.wait_for(task, 5)
        pending = stream.pending_deferred_input
    assert cancelled.status == "cancelled" and cancelled.state is not None and pending is not None
    restored = HarnessState.model_validate_json(cancelled.state.model_dump_json())
    recovered = await build(disconnected=external_disconnected).run(
        previous_state=restored, deferred_resume=replace(pending, recovery=True)
    )
    assert recovered.output_or_raise() == "done" and effects == []
    returns = [
        part
        for message in recovered.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(returns) == 2
    external_return = next(part for part in returns if part.tool_call_id == "model-1")
    assert external_return.content == ("Client disconnected" if external_disconnected else "selected once")
    assert external_return.outcome == ("failed" if external_disconnected else "success")


async def test_approved_target_can_defer_a_result_without_changing_its_association():
    from pydantic_ai import CallDeferred

    effects = []

    def change(value: int):
        effects.append(value)
        raise CallDeferred(metadata={"receipt": "job-1"})

    source = FunctionToolset([Tool(change, requires_approval=True)], id="operations")
    first = await _build(source).run("go")
    assert first.deferred is not None
    item = first.deferred.approvals[0]
    second = await _build(source).run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            first.deferred,
            first.deferred.build_results(
                approvals={item.target_call.tool_call_id: ToolApproved(override_args={"value": 8})}
            ),
        ),
    )
    assert second.status == "suspended" and second.deferred is not None
    assert second.deferred.calls[0] == item
    assert effects == [8]
    third = await _build(source).run(
        previous_state=second.state,
        deferred_resume=DeferredToolResume(
            second.deferred, second.deferred.build_results(calls={item.target_call.tool_call_id: "job finished"})
        ),
    )
    assert third.output_or_raise() == "done" and effects == [8]


async def test_consumed_approval_cannot_authorize_a_later_call_reusing_the_same_id():
    effects = []

    def change(value: int):
        effects.append(value)
        return value

    async def model(messages, info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if len(returns) < 2:
            yield {
                0: DeltaToolCall(
                    name="invoke",
                    json_args=json.dumps({"group": "tools", "tool": "change", "arguments": {"value": 3}}),
                    tool_call_id="reused",
                )
            }
        else:
            yield "done"

    source = FunctionToolset([Tool(change, requires_approval=True)], id="operations")
    first = await _build(source, model_override=model).run("go")
    assert first.deferred is not None
    second = await _build(source, model_override=model).run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(first.deferred, first.deferred.build_results(approve_all=True)),
    )
    assert second.status == "suspended" and second.deferred is not None
    assert effects == [3]
    assert second.deferred.approvals[0].target_call.tool_call_id != first.deferred.approvals[0].target_call.tool_call_id


async def test_grouped_external_inline_handler_receives_declared_name_metadata_once():
    from a13n_harness.tools.client import client_tool_name

    seen = []

    async def handler(ctx, requests):
        call = requests.calls[0]
        seen.append((call.tool_name, client_tool_name(call, requests.metadata[call.tool_call_id])))
        return requests.build_results(calls={call.tool_call_id: "selected"})

    result = await _build(ExternalToolset([ToolDefinition(name="change")], id="editor"), handler=handler).run("go")
    assert result.output_or_raise() == "done"
    assert seen == [("tools__change", "change")]


async def test_group_underscore_boundaries_do_not_merge_different_targets():
    groups = {
        "a_": ToolSearchGroup(ExternalToolset([ToolDefinition(name="b")], id="one"), "One"),
        "a": ToolSearchGroup(ExternalToolset([ToolDefinition(name="_b")], id="two"), "Two"),
        "a_u": ToolSearchGroup(ExternalToolset([ToolDefinition(name="b")], id="three"), "Three"),
    }
    calls = [
        ("invoke", {"group": group, "tool": name, "arguments": {}})
        for group, name in [("a_", "b"), ("a", "_b"), ("a_u", "b")]
    ]
    # The helper's empty group contributes no controls; the explicit groups own them.
    first = await _build(
        FunctionToolset(),
        proxy=False,
        calls=calls,
        extra=(ToolSearchCapability(groups=groups, config=ProxyToolSearchConfig(call_name="invoke")),),
    ).run("go")
    assert first.deferred is not None
    assert {item.target_call.tool_name for item in first.deferred.calls} == {"a_u__b", "a___b", "a_uu__b"}


async def test_resume_rejects_changed_client_routing_metadata():
    from a13n_harness.tools.client import CLIENT_TOOL_MARKER_KEY

    source = ExternalToolset([ToolDefinition(name="change")], id="editor")
    first = await _build(source).run("go")
    assert first.deferred is not None
    requests = _roundtrip(first.deferred)
    call = requests.calls[0].target_call
    requests.metadata[call.tool_call_id][CLIENT_TOOL_MARKER_KEY]["declared_name"] = "other"
    with pytest.raises(DefinitionError, match="pending client declaration changed"):
        await _build(source).run(
            previous_state=first.state,
            deferred_resume=DeferredToolResume(requests, requests.build_results(calls={call.tool_call_id: "selected"})),
        )


async def test_grouped_question_preserves_request_and_answer_validation():
    from a13n_harness.capabilities import UserInteractionCapability

    question = {
        "header": "Pick",
        "question": "Where?",
        "options": [{"label": "Left", "description": "First"}, {"label": "Right", "description": "Second"}],
    }
    source = UserInteractionCapability()
    calls = [("invoke", {"group": "tools", "tool": "ask_user_question", "arguments": {"questions": [question]}})]
    first = await _build(source, plan=True, calls=calls).run("go")
    assert first.deferred is not None
    pending = _roundtrip(first.deferred)
    call_id = pending.calls[0].target_call.tool_call_id
    with pytest.raises(RunError, match="invalid JSON values"):
        _build(source, plan=True, calls=calls).stream(
            previous_state=first.state,
            deferred_resume=DeferredToolResume(
                pending, pending.build_results(calls={call_id: {"answers": {"Another question": "Left"}}})
            ),
        )
    resumed = await _build(source, plan=True, calls=calls).run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            pending, pending.build_results(calls={call_id: {"answers": {"Where?": "Left"}}})
        ),
    )
    assert resumed.output_or_raise() == "done"
