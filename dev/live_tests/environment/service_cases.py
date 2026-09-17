"""Service/Harness Environment contracts shared by management and backend opt-ins."""

import json
import logging
from uuid import uuid4

from ..infrastructure.management_support import has_tool, last_tool_result

logger = logging.getLogger(__name__)


def view(path):
    return {"tool": "view", "arguments": {"file_path": path}}


def write(path, content):
    return {"tool": "write", "arguments": {"file_path": path, "content": content}}


def listing():
    return {"tool": "ls", "arguments": {"path": "."}}


async def execute(journey, environment, steps, **options):
    case = await journey.case(steps=steps)
    selection = {"environment_id": environment["id"]} if environment is not None else None
    receipt = await journey.start(case, environment=selection, **options)
    result = await journey.live.finish(receipt["run_id"])
    return result, journey.observations(case)


def assert_missing(observation, filename):
    result = last_tool_result(observation)
    assert result.get("ok") is True and result.get("has_more") is False, result
    assert filename not in {entry["path"].rsplit("/", 1)[-1] for entry in result["entries"]}, result


async def assert_managed_continuity(journey, environment, *, preserves_files, root=None):
    live = journey.live
    marker = "CONTINUITY_" + uuid4().hex
    if root is not None:
        # Seed the allocated Environment directory independently of Harness tools.
        (root / "proof.txt").write_text(marker)
    first_use = view("proof.txt") if root is not None else write("proof.txt", marker)
    original, _ = await execute(journey, environment, [first_use])
    if root is not None:
        assert marker in original["output_text"]
    path = f"/api/v1/environments/{environment['id']}"
    before = await live.request("GET", path)
    result, _ = await execute(journey, environment, [view("proof.txt")])
    assert marker in result["output_text"] and result["environment_id"] == original["environment_id"]
    assert await live.run(original["id"]) == original
    stopped = await journey.environment_command(environment["id"], "stop")
    assert stopped["status"] == "stopped"
    result, _ = await execute(journey, environment, [view("proof.txt")])
    assert marker in result["output_text"]
    resumed = await live.request("GET", path)
    assert resumed["generation"] == before["generation"]
    deleted = await journey.environment_command(environment["id"], "delete")
    assert deleted["status"] == "deleted"
    result, observed = await execute(journey, environment, [view("proof.txt") if preserves_files else listing()])
    rebuilt = await live.request("GET", path)
    assert rebuilt["id"] == before["id"]
    assert rebuilt["generation"] > before["generation"]
    assert rebuilt["template_revision_id"] == before["template_revision_id"]
    if preserves_files:
        assert marker in result["output_text"], "The caller-owned bind directory was lost"
    else:
        assert_missing(observed[-1], "proof.txt")
    logger.info(
        "Environment %s stop/resume and rebuild preserved its selection; files retained=%s",
        before["id"],
        preserves_files,
    )


