from __future__ import annotations

import pytest
from a13n_harness_ui.composition import AgentCompositionResolver
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration_inspection import captured_configuration

from .test_composition import _catalog, _selection, _write_source


@pytest.mark.anyio
async def test_capture_projection_omits_executable_payloads_and_bounds_children(tmp_path) -> None:
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    value = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    child = value.root.children[0]
    children = tuple(child.model_copy(update={"name": f"child-{index}"}) for index in range(105))
    value = value.model_copy(update={"root": value.root.model_copy(update={"children": children})})
    result = captured_configuration("a" * 64, value)
    assert len(result.children) == 100 and result.omitted_children == 5
    assert result.tool_proxy is None  # Legacy direct captures are not rewritten.
    assert value.root.tool_proxy is None
    assert result.mcp_server_ids == ("mcp-docs",)
    assert result.harness_plugin_ids == ("plugin-memory",)
    payload = result.model_dump_json()
    for private in (
        "OPENAI_API_KEY",
        "MCP_TOKEN",
        "Root authored instructions.",
        "example.test",
        "capacity",
        "temperature",
    ):
        assert private not in payload
    assert {"model_settings", "plugin_configuration", "mcp_transport", "child_definitions"} <= set(
        result.omitted_fields
    )
