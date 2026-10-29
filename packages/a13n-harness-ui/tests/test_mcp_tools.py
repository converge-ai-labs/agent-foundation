from __future__ import annotations

import json
from pathlib import Path

import pytest
from a13n_harness_ui.configuration import load_harness_ui_configuration, mutate_configuration_source
from a13n_harness_ui.configuration.mcp_tools import McpToolSelection, discover_tools, selection_mutation, tool_settings
from a13n_harness_ui.configuration.models import AgentToolSearch
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.mcp_adapters import resolve_mcp_values
from fastmcp import FastMCP

from .test_mcp_configuration import _source
from .test_tool_search_integration import _http_mcp

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("bundle", [False, True])
@pytest.mark.parametrize("suffix", ["json", "yaml"])
async def test_selection_preserves_private_connection_and_siblings(tmp_path: Path, bundle: bool, suffix: str):
    transport = {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer private-sentinel"}}
    document = (
        {"mcpServers": {"one": transport, "two": {"command": "python"}}}
        if bundle
        else {
            "schema_version": "1",
            "kind": "mcp_server",
            "id": "mcp-one",
            "name": "One",
            "transport": transport,
        }
    )
    root, _ = _source(tmp_path, document, suffix=suffix)
    source = await load_harness_ui_configuration(root)
    settings = tool_settings(root, source, "mcp-one")
    assert "private-sentinel" not in settings.model_dump_json()
    for selected in (["search"], [], None):
        path, mutation = selection_mutation(
            root, source, "mcp-one", McpToolSelection(resource_digest=settings.resource_digest, tools=selected)
        )
        saved = await mutate_configuration_source(root, path, mutation)
        source = saved.configuration
        settings = tool_settings(root, source, "mcp-one")
        assert settings.tools == selected
        resource = source.mcp_servers["mcp-one"]
        assert resource.schema_version == "2"
        assert await resolve_mcp_values(resource.transport.headers, tmp_path) == {
            "Authorization": "Bearer private-sentinel"
        }
        assert "private-sentinel" not in source.model_dump_json()
        if bundle:
            assert source.mcp_servers["mcp-two"].tools is None
            assert source.mcp_servers["mcp-two"].transport.command == "python"


async def test_stale_selection_does_not_overwrite_connection(tmp_path: Path):
    root, file = _source(tmp_path, {"mcpServers": {"one": {"command": "python"}}})
    source = await load_harness_ui_configuration(root)
    settings = tool_settings(root, source, "mcp-one")
    external = file.read_text().replace("python", "node")
    file.write_text(external)
    with pytest.raises(ConfigurationError, match="changed"):
        selection_mutation(
            root, source, "mcp-one", McpToolSelection(resource_digest=settings.resource_digest, tools=[])
        )
    assert file.read_text() == external


async def test_version_one_cannot_silently_ignore_restrictions(tmp_path: Path):
    root, _ = _source(tmp_path, {"mcpServers": {"one": {"command": "python", "tools": []}}})
    with pytest.raises(ConfigurationError):
        await load_harness_ui_configuration(root)


async def test_discovery_lists_disabled_tools_without_executing(tmp_path: Path):
    server = FastMCP("Selection fixture")
    calls = []

    @server.tool
    async def search(query: str) -> str:
        calls.append(query)
        return query

    @server.tool
    async def transfer() -> str:
        calls.append("transfer")
        return "done"

    async with _http_mcp(server) as url:
        root, _ = _source(tmp_path, {"schema_version": "2", "mcpServers": {"one": {"url": url, "tools": ["search"]}}})
        source = await load_harness_ui_configuration(root)
        descriptors = await discover_tools(source, "mcp-one", tmp_path)
        assert {tool.name for tool in descriptors} == {"search", "transfer"}
        assert calls == []


def test_search_disabled_round_trip_preserves_custom_groups():
    plan = AgentToolSearch.model_validate_json(
        json.dumps(
            {
                "enabled": False,
                "groups": {
                    "web": {
                        "description": "Custom description",
                        "mcp_servers": ["mcp-one"],
                        "harness_plugins": ["plugin-one"],
                    }
                },
            }
        )
    )
    captured = plan.selected(mcp_servers=("mcp-one",), harness_plugins=("plugin-one",))
    assert captured.enabled is False
    assert captured.groups == plan.groups
    assert AgentToolSearch.model_validate_json(captured.model_dump_json()) == captured


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("selected", [["search"], []])
async def test_mcp_selection_filters_model_directory_and_proxy_dispatch(
    tmp_path: Path, grouped: bool, selected: list[str]
):
    from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
    from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
    from a13n_harness_ui.composition.models import ResolvedMcpRecipe
    from a13n_harness_ui.configuration.models import McpRemoteTransport
    from a13n_harness_ui.mcp_adapters import HarnessUiMCP
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    server = FastMCP("Runtime selection")
    calls = []

    @server.tool
    async def search() -> str:
        calls.append("search")
        return "found"

    @server.tool
    async def transfer() -> str:
        raise AssertionError("An excluded tool must never execute")

    step = 0

    async def model(messages, info):
        nonlocal step
        names = {tool.name for tool in info.function_tools}
        assert "transfer" not in names
        if not selected:
            assert not names
            yield "done"
            return
        if grouped:
            assert "search" not in names
            commands = [
                ("call_tool", {"group": "web", "tool": "transfer", "arguments": {}}),
                ("call_tool", {"group": "web", "tool": "search", "arguments": {}}),
            ]
        else:
            assert names == {"search"}
            commands = [("search", {})]
        if step < len(commands):
            name, args = commands[step]
            step += 1
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{step}")}
        else:
            yield "done"

    async with _http_mcp(server) as url:
        recipe = ResolvedMcpRecipe(server_id="mcp-one", transport=McpRemoteTransport(url=url), tools=tuple(selected))
        frozen = ResolvedMcpRecipe.model_validate_json(recipe.model_dump_json())
        capability = HarnessUiMCP(frozen)
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(capability,),
            tool_search=ToolSearchPlan(groups={"web": ToolSearchSelection("Web tools", capabilities=(capability,))})
            if grouped
            else None,
        )
        result = await executable.run("test", bindings=RunBindings.embedded())
        assert result.output_or_raise() == "done"
        assert calls == selected


