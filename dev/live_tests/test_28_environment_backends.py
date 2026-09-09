"""Cases 3/20/21/22 across five real Environment backends and their capability limits."""

import logging
import os
from pathlib import Path
from uuid import uuid4

import pytest

from .environment_backends import BACKENDS, REMOTE, EnvironmentBackend, recipe_configuration
from .management_support import has_tool, last_tool_result
from .round_two_lab import REPOSITORY, open_lab

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", params=BACKENDS)
async def environment_backend(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in with --live-environments for real envd, Docker and configured E2B")
    kind, binary, settings = request.param, None, None
    if kind in {"local-envd", *REMOTE}:
        binary = Path(os.environ.get("A13N_ENVD_TEST_BINARY", REPOSITORY / "target/debug/a13n-envd")).resolve()
        if not binary.is_file():
            pytest.fail("Build a13n-envd or set A13N_ENVD_TEST_BINARY before enabling the Environment matrix")
    if kind == "e2b":
        from .provider_config import load_provider_settings

        settings = load_provider_settings().environment
        if settings is None:
            pytest.skip("Configure the optional E2B environment section to run its matrix")
    async with open_lab(suite="management", websocket_envd=kind == "websocket-envd") as lab:
        backend = EnvironmentBackend(lab, kind, binary, settings)
        if kind == "local-envd":
            lab.worker_environment["A13N_ENVD_EXECUTABLE"] = str(binary)
            await backend.restart_worker()
        yield backend


def view(path):
    return {"tool": "view", "arguments": {"file_path": path}}


def write(path, content):
    return {"tool": "write", "arguments": {"file_path": path, "content": content}}


async def execute(journey, environment, steps):
    case = await journey.case(steps=steps)
    receipt = await journey.start(case, environment={"environment_id": environment["id"]})
    result = await journey.live.finish(receipt["run_id"])
    return result, journey.observations(case)


def listing():
    return {"tool": "ls", "arguments": {"path": "/workspace"}}


def assert_missing(observation, filename):
    result = last_tool_result(observation)
    assert result["ok"] is True and result["has_more"] is False, result
    assert filename not in {entry["path"].rsplit("/", 1)[-1] for entry in result["entries"]}, result


async def test_environment_backend_tools_and_access(environment_backend):
    backend, journey = environment_backend, environment_backend.journey
    for access in ("read_only", "read_write", "full"):
        async with backend.target() as target:
            environment = await target.allocate(access=access)
            marker = "BACKEND_" + uuid4().hex
            steps = [listing()]
            if access != "read_only":
                steps.extend([write("/workspace/proof.txt", marker), view("/workspace/proof.txt")])
            if access == "full":
                steps.extend(
                    [
                        {
                            "tool": "shell_exec",
                            "arguments": {
                                "command": "cat proof.txt > shell-copy.txt",
                                "cwd": "/workspace",
                                "yield_time_seconds": 5,
                            },
                        },
                        view("/workspace/shell-copy.txt"),
                    ]
                )
            result, observed = await execute(journey, environment, steps)
            assert has_tool(observed[0], "view")
            assert has_tool(observed[0], "write") == (access != "read_only")
            assert has_tool(observed[0], "shell_exec") == (access == "full")
            assert last_tool_result(observed[-1])["ok"] is True
            if access != "read_only":
                assert marker in result["output_text"]
            if access != "full":
                # Force a tool omitted from discovery, then independently read the
                # destination through the real backend to prove no mutation occurred.
                forbidden = (
                    write("/workspace/forbidden.txt", marker)
                    if access == "read_only"
                    else {
                        "tool": "shell_exec",
                        "arguments": {"command": "printf forbidden > forbidden.txt", "cwd": "/workspace"},
                    }
                )
                await execute(journey, environment, [{**forbidden, "force_unadvertised": True}])
                _, observed = await execute(journey, environment, [listing()])
                assert_missing(observed[-1], "forbidden.txt")
            logger.info(
                "Environment backend=%s access=%s passed real operation/effect assertions", backend.kind, access
            )


async def test_environment_backend_templates_and_preparation(environment_backend):
    backend, journey = environment_backend, environment_backend.journey
    live = journey.live
    if backend.kind in REMOTE:
        async with backend.target() as target:
            denied = await journey.post(
                journey.base + "/environment-templates",
                {"name": "Unsupported remote template", **target.recipe},
                expected=422,
            )
            assert denied["error"]["code"] == "environment_invalid", denied
            assert (await live.collection(journey.base + "/environment-templates")) == []
            assert target.process.returncode is None
        return
    for preparation in ("on_run", "on_use"):
        async with backend.target() as target:
            template = await target.template(access="read_write", preparation=preparation)
            revised = {
                **target.recipe,
                "access": "full",
                "preparation": preparation,
                "configuration": recipe_configuration(
                    backend.kind, target.root.parent / "version-two", backend.settings
                ),
            }
            revision = await journey.post(
                f"/api/v1/environment-templates/{template['id']}/revisions",
                {**revised, "expected_version": template["version"]},
            )
            for selection, revision_id, access in (
                ({"template_id": template["id"], "version": 1}, template["current_revision_id"], "read_write"),
                ({"template_id": template["id"]}, revision["id"], "full"),
            ):
                case = await journey.case(gate_at=0, parallel_steps=[listing(), listing()])
                receipt = await journey.start(case, environment=selection)
                await journey.ready(case, receipt["run_id"])
                run = await live.run(receipt["run_id"])
                path = f"/api/v1/environments/{run['environment_id']}"
                before = await live.request("GET", path)
                assert before["template_revision_id"] == revision_id and before["access"] == access
                assert before["status"] == ("unprepared" if preparation == "on_use" else "running")
                assert before["generation"] == (0 if preparation == "on_use" else 1)
                await live.release(case)
                await live.finish(run["id"])
                observed = journey.observations(case)
                assert has_tool(observed[0], "shell_exec") == (access == "full")
                messages = [message for message in observed[-1]["body"]["messages"] if message.get("role") == "tool"]
                assert len(messages) == 2
                for message in messages:
                    assert_missing({"body": {"messages": [message]}}, "version.txt")
                assert (await live.request("GET", path))["generation"] == 1
                marker = "VERSION_" + uuid4().hex
                result, _ = await execute(
                    journey, before, [write("/workspace/version.txt", marker), view("/workspace/version.txt")]
                )
                assert marker in result["output_text"]
            logger.info(
                "Environment backend=%s preparation=%s retained exact revisions and prepared once",
                backend.kind,
                preparation,
            )


async def test_environment_backend_lifecycle_and_continuity(environment_backend):
    backend, journey = environment_backend, environment_backend.journey
    live = journey.live
    async with backend.target() as target:
        environment = await target.allocate()
        marker = "CONTINUITY_" + uuid4().hex
        original, _ = await execute(journey, environment, [write("/workspace/proof.txt", marker)])
        path = f"/api/v1/environments/{environment['id']}"
        before = await live.request("GET", path)
        result, _ = await execute(journey, environment, [view("/workspace/proof.txt")])
        assert marker in result["output_text"] and result["environment_id"] == original["environment_id"]
        assert await live.run(original["id"]) == original
        if backend.kind in {"docker", "e2b"}:
            stopped = await journey.environment_command(environment["id"], "stop")
            assert stopped["status"] == "stopped"
            result, _ = await execute(journey, environment, [view("/workspace/proof.txt")])
            assert marker in result["output_text"]
            resumed = await live.request("GET", path)
            assert resumed["generation"] == before["generation"]
            deleted = await journey.environment_command(environment["id"], "delete")
            assert deleted["status"] == "deleted"
            result, observed = await execute(
                journey, environment, [view("/workspace/proof.txt") if backend.kind == "docker" else listing()]
            )
            rebuilt = await live.request("GET", path)
            assert rebuilt["generation"] > before["generation"]
            assert rebuilt["template_revision_id"] == before["template_revision_id"]
            if backend.kind == "docker":
                assert marker in result["output_text"], "The caller-owned bind directory was lost"
            else:
                assert_missing(observed[-1], "proof.txt")
        else:
            for action in ("stop", "delete"):
                denied = await journey.post(path + "/" + action, {}, expected=409 if backend.kind in REMOTE else 422)
                assert denied["error"]["code"] == (
                    "environment_busy" if backend.kind in REMOTE else "environment_invalid"
                ), denied
            result, _ = await execute(journey, environment, [view("/workspace/proof.txt")])
            assert marker in result["output_text"]
            if target.process is not None:
                assert target.process.returncode is None, "Service stopped an externally owned daemon"
            assert target.root.joinpath("proof.txt").read_text() == marker
        logger.info("Environment backend=%s continuity and lifecycle capability boundaries passed", backend.kind)
