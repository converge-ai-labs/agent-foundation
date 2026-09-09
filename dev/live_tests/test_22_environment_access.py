"""Case 22: file/Shell exposure, actual effects and denied writes by access level."""

import json

import pytest

from .management_support import has_tool

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("access", ["none", "read_only", "read_write", "full"])
async def test_environment_access_controls_tools_and_actual_files(management, access):
    journey, live = management, management.live
    environment, root = await journey.environment(access="full" if access == "none" else access)
    proof = "PREEXISTING_FILE_CONTENT"
    (root / "proof.txt").write_text(proof)
    steps = [] if access == "none" else [{"tool": "view", "arguments": {"file_path": "/workspace/proof.txt"}}]
    if access in {"read_write", "full"}:
        steps.extend(
            [
                {"tool": "write", "arguments": {"file_path": "/workspace/output.txt", "content": "WRITTEN_BY_HARNESS"}},
                {"tool": "view", "arguments": {"file_path": "/workspace/output.txt"}},
            ]
        )
    if access == "full":
        steps.append(
            {
                "tool": "shell_exec",
                "arguments": {
                    "command": "cat proof.txt > shell-copy.txt && cat shell-copy.txt",
                    "cwd": "/workspace",
                },
            }
        )
    case = await journey.case(steps=steps)
    receipt = await journey.start(case, environment=None if access == "none" else {"environment_id": environment["id"]})
    result = await live.finish(receipt["run_id"])
    observed = journey.observations(case)
    assert has_tool(observed[0], "view") == (access != "none")
    assert has_tool(observed[0], "write") == (access in {"read_write", "full"})
    assert has_tool(observed[0], "shell_exec") == (access == "full")
    if access != "none":
        assert proof in json.dumps(observed[-1]["body"]["messages"])
    assert (root / "output.txt").exists() == (access in {"read_write", "full"})
    if access in {"read_write", "full"}:
        assert (root / "output.txt").read_text() == "WRITTEN_BY_HARNESS"
    if access == "full":
        assert (root / "shell-copy.txt").read_text() == proof
        assert proof in result["output_text"]
    else:
        assert not (root / "shell-copy.txt").exists()
    if access in {"none", "read_only"}:
        denied = await journey.case(
            steps=[
                {
                    "tool": "write",
                    "force_unadvertised": True,
                    "arguments": {
                        "file_path": "/workspace/forbidden.txt",
                        "content": "MUST_NOT_EXIST",
                    },
                }
            ]
        )
        receipt = await journey.start(
            denied,
            environment=None if access == "none" else {"environment_id": environment["id"]},
            config_override={"retries": {"tools": 1}},
        )
        await live.finish(receipt["run_id"])
        assert not (root / "forbidden.txt").exists()
        assert "MUST_NOT_EXIST" not in (await live.run(receipt["run_id"]))["output_text"]