async def test_bundle_drafts_save_independently_but_reject_same_server_changes(tmp_path: Path):
    root, file = _source(tmp_path, {"mcpServers": {"one": {"command": "python"}, "two": {"command": "node"}}})
    source = await load_harness_ui_configuration(root)
    one = tool_settings(root, source, "mcp-one")
    two = tool_settings(root, source, "mcp-two")
    for server_id, settings, selected in [("mcp-one", one, ["search"]), ("mcp-two", two, [])]:
        path, mutation = selection_mutation(
            root, source, server_id, McpToolSelection(resource_digest=settings.resource_digest, tools=selected)
        )
        source = (await mutate_configuration_source(root, path, mutation)).configuration
    assert source.mcp_servers["mcp-one"].tools == ("search",)
    assert source.mcp_servers["mcp-two"].tools == ()
    before = file.read_bytes()
    with pytest.raises(ConfigurationError, match="changed"):
        selection_mutation(root, source, "mcp-one", McpToolSelection(resource_digest=one.resource_digest, tools=[]))
    assert file.read_bytes() == before


async def test_yaml_comments_survive_and_all_tools_does_not_upgrade_unrestricted_files(tmp_path: Path):
    root, file = _source(tmp_path, {}, suffix="yaml")
    original = '# keep me\nmcpServers:\n  one:\n    command: "python" # inline\n'
    file.write_text(original)
    source = await load_harness_ui_configuration(root)
    settings = tool_settings(root, source, "mcp-one")
    path, mutation = selection_mutation(
        root, source, "mcp-one", McpToolSelection(resource_digest=settings.resource_digest, tools=None)
    )
    assert mutation.content == original
    for selected in (["_private tool"], None):
        path, mutation = selection_mutation(
            root, source, "mcp-one", McpToolSelection(resource_digest=settings.resource_digest, tools=selected)
        )
        source = (await mutate_configuration_source(root, path, mutation)).configuration
        settings = tool_settings(root, source, "mcp-one")
        text = file.read_text()
        assert "# keep me" in text and "# inline" in text
        assert 'command: "python"' in text
        assert settings.tools == selected
    assert "tools:" not in file.read_text()


@pytest.mark.parametrize("name", ["_private tool", "_private", "x" * 128])
async def test_selection_accepts_exact_mcp_tool_names(tmp_path: Path, name: str):
    root, _ = _source(tmp_path, {"mcpServers": {"one": {"command": "python"}}})
    source = await load_harness_ui_configuration(root)
    settings = tool_settings(root, source, "mcp-one")
    path, mutation = selection_mutation(
        root, source, "mcp-one", McpToolSelection(resource_digest=settings.resource_digest, tools=[name])
    )
    source = (await mutate_configuration_source(root, path, mutation)).configuration
    assert source.mcp_servers["mcp-one"].tools == (name,)


async def test_json_noop_preserves_bytes_and_changed_selection_normalizes_indentation(tmp_path: Path):
    root, file = _source(tmp_path, {"mcpServers": {"one": {"command": "python"}}})
    original = file.read_text()
    source = await load_harness_ui_configuration(root)
    settings = tool_settings(root, source, "mcp-one")
    _, unchanged = selection_mutation(
        root, source, "mcp-one", McpToolSelection(resource_digest=settings.resource_digest, tools=None)
    )
    assert unchanged.content == original
    _, changed = selection_mutation(
        root, source, "mcp-one", McpToolSelection(resource_digest=settings.resource_digest, tools=[])
    )
    assert changed.content == json.dumps(json.loads(changed.content), indent=2) + "\n"
