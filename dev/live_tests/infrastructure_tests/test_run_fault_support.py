"""Offline checks for fault isolation, bounded claims and cancellation cleanup."""

import asyncio
import json
import socket
from types import SimpleNamespace

import anyio
import httpx2
import pytest
import uvicorn
from a13n_service.ids import new_object_id
from a13n_service.storage import ObjectConflict, ObjectStoreUnavailable
from pydantic import ValidationError

from ..infrastructure.run_faults import Faults, arm
from ..run_recovery.run_fault_model import completion

pytestmark = pytest.mark.anyio


async def test_concurrent_claims_are_unique_and_exhaustible(tmp_path):
    path = arm(tmp_path, "boundary", point="checkpoint.after", role="worker", match={"run_id": "run_owned"}, times=3)
    # Independent Faults instances share only disk, as separate Hosts do.
    tickets = await asyncio.gather(
        *(Faults(tmp_path, "worker").take("checkpoint.after", run_id="run_owned") for _ in range(16))
    )
    claimed = [ticket for ticket in tickets if ticket is not None]
    assert sorted(ticket.evidence["hit"] for ticket in claimed) == [1, 2, 3]
    assert {json.loads(item.read_text())["hit"] for item in path.glob("hit-*.json")} == {1, 2, 3}
    assert await Faults(tmp_path, "worker").take("checkpoint.after", run_id="run_owned") is None


async def test_wrong_role_run_point_and_root_do_not_consume_fault(tmp_path):
    path = arm(tmp_path, "owned", point="state.put_after", role="worker", match={"run_id": "run_owned"})
    assert await Faults(tmp_path, "control").take("state.put_after", run_id="run_owned") is None
    worker = Faults(tmp_path, "worker")
    assert await worker.take("state.put_before", run_id="run_owned") is None
    assert await worker.take("state.put_after", run_id="run_other") is None
    assert await Faults(tmp_path / "another-lab", "worker").take("state.put_after", run_id="run_owned") is None
    assert not list(path.glob("hit-*.json"))
    ticket = await worker.take("state.put_after", run_id="run_owned")
    assert ticket is not None and ticket.evidence["hit"] == 1


@pytest.mark.parametrize(("action", "error"), [("unavailable", ObjectStoreUnavailable), ("conflict", ObjectConflict)])
async def test_fault_errors_propagate_with_finished_evidence(tmp_path, action, error):
    path = arm(tmp_path, "failure", point="state.put_before", action=action)
    with pytest.raises(error):
        await Faults(tmp_path, "worker").reach("state.put_before")
    assert json.loads((path / "finished-1.json").read_text())["point"] == "state.put_before"
    await Faults(tmp_path, "worker").reach("state.put_before")


async def test_cancelled_barrier_records_cleanup_and_does_not_rearm(tmp_path):
    path = arm(tmp_path, "cancel", point="checkpoint.before")
    ticket = await Faults(tmp_path, "worker").take("checkpoint.before")
    with anyio.move_on_after(0.05) as scope:
        await ticket.apply()
    assert scope.cancel_called
    assert (path / "finished-1.json").is_file()
    assert await Faults(tmp_path, "worker").take("checkpoint.before") is None


@pytest.mark.parametrize("cancel_requests", [1, 3])
async def test_native_cancellation_preserves_marker_and_never_resumes_effect(tmp_path, monkeypatch, cancel_requests):
    path = arm(tmp_path, "native-cancel", point="tool.before_effect")
    faults = Faults(tmp_path, "worker")
    ticket = await faults.take("tool.before_effect")
    entered = asyncio.Event()
    effects = []

    async def pause(_seconds):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Reproduce overlapping owners cancelling the same task during
            # unwind. An async marker write would be the next cancellation point.
            task = asyncio.current_task()
            for _ in range(cancel_requests - 1):
                task.cancel()
            raise

    monkeypatch.setattr(anyio, "sleep", pause)

    async def tool():
        await ticket.apply()
        effects.append("must not execute")

    task = asyncio.create_task(tool())
    try:
        with anyio.fail_after(2):
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled() and task.cancelling() == cancel_requests
    marker = json.loads((path / "finished-1.json").read_text())
    assert marker["point"] == "tool.before_effect" and marker["hit"] == 1
    assert marker["finished_at"]
    assert effects == [] and not (path / "release").exists()
    assert await faults.take("tool.before_effect") is None


async def test_release_opens_pause_and_existing_evidence_cannot_be_overwritten(tmp_path):
    path = arm(tmp_path, "release", point="checkpoint.after")
    (path / "release").touch()
    await Faults(tmp_path, "worker").reach("checkpoint.after")
    assert (path / "finished-1.json").is_file()
    with pytest.raises(FileExistsError):
        arm(tmp_path, "release", point="checkpoint.before")


@pytest.mark.parametrize("name", ["../escape", "", "absolute/path"])
async def test_fault_name_cannot_escape_owned_root(tmp_path, name):
    with pytest.raises(ValueError):
        arm(tmp_path, name, point="checkpoint.before")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("rule", [{"times": 0}, {"times": 101}, {"point": "invalid"}, {"action": "kill"}])
