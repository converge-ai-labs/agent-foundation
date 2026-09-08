from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from a13n_harness.errors import RunError
from a13n_harness_ui.composition import AgentCompositionResolver, CompositionAcceptanceService
from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.loader import configuration_tree_fingerprint
from a13n_harness_ui.configuration.models import LoadedHarnessUiConfiguration, McpCommandTransport, McpRemoteTransport
from a13n_harness_ui.configuration.mutation import (
    ResourceMutationRequest,
    delete_configuration_source,
    mutate_configuration_source,
)
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.mcp_adapters import resolve_mcp_values
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import open_local_store

pytestmark = pytest.mark.anyio


def _source(tmp_path: Path, document: dict, *, suffix: str = "json") -> tuple[Path, Path]:
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "2"\n')
    directory = tmp_path / "mcp"
    directory.mkdir(exist_ok=True)
    source = directory / f"servers.{suffix}"
    source.write_text(json.dumps(document) if suffix == "json" else yaml.safe_dump(document))
    return root, source


@pytest.mark.parametrize("suffix", ["json", "yaml"])
@pytest.mark.parametrize("bundle", [True, False])
async def test_mcp_values_accept_literals_references_and_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    bundle: bool,
) -> None:
    values = {
        "TOKEN": "literal-sentinel-token",
        "REGION": "us-east-1",
        "EMPTY": "",
        "SPACE": " padded ",
        "FROM_ENV": {"env": "MCP_TEST_VALUE"},
        "SHORTHAND": "${MCP_TEST_VALUE}",
        "TEMPLATE": "Bearer ${MCP_TEST_VALUE}",
    }
    document = (
        {"mcpServers": {"Local_Tools ": {"type": "stdio", "command": "python", "args": ["server.py"], "env": values}}}
        if bundle
        else {
            "schema_version": "1",
            "kind": "mcp_server",
            "id": "mcp-local-tools",
            "name": "Local tools",
            "transport": {"command": "python", "arguments": ["server.py"], "environment": values},
        }
    )
    root, source = _source(tmp_path, document, suffix=suffix)
    loaded = await load_harness_ui_configuration(root)
    transport = loaded.mcp_servers["mcp-local-tools"].transport
    assert isinstance(transport, McpCommandTransport)
    assert transport.arguments == ("server.py",)
    assert "literal-sentinel-token" not in loaded.model_dump_json()
    assert loaded.source(f"mcp/{source.name}").content == ""
    monkeypatch.setenv("MCP_TEST_VALUE", "resolved-sentinel")
    expected = {
        **values,
        "FROM_ENV": "resolved-sentinel",
        "SHORTHAND": "resolved-sentinel",
        "TEMPLATE": "Bearer resolved-sentinel",
    }
    assert await resolve_mcp_values(transport.environment, tmp_path) == expected
    restored = LoadedHarnessUiConfiguration.model_validate_json(loaded.model_dump_json())
    assert restored == loaded
    recipe = ResolvedMcpRecipe(server_id="mcp-local-tools", transport=transport)
    assert "literal-sentinel-token" not in recipe.model_dump_json()
    assert "resolved-sentinel" not in recipe.model_dump_json()
    monkeypatch.setenv("MCP_TEST_VALUE", "rotated")
    assert (await resolve_mcp_values(transport.environment, tmp_path))["TEMPLATE"] == "Bearer rotated"
    monkeypatch.delenv("MCP_TEST_VALUE")
    with pytest.raises(RunError, match="unavailable"):
        await resolve_mcp_values(transport.environment, tmp_path)


