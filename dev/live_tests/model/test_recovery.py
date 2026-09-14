"""Freeze calling API and settings across acceptance, Worker replacement and Retry."""

import hashlib
import signal

import pytest

from ..infrastructure.client import agent_input

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("boundary", ["accepted", "worker_replacement"])
async def test_snapshot_survives_api_edit_with_current_provider_connection(model_lab, boundary):
    journey, lab = model_lab, model_lab.lab
    case = await journey.model(
        settings={"temperature": 0.2}, **({"gate_at": 1} if boundary == "worker_replacement" else {})
    )
    destination = await journey.model()
    agent = await journey.agent(model_key=case["model"]["key"])
    worker = next(worker for worker in lab.workers if worker.returncode is None)
    if boundary == "accepted":
        await lab.stop(worker)
    receipt = await journey.post(
        journey.base + "/runs",
        {
            "agent_id": agent["agent"]["id"],
            "input": agent_input("Preserve the accepted model configuration."),
        },
        expected=202,
    )
    journey.live.track(receipt)
    if boundary == "accepted":
        assert (await journey.live.run(receipt["run_id"]))["status"] == "accepted"
    else:
        await journey.gated(case, receipt)
    await journey.patch(
        journey.base + "/models/" + case["model"]["id"],
        {
            "model_api": "openai.responses",
            "upstream_model": "edited-model",
            "settings": {"temperature": 0.8},
        },
    )
    await journey.patch(
        journey.base + "/model-providers/" + case["provider"]["id"],
        {
            "configuration": destination["provider"]["configuration"],
            "credential": "fixture-rotated",
        },
    )
    if boundary == "worker_replacement":
        await lab.stop(worker, signal.SIGKILL)
        journey.release_model(case)
    await lab.start_worker()
    result = await journey.live.finish(receipt["run_id"])
    assert result["output_text"] == destination["answer"]
    requests = journey.requests(destination, inference=True)
    assert requests and all(item["path"].endswith("chat/completions") for item in requests)
    assert all(item["body"]["model"] == "manual-model" and item["body"]["temperature"] == 0.2 for item in requests)
    assert all(
        item["headers_sha256"]["authorization"] == hashlib.sha256(b"Bearer fixture-rotated").hexdigest()
        for item in requests
    )
    if boundary == "worker_replacement":
        assert len(await lab.attempts(receipt["run_id"])) == 2
    await journey.invoke(case, agent=agent)
    later = journey.requests(destination, inference=True)[-1]
    assert later["path"].endswith("responses") and later["body"]["model"] == "edited-model"
    assert later["body"]["temperature"] == 0.8


async def test_explicit_retry_keeps_failed_run_api_and_settings(model_lab):
    journey = model_lab
    case = await journey.model(status=401, settings={"temperature": 0.2})
    agent = await journey.agent(model_key=case["model"]["key"])
    failed = await journey.invoke(case, agent=agent, expected="failed")
    before = len(journey.requests(case, inference=True))
    await journey.patch(
        journey.base + "/models/" + case["model"]["id"],
        {
            "model_api": "openai.responses",
            "upstream_model": "edited-model",
            "settings": {"temperature": 0.8},
        },
    )
    journey.plan_model(case, status=None)
    thread = await journey.live.thread(failed["thread_id"])
    retry = await journey.post(
        f"/api/v1/runs/{failed['id']}/retry",
        {
            "expected_thread_version": thread["version"],
        },
        expected=202,
    )
    journey.live.track(retry)
    result = await journey.live.finish(retry["run_id"])
    assert result["retry_of_run_id"] == failed["id"] and result["output_text"] == case["answer"]
    assert await journey.live.run(failed["id"]) == failed
    for request in journey.requests(case, inference=True)[before:]:
        assert request["path"].endswith("chat/completions")
        assert request["body"]["model"] == "manual-model" and request["body"]["temperature"] == 0.2
    await journey.invoke(case, agent=agent)
    assert journey.requests(case, inference=True)[-1]["path"].endswith("responses")