async def test_invalid_rules_are_not_published(tmp_path, rule):
    with pytest.raises(ValidationError):
        arm(tmp_path, "invalid", **{"point": "checkpoint.before", **rule})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["truncated", "malformed", "401", "429", "503"])
async def test_model_fault_has_wire_evidence_and_repairs_after_configured_count(tmp_path, failure):
    path = tmp_path / "workspace" / "case"
    path.mkdir(parents=True)
    (path / "plan.json").write_text(json.dumps({"failure": failure, "failures": 1}))
    case = SimpleNamespace(case_id="case", scenario="run_fault", token="healthy-response")
    body = {"messages": [{"role": "user", "content": "test"}]}
    response = await completion(case, path, body)
    frames = []
    if failure in {"401", "429", "503"}:
        assert response.status_code == int(failure) and response.headers["retry-after"] == "0"
    elif failure == "truncated":
        with pytest.raises(RuntimeError, match="disconnect"):
            async for frame in response.body_iterator:
                frames.append(frame)
        wire = "".join(frames)
        assert "PARTIAL_" in wire and "[DONE]" not in wire and '"finish_reason": "stop"' not in wire
    else:
        frames = [frame async for frame in response.body_iterator]
        with pytest.raises(json.JSONDecodeError):
            json.loads(frames[0].removeprefix("data: "))
    repaired = await completion(case, path, body)
    wire = "".join([frame async for frame in repaired.body_iterator])
    assert "[DONE]" in wire and '"finish_reason": "stop"' in wire
    assert len((path / "observations.jsonl").read_text().splitlines()) == 2


async def test_queued_case_does_not_reuse_prior_run_tool_result(tmp_path):
    path = tmp_path / "workspace" / "new-case"
    path.mkdir(parents=True)
    (path / "plan.json").write_text("{}")
    case = SimpleNamespace(case_id="new-case", scenario="run_fault", token="fresh")
    body = {
        "messages": [
            {"role": "user", "content": "old-case"},
            {"role": "tool", "content": "stale-result"},
            {"role": "user", "content": "new-case"},
        ]
    }
    response = await completion(case, path, body)
    wire = "".join([frame async for frame in response.body_iterator])
    assert "fresh" in wire and "stale" not in wire


async def test_batch_plan_repeats_unapplied_request_and_advances_only_after_complete_feedback(tmp_path):
    path = tmp_path / "workspace" / "case"
    path.mkdir(parents=True)
    batches = [
        [{"tool": "client", "arguments": {}}, {"tool": "approval", "arguments": {}}],
        [{"tool": "next_client", "arguments": {}}],
    ]
    (path / "plan.json").write_text(json.dumps({"batches": batches}))
    case = SimpleNamespace(case_id="case", scenario="run_fault", token="final")
    body = {
        "tools": [{"function": {"name": name}} for name in ("client", "approval", "next_client")],
        "messages": [
            {"role": "tool", "content": "Old inherited response"},
            {"role": "user", "content": "Start case"},
        ],
    }

    async def requested_tools():
        response = await completion(case, path, body)
        chunks = [chunk async for chunk in response.body_iterator]
        frames = [json.loads(chunk[6:]) for chunk in chunks if chunk.startswith("data: {")]
        return [
            call for frame in frames for choice in frame["choices"] for call in choice["delta"].get("tool_calls", [])
        ]

    for _ in range(2):
        calls = await requested_tools()
        assert [call["function"]["name"] for call in calls] == ["client", "approval"]
        assert [call["index"] for call in calls] == [0, 1]
        assert len({call["id"] for call in calls}) == 2
    body["messages"].extend([{"role": "tool", "content": "No response"}, {"role": "tool", "content": "Rejected"}])
    assert [call["function"]["name"] for call in await requested_tools()] == ["next_client"]
    body["messages"].append({"role": "tool", "content": "Completed"})
    assert await requested_tools() == []