async def test_json_bundle_indexes_all_servers_and_keeps_persisted_values_private(tmp_path: Path) -> None:
    root, _ = _source(
        tmp_path,
        {
            "mcpServers": {
                "one": {"command": "python", "env": {"TOKEN": "private-one"}},
                "mcp-two": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer private-two"}},
            }
        },
    )
    loaded = await load_harness_ui_configuration(root)
    source = loaded.source("mcp/servers.json")
    assert source.resource_id is None
    assert source.resource_ids == ("mcp-one", "mcp-two")
    async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
        service = CompositionAcceptanceService(store, AgentCompositionResolver(HarnessUiExtensionCatalog()))
        await service.accept(loaded, expected_current_digest=None)
        restored = await service.current()
        assert restored == loaded
        entries = await store.configurations.resources(loaded.source_digest)
        assert {item.resource_id for item in entries if item.resource_kind == "mcp_server"} == {"mcp-one", "mcp-two"}
    for path in (tmp_path / "state").rglob("*"):
        if path.is_file():
            assert b"private-one" not in path.read_bytes()
            assert b"private-two" not in path.read_bytes()
    assert restored is not None
    transport = restored.mcp_servers["mcp-two"].transport
    assert isinstance(transport, McpRemoteTransport)
    assert await resolve_mcp_values(transport.headers, tmp_path) == {"Authorization": "Bearer private-two"}


async def test_json_changes_are_observed_and_captured_literal_sources_fail_if_changed(tmp_path: Path) -> None:
    root, source = _source(tmp_path, {"mcpServers": {"test": {"command": "python", "env": {"TOKEN": "old"}}}})
    loaded = await load_harness_ui_configuration(root)
    transport = loaded.mcp_servers["mcp-test"].transport
    assert isinstance(transport, McpCommandTransport)
    before = await configuration_tree_fingerprint(root)
    source.write_text(source.read_text().replace('"old"', '"new"'))
    assert before != await configuration_tree_fingerprint(root)
    with pytest.raises(RunError) as changed:
        await resolve_mcp_values(transport.environment, tmp_path)
    assert changed.value.code == "mcp_source_changed"
    current = (await load_harness_ui_configuration(root)).mcp_servers["mcp-test"].transport
    assert isinstance(current, McpCommandTransport)
    assert await resolve_mcp_values(current.environment, tmp_path) == {"TOKEN": "new"}
    source.unlink()
    with pytest.raises(RunError):
        await resolve_mcp_values(current.environment, tmp_path)


@pytest.mark.parametrize(
    "content",
    [
        '{"mcpServers": {}, "mcpServers": {}}',
        '{"mcpServers": {"x": {"command": "python", "env": {"TOKEN": "private-token", "TOKEN": "other"}}}}',
        '{"mcpServers": {"x": {"command": "python", "env": {"TOKEN": 1}}}}',
        '{"mcpServers": {"x": {"command": "python", "env": {"TOKEN": {"file": "private-token"}}}}}',
        '{"mcpServers": {"x": {"url": "https://example.com", "type": "sse", "headers": {"Authorization": "private-token"}}}}',
        '{"mcpServers": {"x": {"command": "python", "url": "https://example.com"}}}',
        '{"mcpServers": {"x": {"command": "python", "disabled": true}}}',
        '{"mcpServers": {}, "extra": "private-token"}',
        '{"mcpServers": NaN}',
        "[]",
        '{"private-token":',
        '{"mcpServers": {"x": {"command": "python"}},}',
    ],
)
async def test_invalid_json_is_rejected_without_secret_exception_chains(tmp_path: Path, content: str) -> None:
    root, source = _source(tmp_path, {})
    source.write_text(content)
    with pytest.raises(ConfigurationError) as invalid:
        await load_harness_ui_configuration(root)
    assert "private-token" not in str(invalid.value)
    assert invalid.value.__cause__ is None
    assert invalid.value.__context__ is None


async def test_json_nesting_limit(tmp_path: Path) -> None:
    root, source = _source(tmp_path, {})
    source.write_text('{"x":' + "[" * 70 + "0" + "]" * 70 + "}")
    with pytest.raises(ConfigurationError) as invalid:
        await load_harness_ui_configuration(root)
    assert invalid.value.code == "configuration_source_limit"


