"""Case 32: current resource eligibility and immutable selections across three Workers."""

import json
import signal

import pytest

from ..infrastructure.management_support import last_tool_result
from .test_27_connectivity_execution import connection, requests

pytestmark = pytest.mark.anyio


async def test_multiworker_agent_and_model_snapshots(multiworker):
    journey = multiworker
    live, lab = journey.live, journey.lab
    # Three simultaneous claims freeze the old Model settings and Agent revision.
    model_path = journey.base + "/models/" + live.config["model_id"]
    await journey.patch(model_path, {"settings": {"temperature": 0.2}})
    plugins = live.config["resilience_plugins"]
    first = await journey.agent(instructions="AUTHORITY_REVISION_ONE", plugins=plugins)
    cases = [await journey.case(gate_at=0) for _ in range(3)]
    for case in cases:
        journey.plan(
            case,
            gate_at=0,
            steps=[{"tool": "live_effect", "arguments": {"case_id": case["case_id"], "token": case["token"]}}],
        )
    receipts = [await journey.start(case, agent_id=first["agent"]["id"]) for case in cases]
    for case, receipt in zip(cases, receipts, strict=True):
        await journey.ready(case, receipt["run_id"])
    assert len({lab.execution_owner(receipt["run_id"]).pid for receipt in receipts}) == 3
    assert all(len(journey.observations(case)) == 1 for case in cases)
    second = await journey.revision(first["agent"], instructions="AUTHORITY_REVISION_TWO", plugins=plugins)
    await journey.patch(model_path, {"settings": {"temperature": 0.8}, "upstream_model": "changed-upstream"})
    for case in cases:
        await live.release(case)
    for case, receipt in zip(cases, receipts, strict=True):
        done = await live.finish(receipt["run_id"])
        assert done["agent_revision_id"] == first["revision"]["id"]
        observed = journey.observations(case)
        assert len(observed) == 2, "A second model request must observe the frozen selection after mutation"
        for observation in observed:
            body = observation["body"]
            assert "AUTHORITY_REVISION_ONE" in json.dumps(body) and "AUTHORITY_REVISION_TWO" not in json.dumps(body)
            assert body["model"] == "live-fixture" and body["temperature"] == 0.2
        assert (await live.evidence(case))["effects"] == 1
        assert case["token"] in done["output_text"]
    later = await journey.case()
    receipt = await journey.start(later, agent_id=first["agent"]["id"])
    assert (await live.finish(receipt["run_id"]))["agent_revision_id"] == second["revision"]["id"]
    body = journey.observations(later)[0]["body"]
    assert body["model"] == "changed-upstream" and body["temperature"] == 0.8
    assert "AUTHORITY_REVISION_TWO" in json.dumps(body) and "AUTHORITY_REVISION_ONE" not in json.dumps(body)


async def test_multiworker_model_disable_blocks_next_request(multiworker):
    journey = multiworker
    live = journey.live
    model_path = journey.base + "/models/" + live.config["model_id"]
    # Disable a Model after its initial response is in flight; no next outbound call.
    case = await journey.case(gate_at=0)
    journey.plan(
        case,
        gate_at=0,
        steps=[{"tool": "live_effect", "arguments": {"case_id": case["case_id"], "token": case["token"]}}],
    )
    receipt = await journey.start(case)
    await journey.ready(case, receipt["run_id"])
    await journey.patch(model_path, {"enabled": False})
    await live.release(case)
    await live.finish(receipt["run_id"], "failed")
    assert len(journey.observations(case)) == 1


@pytest.mark.parametrize("kind", ["mcp", "connector"])
@pytest.mark.parametrize("recover", [False, True])
async def test_multiworker_connection_revocation_blocks_dispatch(multiworker, kind, recover):
    journey = multiworker
    live, lab = journey.live, journey.lab
    resource = await connection(journey, kind)
    agent = await journey.agent(
        **{kind + "_tools": [{kind + "_connection_id": resource["id"], "tools": ["live_echo"]}]}
    )
    case = await journey.case(gate_at=0, steps=[{"tool": "live_echo", "arguments": {"value": "AUTHORITY_NO_DISPATCH"}}])
    before = [
        item
        for item in requests(journey, kind)
        if item["body"].get("method") == "tools/call" or "/tools/execute/" in item["path"]
    ]
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    await journey.ready(case, receipt["run_id"])
    path = f"/api/v1/{kind}-connections/{resource['id']}"
    current = await live.request("GET", path)
    await journey.post(
        path + ("/disable" if kind == "mcp" else "/revoke"), {"expected_version": current["version"]}, expected=200
    )
    if recover:
        await lab.stop(lab.execution_owner(receipt["run_id"]), signal.SIGKILL)
    await live.release(case)
    done = await live.finish(receipt["run_id"], "failed")
    after = [
        item
        for item in requests(journey, kind)
        if item["body"].get("method") == "tools/call" or "/tools/execute/" in item["path"]
    ]
    assert after == before, "Revoked connection dispatched an external tool"
    assert "REMOTE:AUTHORITY_NO_DISPATCH" not in (done.get("output_text") or "")
    if recover:
        assert len(await lab.attempts(receipt["run_id"])) == 2


