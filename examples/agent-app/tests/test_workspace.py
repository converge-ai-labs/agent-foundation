from __future__ import annotations

from pathlib import Path

import pytest

from a13n_agent_app_example import run_local_workspace

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("review_style", ["Focused", "Broad"])
async def test_local_workspace_runs_real_tools_then_resumes_with_fresh_bindings(
    tmp_path: Path,
    review_style: str,
) -> None:
    workspace_result = await run_local_workspace(tmp_path, review_style=review_style)

    assert workspace_result.suspended_run_id != workspace_result.resumed_run_id
    assert workspace_result.output == f"Local plan created; {review_style.lower()} review selected."
    assert workspace_result.tool_call_names == ("write", "task_create", "note", "ask_user_question")
    assert workspace_result.plan_content == "# Local plan\n\nCreated by a managed Direct Local file tool.\n"
    assert (tmp_path / "plan.md").read_text() == workspace_result.plan_content
