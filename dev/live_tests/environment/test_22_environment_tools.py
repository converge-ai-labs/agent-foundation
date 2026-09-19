"""Case 22: Provider tools work without a Service access-level selection."""

import pytest

from ..infrastructure.management_support import has_tool
from .service_cases import assert_environment_tools, execute, write

pytestmark = pytest.mark.anyio


async def test_environment_exposes_file_and_shell_tools(management):
    environment, root = await management.environment()
    await assert_environment_tools(management, environment, root=root)


async def test_missing_environment_exposes_no_tools_or_file_effects(management):
    _, observed = await execute(management, None, [])
    assert not has_tool(observed[0], "view")
    assert not has_tool(observed[0], "shell_exec")
    denied, _ = await execute(
        management,
        None,
        [{**write("forbidden.txt", "MUST_NOT_EXIST"), "force_unadvertised": True}],
        config_override={"retries": {"tools": 1}},
    )
    assert "MUST_NOT_EXIST" not in denied["output_text"]