async def test_multiworker_environment_disable_blocks_write(multiworker):
    journey = multiworker
    live = journey.live
    # Environment provider eligibility is live; a model's already selected write cannot bypass disablement.
    template, template_config, root = await journey.environment_template(preparation="on_use")
    case = await journey.case(
        gate_at=0,
        steps=[{"tool": "write", "arguments": {"file_path": "/workspace/forbidden.txt", "content": "UNAUTHORIZED"}}],
    )
    receipt = await journey.start(case, environment={"template_id": template["id"]})
    await journey.ready(case, receipt["run_id"])
    await journey.patch("/api/v1/environment-providers/" + template_config["provider_id"], {"enabled": False})
    await live.release(case)
    await live.finish(receipt["run_id"])
    assert last_tool_result(journey.observations(case)[-1]) == {
        "error": "Managed tool resources could not be resolved."
    }
    assert not (root / "forbidden.txt").exists()


async def test_multiworker_template_revision_survives_recovery(multiworker):
    journey = multiworker
    live, lab = journey.live, journey.lab
    template, template_config, root = await journey.environment_template(preparation="on_use")
    (root / "proof.txt").write_text("TEMPLATE_OLD")
    shared = await journey.post(journey.base + "/environments", {"template_id": template["id"]})
    cases = [
        await journey.case(gate_at=0, steps=[{"tool": "view", "arguments": {"file_path": "/workspace/proof.txt"}}])
        for _ in range(3)
    ]
    receipts = [await journey.start(case, environment={"environment_id": shared["id"]}) for case in cases]
    owners = []
    for case, receipt in zip(cases, receipts, strict=True):
        await journey.ready(case, receipt["run_id"])
        owners.append(lab.execution_owner(receipt["run_id"]))
        run = await live.run(receipt["run_id"])
        environment = await live.request("GET", "/api/v1/environments/" + run["environment_id"])
        assert environment["generation"] == 0 and environment["template_revision_id"] == template["default_revision_id"]
    assert len({worker.pid for worker in owners}) == 3
    other = lab.root / "template-new"
    other.mkdir(mode=0o700)
    (other / "proof.txt").write_text("TEMPLATE_NEW")
    revision = await journey.post(
        "/api/v1/environment-templates/" + template["id"] + "/revisions",
        {
            **template_config,
            "configuration": {**template_config["configuration"], "root": {"path": str(other)}},
            "expected_version": template["version"],
        },
    )
    await lab.stop(owners[0], signal.SIGKILL)
    await live.release(cases[1])
    await live.release(cases[2])
    await live.wait(
        lambda: lab.attempts(receipts[0]["run_id"]),
        lambda attempts: len(attempts) == 2,
        "template recovery on another worker",
    )
    await live.release(cases[0])
    for receipt in receipts:
        run = await live.finish(receipt["run_id"])
        assert "TEMPLATE_OLD" in run["output_text"] and "TEMPLATE_NEW" not in run["output_text"]
        environment = await live.request("GET", "/api/v1/environments/" + run["environment_id"])
        assert environment["generation"] == 1 and environment["template_revision_id"] == template["default_revision_id"]
    later = await journey.case(steps=[{"tool": "view", "arguments": {"file_path": "/workspace/proof.txt"}}])
    receipt = await journey.start(later, environment={"template_id": template["id"]})
    run = await live.finish(receipt["run_id"])
    assert "TEMPLATE_NEW" in run["output_text"]
    environment = await live.request("GET", "/api/v1/environments/" + run["environment_id"])
    assert environment["template_revision_id"] == revision["id"]
