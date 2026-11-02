"""Execution business discovery and content navigation retain original authority and versions."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from a13n_service.infra import cursors
from a13n_service.infra.errors import ServiceError
from a13n_service.platform_tools import ToolContext
from a13n_service.runs import content_tools, platform_queries, trace_tools
from a13n_service.runs.findings import platform_tools as finding_tools
from a13n_service.runs.tool_executions import platform_tools as execution_tools
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from fastapi import Request
from pydantic import ValidationError

from .conftest import start_thread
from .test_agents import create_agent, create_model

pytestmark = pytest.mark.anyio


def context(service: SimpleNamespace) -> ToolContext:
    tenant = service.tenant
    principal = Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["admin"]),))
    request = Request({"type": "http", "app": service.app, "headers": []})
    return ToolContext(service.runtime, principal, tenant.workspace_id, request, None)


def test_execution_catalog_owns_every_existing_operation_and_has_no_http_arguments():
    definitions = (
        *platform_queries.TOOLS,
        *trace_tools.TOOLS,
        *finding_tools.TOOLS,
        *execution_tools.TOOLS,
        *content_tools.TOOLS,
    )
    names = {tool.name for tool in definitions}
    assert len(names) == len(definitions) == 32
    assert names == {
        "list_sessions",
        "get_session",
        "get_message_authors",
        "list_session_threads",
        "list_threads",
        "get_thread",
        "list_thread_runs",
        "list_inbox",
        "get_inbox_entry",
        "list_runs",
        "get_run",
        "list_run_items",
        "get_run_configuration",
        "get_run_lineage",
        "list_run_attempts",
        "get_run_attempt",
        "list_tool_executions",
        "get_tool_execution",
        "get_tool_execution_result",
        "list_attempt_spans",
        "list_traces",
        "search_traces",
        "get_trace",
        "list_trace_spans",
        "get_trace_span",
        "get_trace_backend",
        "list_finding_analyses",
        "list_findings",
        "create_finding",
        "get_finding",
        "read_content",
        "search_content",
    }
    for tool in definitions:
        schema = tool.inputs.model_json_schema()
        assert schema["additionalProperties"] is False
        assert not {"request_body", "workspace_id", "actor", "runtime", "origin_run_id"} & set(schema["properties"]) - {
            "origin_run_id"
        }
    for name in ("list_inbox", "list_run_items", "list_attempt_spans", "list_traces", "list_trace_spans"):
        assert "view" not in next(tool for tool in definitions if tool.name == name).inputs.model_fields
    read = next(tool for tool in definitions if tool.name == "read_content")
    assert set(read.inputs.model_fields) == {"reference", "limit"}
    with pytest.raises(ValidationError):
        read.inputs.model_validate({"reference": "x", "offset": 10})
    filters = next(tool for tool in definitions if tool.name == "list_runs")
    assert "filters" not in filters.inputs.model_fields


async def test_run_content_references_continue_search_and_reject_changed_versions(service):
    agent = await create_agent(service, "Reader", await create_model(service))
    created = await start_thread(service, agent, "A long input " + "hello needle " * 1000)
    ctx = context(service)
    detail = await platform_queries.get_run(ctx, run_id=created["run"]["id"])
    ref = detail.content["input"]
    first = await content_tools.read_content(ctx, reference=ref, limit=200)
    assert first.fragment.total_chars > 200 and first.continuation
    following = await content_tools.read_content(ctx, reference=first.continuation, limit=200)
    assert following.fragment.offset == first.fragment.next_offset
    assert following.fragment.version == first.fragment.version
    match = await content_tools.search_content(ctx, reference=ref, query="needle", limit=200)
    assert match.fragment.match_offset is not None
    source, _version, offset = content_tools.unpack(ctx, ref)
    stale = content_tools.encode_reference(ctx, source, "0" * 64, offset)
    with pytest.raises(ServiceError) as changed:
        await content_tools.read_content(ctx, reference=stale)
    assert changed.value.code == "conflict"
    assert changed.value.details["reason"] == "content_changed"
    assert json.loads((await content_tools.read_content(ctx, reference=detail.content["labels"])).fragment.text) == {}


async def test_content_handle_is_not_authority_and_cannot_cross_workspace(service):
    agent = await create_agent(service, "Reader", await create_model(service))
    created = await start_thread(service, agent, "secret input")
    ctx = context(service)
    detail = await platform_queries.get_run(ctx, run_id=created["run"]["id"])
    ref = detail.content["input"]
    denied = replace(ctx, actor=Principal(ctx.actor.id, "user", ()))
    with pytest.raises(ServiceError) as forbidden:
        await content_tools.read_content(denied, reference=ref)
    assert forbidden.value.code in {"forbidden", "not_found"}
    with pytest.raises(ServiceError) as run_scope:
        await content_tools.read_content(replace(ctx, origin="run_other"), reference=ref)
    assert run_scope.value.code == "invalid_argument"
    other = replace(ctx, workspace_id="ws_other")
    with pytest.raises(ServiceError) as scope:
        await content_tools.read_content(other, reference=ref)
    assert scope.value.code == "invalid_argument"
    # Even rewriting the navigation scope cannot grant access to the source resource.
    source, version, offset = content_tools.unpack(ctx, ref)
    forged = cursors.encode("execution-content-v1", "ws_other", source.model_dump(mode="json"), version, offset)
    with pytest.raises(ServiceError):
        await content_tools.read_content(other, reference=forged)


async def test_configuration_and_inbox_return_usable_versioned_handles(service):
    agent = await create_agent(service, "Configuration", await create_model(service), instructions="Keep exact config")
    created = await start_thread(service, agent, "Read configuration")
    ctx = context(service)
    configured = await platform_queries.get_run_configuration(ctx, run_id=created["run"]["id"], part="instructions")
    original = await content_tools.read_content(ctx, reference=configured.content["original"])
    assert original.fragment.text == "Keep exact config"
    assert configured.summary.revision_id == agent["default_revision_id"]
    inbox = await platform_queries.list_inbox(ctx, thread_id=created["thread"]["id"])
    entry = await platform_queries.get_inbox_entry(ctx, thread_id=created["thread"]["id"], entry_id=inbox.items[0].id)
    payload = await content_tools.read_content(ctx, reference=entry.content["payload"])
    assert "Read configuration" in payload.fragment.text


async def test_item_navigation_resolves_retained_content_and_keeps_pages_bounded(service, scripted_model, runs_kit):
    from a13n_service.infra import reads

    agent = await runs_kit.create_agent(service, scripted_model)
    text = "retained marker " + "long output " * 1500
    scripted_model.say(text)
    created = await runs_kit.start_thread(service, agent, "Return long output")
    await (await runs_kit.attempt(service))
    ctx = context(service)
    page = await platform_queries.list_run_items(ctx, run_id=created["run"]["id"], limit=100)
    assert reads.size(page) < 12000
    found = False
    for refs in page.content.values():
        match = await content_tools.search_content(ctx, reference=refs["all"], query="retained marker")
        if match.fragment.match_offset is not None:
            assert "retained marker" in match.fragment.text
            assert match.fragment.total_chars > 10000
            found = True
    assert found


async def test_item_discovery_is_lazy_and_continuations_bind_first_read(service, scripted_model, runs_kit, monkeypatch):
    from unittest.mock import AsyncMock

    from a13n_service.runs import item_reads, runs

    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.say("retained marker " + "long output " * 5000)
    created = await runs_kit.start_thread(service, agent, "Return long output")
    await (await runs_kit.attempt(service))
    ctx = context(service)
    retained = AsyncMock(wraps=runs.content)
    selected = AsyncMock(wraps=item_reads.get_item)
    monkeypatch.setattr(runs, "content", retained)
    monkeypatch.setattr(item_reads, "get_item", selected)
    page = await platform_queries.list_run_items(ctx, run_id=created["run"]["id"], limit=100)
    selected.assert_not_awaited()
    retained.assert_not_awaited()
    for item in page.page.items:
        if "retained marker" in item.text.text:
            discovery = page.content[item.id]["all"]
            break
    else:
        pytest.fail("Missing assistant output summary")
    assert content_tools.unpack(ctx, discovery)[1] is None
    first = await content_tools.read_content(ctx, reference=discovery, limit=200)
    assert retained.await_count > 0
    assert first.continuation
    assert content_tools.unpack(ctx, first.reference)[1] == first.fragment.version
    assert content_tools.unpack(ctx, first.continuation)[1] == first.fragment.version
    next_read = await content_tools.read_content(ctx, reference=first.continuation, limit=200)
    assert next_read.fragment.offset == first.fragment.next_offset
    denied = replace(ctx, actor=Principal(ctx.actor.id, "user", ()))
    with pytest.raises(ServiceError) as forbidden:
        await content_tools.read_content(denied, reference=first.continuation)
    assert forbidden.value.code in {"forbidden", "not_found"}

    async def changed_content(*args, **kwargs):
        saved = await retained(*args, **kwargs)
        assert isinstance(saved.value, str)
        return saved.model_copy(update={"value": saved.value + " changed after initial read"})

    monkeypatch.setattr(runs, "content", changed_content)
    for reference in (first.reference, first.continuation):
        with pytest.raises(ServiceError) as changed:
            await content_tools.read_content(ctx, reference=reference)
        assert changed.value.code == "conflict"
        assert changed.value.details["reason"] == "content_changed"
    refreshed = await content_tools.search_content(ctx, reference=discovery, query="changed after initial read")
    assert refreshed.fragment.match_offset is not None
    assert refreshed.fragment.version != first.fragment.version
    # An unbound discovery handle is valid only at the beginning, never for continuation.
    source, _, _ = content_tools.unpack(ctx, discovery)
    invalid = content_tools.encode_reference(ctx, source, None, 1)
    with pytest.raises(ServiceError) as missing_version:
        await content_tools.read_content(ctx, reference=invalid)
    assert missing_version.value.code == "invalid_argument"


async def test_model_schema_preparation_cache_is_attempt_local_and_rechecks_changes(monkeypatch):
    from unittest.mock import Mock

    from a13n_harness.tools.identity import ToolIdentity
    from a13n_service.runs.platform_tools import PlatformToolset
    from pydantic_ai import RunContext
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.toolsets import FunctionToolset
    from pydantic_ai.usage import RunUsage

    def inspect(value: str) -> str:
        """Inspect the provided value."""
        return value

    wrapped = FunctionToolset([inspect])
    identities = {"inspect": ToolIdentity("test/inspect")}
    toolset = PlatformToolset(wrapped, identities)
    model = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="test"))
    prepared = Mock(wraps=model.prepare_request)
    monkeypatch.setattr(model, "prepare_request", prepared)
    ctx = RunContext(deps=None, model=model, usage=RunUsage())
    await toolset.get_tools(ctx)
    await toolset.get_tools(ctx)
    assert prepared.call_count == 1
    # Detect in-place changes, not only replacement of the definition object.
    wrapped.tools["inspect"].function_schema.json_schema["properties"]["value"]["maxLength"] = 10
    await toolset.get_tools(ctx)
    await toolset.get_tools(ctx)
    assert prepared.call_count == 2
    other = OpenAIChatModel("gpt-4o-mini", provider=OpenAIProvider(api_key="test"))
    other_prepared = Mock(wraps=other.prepare_request)
    monkeypatch.setattr(other, "prepare_request", other_prepared)
    await toolset.get_tools(replace(ctx, model=other))
    await toolset.get_tools(replace(ctx, model=other))
    assert other_prepared.call_count == 1
    # Returning to a previous model and a fresh attempt both require validation.
    await toolset.get_tools(ctx)
    assert prepared.call_count == 3
    await PlatformToolset(wrapped, identities).get_tools(ctx)
    assert prepared.call_count == 4