async def test_cross_format_and_normalized_name_collisions_reject_candidate(tmp_path: Path) -> None:
    root, source = _source(tmp_path, {"mcpServers": {"Test_Server": {"command": "python"}}})
    duplicate = source.with_suffix(".yaml")
    duplicate.write_text(
        'schema_version: "1"\nkind: mcp_server\nid: mcp-test-server\nname: Duplicate\ntransport:\n  command: python\n'
    )
    with pytest.raises(ConfigurationError):
        await load_harness_ui_configuration(root)
    duplicate.unlink()
    source.write_text(
        json.dumps({"mcpServers": {"Test_Server": {"command": "python"}, "test-server": {"command": "python"}}})
    )
    with pytest.raises(ConfigurationError):
        await load_harness_ui_configuration(root)


async def test_json_mutation_roundtrip_preserves_other_literal_sources(tmp_path: Path) -> None:
    root, source = _source(tmp_path, {"mcpServers": {"one": {"command": "python", "env": {"TOKEN": "untouched"}}}})
    original = source.read_bytes()
    request = ResourceMutationRequest(
        content=json.dumps(
            {
                "mcpServers": {
                    "two": {"command": "python", "env": {"TOKEN": "created"}},
                    "three": {"command": "python"},
                }
            }
        )
    )
    result = await mutate_configuration_source(root, "mcp/extra.json", request)
    assert set(result.configuration.mcp_servers) == {"mcp-one", "mcp-two", "mcp-three"}
    assert source.read_bytes() == original
    updated = await mutate_configuration_source(
        root,
        "mcp/extra.json",
        ResourceMutationRequest(
            expected_source_digest=result.source_digest,
            content=json.dumps({"mcpServers": {"two": {"command": "python", "env": {"TOKEN": "updated"}}}}),
        ),
    )
    assert "mcp-three" not in updated.configuration.mcp_servers
    transport = updated.configuration.mcp_servers["mcp-two"].transport
    assert isinstance(transport, McpCommandTransport)
    assert await resolve_mcp_values(transport.environment, tmp_path) == {"TOKEN": "updated"}
    with pytest.raises(ConfigurationError):
        await mutate_configuration_source(
            root,
            "mcp/extra.json",
            ResourceMutationRequest(
                expected_source_digest=result.source_digest,
                content=request.content,
            ),
        )
    assert updated.source_digest is not None
    deleted = await delete_configuration_source(root, "mcp/extra.json", expected_source_digest=updated.source_digest)
    assert set(deleted.configuration.mcp_servers) == {"mcp-one"}
    assert source.read_bytes() == original


async def test_real_stdio_mcp_tool_executes_with_literal_environment_and_fresh_runs(tmp_path: Path) -> None:
    import sys

    from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
    from a13n_harness_ui.mcp_adapters import HarnessUiMCP
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    server = tmp_path / "server.py"
    server.write_text("""import os
from fastmcp import FastMCP
server = FastMCP("fixture")
@server.tool()
def check_configuration() -> str:
    return "configured" if os.environ.get("API_TOKEN") == "stdio-sentinel" else "missing"
server.run(transport="stdio", show_banner=False)
""")
    root, _ = _source(
        tmp_path,
        {
            "mcpServers": {
                "local": {
                    "command": sys.executable,
                    "args": [str(server)],
                    "env": {"API_TOKEN": "stdio-sentinel"},
                }
            }
        },
    )
    loaded = await load_harness_ui_configuration(root)
    recipe = ResolvedMcpRecipe(server_id="mcp-local", transport=loaded.mcp_servers["mcp-local"].transport)
    restored = ResolvedMcpRecipe.model_validate_json(recipe.model_dump_json())

    async def model(messages, info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            assert "configured" in str(returns[-1].content)
            yield "done"
        else:
            tool = next(tool for tool in info.function_tools if "check_configuration" in tool.name)
            yield {0: DeltaToolCall(name=tool.name, json_args="{}", tool_call_id="fixture-call")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(HarnessUiMCP(restored, configuration_root=tmp_path),),
    )
    for _ in range(2):
        result = await executable.run("Check MCP configuration", bindings=RunBindings.embedded())
        assert result.status == "completed"
        assert "stdio-sentinel" not in str(result.all_messages())


async def test_remote_adapter_receives_literal_headers_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness_ui.mcp_adapters as adapters
    from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
    from a13n_harness_ui.mcp_adapters import HarnessUiMCP
    from fastmcp import FastMCP
    from pydantic_ai.models.function import FunctionModel

    root, _ = _source(
        tmp_path,
        {
            "mcpServers": {
                "remote": {
                    "type": "streamable-http",
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer http-sentinel", "X-Region": "${MCP_REGION}"},
                }
            }
        },
    )
    source = await load_harness_ui_configuration(root)
    recipe = ResolvedMcpRecipe(server_id="mcp-remote", transport=source.mcp_servers["mcp-remote"].transport)
    observed = []

    def transport(url, **kwargs):
        observed.append((url, kwargs["headers"]))
        return FastMCP("fixture")

    async def model(messages, info):
        yield "done"

    monkeypatch.setattr(adapters, "StreamableHttpTransport", transport)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(HarnessUiMCP(recipe, configuration_root=tmp_path),),
    )
    for region in ("west", "east"):
        monkeypatch.setenv("MCP_REGION", region)
        result = await executable.run("Check", bindings=RunBindings.embedded())
        assert result.status == "completed"
    assert observed == [
        ("https://example.com/mcp", {"Authorization": "Bearer http-sentinel", "X-Region": "west"}),
        ("https://example.com/mcp", {"Authorization": "Bearer http-sentinel", "X-Region": "east"}),
    ]


