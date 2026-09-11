"""Independent real child Runs with individually releasable model requests."""

import json

from .control_support import user_texts


async def start_children(journey, *, mode="running", source=None):
    live = journey.live
    case = await live.case("async_children")
    root = journey.lab.root / "workspace" / case["case_id"]
    (root / "parent_mode").write_text(mode)
    if source is None:
        agent = await journey.control_agent(
            subagent_mode="async", subagents={"child": {"agent_id": live.config["child_agent_id"]}}
        )
        receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    else:
        receipt = await journey.accept(*await journey.command(source, "continue", case=case))
    await journey.lab.wait_evidence(case, "parent_ready", run_id=receipt["run_id"])
    journey.lab.worker_environment["A13N_SERVICE_WORKER_DRAIN_SECONDS"] = "120"
    for _ in range(2):
        await journey.lab.start_worker()
    for index in (0, 1):
        await journey.lab.wait_evidence(case, f"child_{index}_started")
    threads = await live.collection(f"/api/v1/sessions/{receipt['session_id']}/threads")
    children = {}
    for thread in threads:
        if thread["origin_run_id"] == receipt["run_id"]:
            run = await live.run(thread["current_run_id"])
            live.runs.append(run["id"])
            index = next(index for index in (0, 1) if f"LIVE_CHILD {index}" in json.dumps(run["input"]))
            children[index] = run
    assert set(children) == {0, 1}
    return case, root, receipt, children


def parent_observations(journey, case):
    return [
        observed
        for observed in journey.observations(case)
        if not any("LIVE_CHILD " in text for text in user_texts(observed))
    ]