async def assert_template_preparation(journey, template, revised, *, preparation, initial_access, roots=None):
    live = journey.live
    before_ids = {item["id"] for item in await live.collection(journey.base + "/environments")}
    proofs = ("TEMPLATE_ONE", "TEMPLATE_TWO") if roots is not None else (None, None)
    revision = await journey.post(
        f"/api/v1/environment-templates/{template['id']}/revisions",
        {**revised, "expected_version": template["version"]},
    )
    allocated = []
    for selection, revision_id, access, proof in (
        ({"template_id": template["id"], "version": 1}, template["current_revision_id"], initial_access, proofs[0]),
        ({"template_id": template["id"]}, revision["id"], revised["access"], proofs[1]),
    ):
        first_use = view("proof.txt") if proof is not None else listing()
        case = await journey.case(gate_at=0, parallel_steps=[first_use, first_use])
        receipt = await journey.start(case, environment=selection)
        await journey.ready(case, receipt["run_id"])
        run = await live.run(receipt["run_id"])
        path = f"/api/v1/environments/{run['environment_id']}"
        before = await live.request("GET", path)
        allocated.append(before["id"])
        if roots is not None:
            root = roots[len(allocated) - 1] / "environments" / before["id"]
            root.mkdir(parents=True, exist_ok=True)
            (root / "proof.txt").write_text(proof)
        assert before["template_revision_id"] == revision_id and before["access"] == access
        assert before["status"] == ("unprepared" if preparation == "on_use" else "running")
        assert before["generation"] == (0 if preparation == "on_use" else 1)
        await live.release(case)
        result = await live.finish(run["id"])
        observed = journey.observations(case)
        assert result["environment_id"] == before["id"]
        assert has_tool(observed[0], "shell_exec") == (access == "full")
        messages = [message for message in observed[-1]["body"]["messages"] if message.get("role") == "tool"]
        assert len(messages) == 2
        if proof is not None:
            assert all(proof in json.dumps(message["content"]) for message in messages)
            assert proof in result["output_text"]
        else:
            for message in messages:
                assert_missing({"body": {"messages": [message]}}, "version.txt")
        assert (await live.request("GET", path))["generation"] == 1
        _, observed = await execute(journey, before, [listing()])
        assert_missing(observed[-1], "version.txt")
        marker = "VERSION_" + uuid4().hex
        result, _ = await execute(journey, before, [write("version.txt", marker), view("version.txt")])
        assert marker in result["output_text"]
    after_ids = {item["id"] for item in await live.collection(journey.base + "/environments")}
    assert len(set(allocated)) == 2 and after_ids - before_ids == set(allocated)


async def assert_access_policy(journey, environment, access, *, root=None, read_text=None):
    marker = "WRITTEN_BY_HARNESS_" + uuid4().hex
    proof = "PREEXISTING_FILE_CONTENT"
    if root is not None:
        (root / "proof.txt").write_text(proof)
    selection = environment if access != "none" else None
    steps = [] if access == "none" else [listing()]
    if access != "none" and root is not None:
        steps.append(view("proof.txt"))
    if access in {"read_write", "full"}:
        steps.extend([write("output.txt", marker), view("output.txt")])
    if access == "full":
        source = "proof.txt" if root is not None else "output.txt"
        steps.extend(
            [
                {
                    "tool": "shell_exec",
                    "arguments": {
                        "command": f"cat {source} > shell-copy.txt && cat shell-copy.txt",
                        "cwd": ".",
                        "yield_time_seconds": 5,
                    },
                },
                view("shell-copy.txt"),
            ]
        )
    result, observed = await execute(journey, selection, steps)
    assert has_tool(observed[0], "view") == (access != "none")
    assert has_tool(observed[0], "write") == (access in {"read_write", "full"})
    assert has_tool(observed[0], "shell_exec") == (access == "full")
    if access != "none":
        assert last_tool_result(observed[-1])["ok"] is True
        if root is not None:
            assert proof in json.dumps(observed[-1]["body"]["messages"])
    if access in {"read_write", "full"}:
        assert marker in json.dumps(observed[-1]["body"]["messages"])
        assert (proof if access == "full" and root is not None else marker) in result["output_text"]
    if root is not None:
        assert (root / "output.txt").exists() == (access in {"read_write", "full"})
        if access in {"read_write", "full"}:
            assert (await read_text("output.txt") if read_text else (root / "output.txt").read_text()) == marker
        if access == "full":
            assert (await read_text("shell-copy.txt") if read_text else (root / "shell-copy.txt").read_text()) == proof
        else:
            assert not (root / "shell-copy.txt").exists()
    if access != "full":
        forbidden = (
            write("forbidden.txt", "MUST_NOT_EXIST")
            if access in {"none", "read_only"}
            else {
                "tool": "shell_exec",
                "arguments": {"command": "printf forbidden > forbidden.txt", "cwd": "."},
            }
        )
        denied, _ = await execute(
            journey, selection, [{**forbidden, "force_unadvertised": True}], config_override={"retries": {"tools": 1}}
        )
        assert "MUST_NOT_EXIST" not in denied["output_text"]
        if root is not None:
            assert not (root / "forbidden.txt").exists()
        if access != "none":
            _, observed = await execute(journey, environment, [listing()])
            assert_missing(observed[-1], "forbidden.txt")