def test_cli_config_show_omits_literal_mcp_values_and_reload_retains_last_valid_generation(tmp_path: Path) -> None:
    from a13n_harness_ui.cli import cli
    from click.testing import CliRunner

    root, source = _source(
        tmp_path,
        {
            "mcpServers": {
                "one": {"command": "python", "env": {"TOKEN": "cli-private-token"}},
                "two": {"url": "https://example.com/mcp"},
            }
        },
    )
    root.write_text('schema_version: "2"\nprocess:\n  pricing_auto_update: false\n')
    args = ["--config", str(root), "--data-root", str(tmp_path / "state"), "config", "show", "--format", "json"]
    runner = CliRunner()
    shown = runner.invoke(cli, args)
    assert shown.exit_code == 0, shown.output
    assert "cli-private-token" not in shown.output
    assert set(json.loads(shown.stdout)["mcp_servers"]) == {"mcp-one", "mcp-two"}
    source.write_text('{"mcpServers": {"bad": "cli-private-token"}')
    fallback = runner.invoke(cli, args)
    assert fallback.exit_code == 0, fallback.output
    assert "cli-private-token" not in fallback.output
    assert set(json.loads(fallback.stdout)["mcp_servers"]) == {"mcp-one", "mcp-two"}


async def test_upgrade_accepts_unchanged_legacy_mcp_sources_without_generation_collision(tmp_path: Path) -> None:
    from a13n_harness_ui.configuration.models import canonical_digest

    root, source_file = _source(
        tmp_path,
        {
            "schema_version": "1",
            "kind": "mcp_server",
            "id": "mcp-legacy",
            "name": "Legacy",
            "transport": {"command": "python", "environment": {"TOKEN": {"env": "TOKEN"}}},
        },
        suffix="yaml",
    )
    current = await load_harness_ui_configuration(root)
    legacy_digest = canonical_digest(
        (
            tuple((item.relative_path, item.source_digest) for item in current.sources),
            current.content_plugin_diagnostics,
        )
    )
    legacy = current.model_copy(
        update={
            "source_digest": legacy_digest,
            "sources": tuple(
                item.model_copy(update={"content": source_file.read_text().strip()})
                if item.resource_kind == "mcp_server"
                else item
                for item in current.sources
            ),
        }
    )
    assert legacy.source_digest != current.source_digest
    assert legacy.root_digest == current.root_digest
    async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
        service = CompositionAcceptanceService(store, AgentCompositionResolver(HarnessUiExtensionCatalog()))
        await service.accept(legacy, expected_current_digest=None)
        await service.accept(current, expected_current_digest=legacy_digest)
        assert await service.current() == current
        assert await service.load(legacy_digest) == legacy
        await service.accept(await load_harness_ui_configuration(root), expected_current_digest=current.source_digest)
        assert await service.current() == current
