"""Case 21: lifecycle recovery, backing generations and operation-specific inheritance."""

import json
from contextlib import closing

import anyio
import pytest

from .management_support import client_tool, feedback_body, last_tool_result

pytestmark = pytest.mark.anyio


@pytest.fixture
async def docker_environment(management):
    environment, root = await management.environment(provider_type="a13n.docker")
    try:
        yield environment, root
    finally:
        # Stop the fixture's Workers before removing only its exact owned target.
        # Control and the model remain available for the lab's Run cleanup.
        for worker in management.lab.workers:
            if worker.returncode is None:
                await management.lab.stop(worker)

        def remove_owned_target():
            import docker

            with closing(docker.from_env()) as client:
                for container in client.containers.list(
                    all=True,
                    filters={
                        "label": [
                            "io.a13n.environment-provider=a13n.docker",
                            "io.a13n.environment-id=" + environment["id"],
                        ]
                    },
                ):
                    container.remove(force=True)

        await anyio.to_thread.run_sync(remove_owned_target)


async def test_stopped_and_deleted_managed_environment_recovers(management, docker_environment):
    journey, live = management, management.live
    environment, root = docker_environment
    (root / "proof.txt").write_text("PERSISTENT_PROOF")
    selection = {"environment_id": environment["id"]}

    async def read():
        case = await journey.case(steps=[{"tool": "view", "arguments": {"file_path": "/workspace/proof.txt"}}])
        receipt = await journey.start(case, environment=selection)
        result = await live.finish(receipt["run_id"])
        assert "PERSISTENT_PROOF" in result["output_text"]
        return await live.request("GET", f"/api/v1/environments/{environment['id']}")

    initial = await read()
    stopped = await journey.environment_command(environment["id"], "stop")
    assert stopped["status"] == "stopped"
    resumed = await read()
    assert resumed["generation"] == initial["generation"], "A connection refresh changed backing generation"
    deleted = await journey.environment_command(environment["id"], "delete")
    assert deleted["status"] == "deleted"
    rebuilt = await read()
    assert rebuilt["id"] == initial["id"] and rebuilt["template_revision_id"] == initial["template_revision_id"]
    assert rebuilt["generation"] > initial["generation"]


@pytest.mark.parametrize("operation", ["continue", "retry", "fork", "feedback"])
async def test_successor_inherits_environment_by_operation(management, operation):
    journey, live = management, management.live
    environment, _ = await journey.environment()
    selection = {"environment_id": environment["id"]}
    case = await journey.case()
    if operation == "retry":
        case = await live.case("model_error")
    agent_id = live.config["agent_id"]
    if operation == "feedback":
        agent = await journey.agent(client_tools=[client_tool()])
        agent_id = agent["agent"]["id"]
        journey.plan(case, steps=[{"tool": "live_client", "arguments": {"prompt": "Please provide the proof"}}])
    receipt = await journey.start(case, environment=selection, agent_id=agent_id)
    parent = await live.finish(
        receipt["run_id"], {"retry": "failed", "feedback": "waiting"}.get(operation, "completed")
    )
    thread = await live.thread(parent["thread_id"])
    if operation == "feedback":
        body = await feedback_body(live, parent, {"proof": "CLIENT_RESULT"})
    elif operation == "retry":
        await live.release(case)
        body = {"expected_thread_version": thread["version"]}
    else:
        body = {"input": live.start_body(await journey.case())["input"]}
        if operation == "continue":
            body["expected_thread_version"] = thread["version"]
    successor = await journey.post(f"/api/v1/runs/{parent['id']}/{operation}", body, expected=202)
    live.track(successor)
    result = await live.finish(successor["run_id"])
    assert result["environment_id"] == environment["id"] and result["environment_access"] == "full"
    assert (result["thread_id"] != parent["thread_id"]) == (operation == "fork")
    assert await live.run(parent["id"]) == parent


async def test_continuation_updates_default_but_historical_fork_keeps_source(management):
    journey, live = management, management.live
    first, _ = await journey.environment()
    second, _ = await journey.environment()
    receipt = await journey.start(await journey.case(), environment={"environment_id": first["id"]})
    original = await live.finish(receipt["run_id"])
    current = original
    for selection in ({"environment": {"environment_id": second["id"]}}, {}):
        thread = await live.thread(current["thread_id"])
        receipt = await journey.post(
            f"/api/v1/runs/{current['id']}/continue",
            {
                "expected_thread_version": thread["version"],
                "input": live.start_body(await journey.case())["input"],
                **selection,
            },
            expected=202,
        )
        live.track(receipt)
        current = await live.finish(receipt["run_id"])
        assert current["environment_id"] == second["id"]
    fork = await journey.post(
        f"/api/v1/runs/{original['id']}/fork",
        {
            "input": live.start_body(await journey.case())["input"],
        },
        expected=202,
    )
    live.track(fork)
    assert (await live.finish(fork["run_id"]))["environment_id"] == first["id"]
    assert (await live.thread(original["thread_id"]))["default_environment_id"] == second["id"]


async def test_process_handle_cannot_cross_rebuilt_environment_generation(management, docker_environment):
    journey, live = management, management.live
    environment, _ = docker_environment
    selection = {"environment_id": environment["id"]}
    case = await journey.case(
        steps=[
            {
                "tool": "shell_exec",
                "arguments": {
                    "command": "sleep 60",
                    "yield_time_seconds": 0.1,
                },
            }
        ]
    )
    receipt = await journey.start(case, environment=selection)
    await live.finish(receipt["run_id"])
    observed = last_tool_result(journey.observations(case)[-1])
    process_id = observed.get("process_id") or observed.get("data", {}).get("process_id")
    assert process_id, "The long-running command did not return a process handle"
    before = await live.request("GET", f"/api/v1/environments/{environment['id']}")
    await journey.environment_command(environment["id"], "delete")
    later = await journey.case(steps=[{"tool": "shell_info", "arguments": {"process_id": process_id}}])
    receipt = await journey.start(later, environment=selection)
    await live.finish(receipt["run_id"])
    after = await live.request("GET", f"/api/v1/environments/{environment['id']}")
    assert after["generation"] > before["generation"]
    result = last_tool_result(journey.observations(later)[-1])
    assert result["ok"] is False and "error" in result, json.dumps(result)