async def test_peer_preserves_partial_http_disconnect_instead_of_graceful_eof(tmp_path):
    from ..infrastructure.fixture_peer import peer_app

    case = {"case_id": "a" * 32, "scenario": "run_fault", "token": "b" * 32}
    path = tmp_path / case["case_id"]
    path.mkdir()
    (path / "case.json").write_text(json.dumps(case))
    (path / "plan.json").write_text(json.dumps({"failure": "truncated", "failures": 1}))
    config = {
        "workspace_root": str(tmp_path),
        "token": "owned-fixture-token",
        "peer_url": "http://127.0.0.1",
        "user_id": new_object_id("usr"),
        "workspace_id": new_object_id("ws"),
        "run_faults": {},
    }
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(peer_app(config), log_level="critical", lifespan="off"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    frames = []
    try:
        with anyio.fail_after(10):
            while not server.started:
                await anyio.sleep(0.01)
        async with httpx2.AsyncClient(trust_env=False, timeout=3) as client:
            with pytest.raises(httpx2.RemoteProtocolError):
                async with client.stream(
                    "POST",
                    origin + "/__live__/model/v1/chat/completions",
                    headers={"Authorization": "Bearer " + config["token"]},
                    json={"stream": True, "messages": [{"role": "user", "content": "LIVE_TEST " + json.dumps(case)}]},
                ) as response:
                    assert response.status_code == 200
                    async for frame in response.aiter_text():
                        frames.append(frame)
        assert "PARTIAL_" in "".join(frames) and "[DONE]" not in "".join(frames)
    finally:
        server.should_exit = True
        with anyio.fail_after(10):
            await serving
        listener.close()


@pytest.mark.parametrize("rollback", [False, True])
async def test_queue_faults_wrap_current_acceptance_and_preserve_transaction_hook(monkeypatch, tmp_path, rollback):
    from a13n_service.interactions.acceptance import RunAcceptanceService

    from ..run_recovery.run_fault_host import _install_queue_faults

    events = []

    async def publish(self, run, state):
        events.append("published")

    async def accept(self, *, run, state, transaction_hook, **kwargs):
        await self._publish_initial(run, state)
        events.append("transaction")
        try:
            await transaction_hook(None, "receipt")
        except ObjectStoreUnavailable:
            events.append("rolled_back")
            raise
        events.append("committed")
        return "receipt"

    async def existing_hook(database, receipt):
        assert receipt == "receipt"
        events.append("existing_hook")

    monkeypatch.setattr(RunAcceptanceService, "_publish_initial", publish)
    monkeypatch.setattr(RunAcceptanceService, "consume_queued", accept)
    faults = Faults(tmp_path, "worker")
    if rollback:
        arm(tmp_path, "rollback", point="queue.rollback", role="worker", action="unavailable")
    _install_queue_faults(faults)
    service = object.__new__(RunAcceptanceService)
    kwargs = {
        "run": SimpleNamespace(id="successor"),
        "state": None,
        "expected_current_run_id": "source",
        "transaction_hook": existing_hook,
    }
    if rollback:
        with pytest.raises(ObjectStoreUnavailable):
            await service.consume_queued(**kwargs)
        assert events == ["published", "transaction", "existing_hook", "rolled_back"]
    else:
        assert await service.consume_queued(**kwargs) == "receipt"
        assert events == ["published", "transaction", "existing_hook", "committed"]
    # A subsequent non-queue publication must not inherit a queue fault context.
    await service._publish_initial(SimpleNamespace(id="unrelated"), None)
    assert events[-1] == "published"


async def test_control_fault_installers_target_current_owners(monkeypatch, tmp_path):
    from a13n_service.interactions.acceptance import RunAcceptanceService
    from a13n_service.interactions.attempts import AttemptExecutionService
    from a13n_service.interactions.objects import RunStateStore
    from a13n_service.interactions.queue import QueuedSubmissionStore
    from a13n_service.interactions.queue_commands import QueuedRunCommands
    from a13n_service.interactions.queue_drain import QueueDrain

    from ..control import fork_fault_host, queue_fault_host

    for owner, names in (
        (RunAcceptanceService, ("_publish_initial",)),
        (AttemptExecutionService, ("yield_attempt",)),
        (RunStateStore, ("claim_writer",)),
        (QueuedSubmissionStore, ("enqueue",)),
        (QueuedRunCommands, ("consume_queued", "prepare_queued_run")),
        (QueueDrain, ("scan",)),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, vars(owner)[name])
    faults = Faults(tmp_path, "control")
    fork_fault_host.install(faults)
    queue_fault_host.install(faults, {})
    assert QueueDrain.scan.__wrapped__ is not None
    assert QueuedRunCommands.prepare_queued_run.__wrapped__ is not None


async def test_state_faults_do_not_intercept_display_objects(monkeypatch, tmp_path):
    from a13n_service.interactions.acceptance import RunAcceptanceService
    from a13n_service.interactions.attempts import AttemptExecutionService
    from a13n_service.interactions.objects import RUN_STATE_CONTENT_TYPE, RunStateStore
    from a13n_service.interactions.terminal_committer import DatabaseAttemptCommitter
    from a13n_service.storage.object_store.s3 import S3ObjectStore

    from ..run_recovery.run_fault_host import install

    for owner, names in (
        (RunAcceptanceService, ("accept_new_thread", "_publish_initial")),
        (AttemptExecutionService, ("fail",)),
        (RunStateStore, ("read", "replace")),
        (DatabaseAttemptCommitter, ("verify_state_outcome",)),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, vars(owner)[name])
    calls = []

    async def put(self, key, source, **kwargs):
        calls.append(key)
        return "stored"

    monkeypatch.setattr(S3ObjectStore, "put", put)
    install({"workspace_root": str(tmp_path), "run_faults": {"queue_faults": False}}, "worker")
    key = "organizations/org/runs/run/display_messages.json"
    result = await S3ObjectStore.put(None, key, b"{}", content_type=RUN_STATE_CONTENT_TYPE, metadata={"run-id": "run"})
    assert result == "stored" and calls == [key]
