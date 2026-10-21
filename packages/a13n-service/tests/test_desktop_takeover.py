"""User takeover crosses real execution, HTTP and SQL boundaries without a fabricated tool call."""

import asyncio
import json

import pytest
from a13n_service.infra.db import transaction
from a13n_service.runs.desktop_assistance import maintenance
from a13n_service.runs.desktop_assistance.tables import DesktopExecutionRow

from .test_desktop_assistance import control, post, waiting

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("winner", ["user_before_dispatch", "user_during_discovery", "agent"])
@pytest.mark.parametrize("provider_key", ["e2b", "daytona", "modal"])
async def test_simultaneous_assistance_and_takeover_keep_one_resumable_wait(
    service, scripted_model, runs_kit, monkeypatch, winner, provider_key
):
    from a13n_service.providers.environment import desktop_access as wire
    from a13n_service.runs.desktop_assistance import service as assistance
    from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow, DesktopTargetRow
    from sqlalchemy import select

    first, endpoint = await waiting(
        service, scripted_model, runs_kit, monkeypatch, start_only=True, provider_key=provider_key
    )
    generate, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    scripted_model.call(
        "request_desktop_assistance", {"mount": "workspace", "reason": "Choose the file"}, call_id="help", gate=generate
    )
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    user_view = await open_desktop(service, first["run"])
    assert (await post(service, user_view, "view", control(user_view))).status_code == 200
    if winner == "user_during_discovery":

        async def slow_discovery(*args, **kwargs):
            entered.set()
            await release.wait()
            return await endpoint.dispatch(*args, **kwargs)

        monkeypatch.setattr(assistance, "dispatch", slow_discovery)
    elif winner == "agent":
        original = assistance.request_assistance

        async def reserved(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(assistance, "request_assistance", reserved)
    if winner != "user_before_dispatch":
        generate.set()
        await asyncio.wait_for(entered.wait(), 10)
    requested = await post(service, user_view, "request-control", {})
    if winner == "agent":
        assert requested.status_code == 409, requested.text
        assert requested.json()["error"]["details"]["reason"] == "desktop_agent_assistance_pending"
        # Stale tabs may retry either entry; neither can create another controller.
        for action in ("request-control", "control"):
            assert (await post(service, user_view, action, control(user_view))).status_code == 409
        reopened = await service.client.post(
            f"{service.api}/runs/{first['run']['id']}/desktop-control",
            json={"mount": "workspace", "reason": "Retry"},
        )
        assert reopened.status_code == 409 and "desktop_agent_assistance_pending" in reopened.text
    else:
        assert requested.status_code == 200 and requested.json()["state"] == "preparing", requested.text
    assert not any(isinstance(call, wire.Grant) and call.mode == "control" for call in endpoint.calls)
    assert (await post(service, user_view, "control", control(user_view))).status_code == 409
    generate.set()
    release.set()
    await asyncio.wait_for(running, 15)
    monkeypatch.setattr(assistance, "dispatch", endpoint.dispatch)
    # The old read-only panel can release its grant after the other origin wins.
    assert (await post(service, user_view, "disconnect", control(user_view))).status_code == 200
    assert all(grant.state == "revoked" for grant in endpoint.grants.values())
    run = await runs_kit.get_run(service, first["run"]["id"])
    assert run["status"] == "waiting", run
    async with transaction(service.runtime.storage) as session:
        execution = await session.get(DesktopExecutionRow, run["id"])
        assert execution.inflight == 0 and not execution.uncertain
        rows = (
            await session.scalars(select(DesktopAssistanceRow).where(DesktopAssistanceRow.run_id == run["id"]))
        ).all()
        targets = (
            await session.scalars(select(DesktopTargetRow).where(DesktopTargetRow.holder_id.in_([r.id for r in rows])))
        ).all()
        assert len(targets) == 1
        owner = next(row for row in rows if row.id == targets[0].holder_id)
        assert owner.cause == ("agent" if winner == "agent" else "user")
        assert owner.state == "awaiting_user"
        assert len(rows) == (2 if winner == "agent" else 1)
    if winner == "agent":
        assert run["wait_reason"] == "call" and run["pending"]["desktop_control_id"] is None
        assert [item["tool_call_id"] for item in run["pending"]["calls"]] == ["help"]
        view = (await service.client.get(f"{service.api}/runs/{run['id']}/desktop-assistance/help")).json()
    else:
        assert run["wait_reason"] == "desktop_control" and not run["pending"]["calls"]
        view = requested.json()
    granted = await post(service, view, "control", control(view))
    assert granted.status_code == 200, granted.text
    view = granted.json()["assistance"]
    assert (
        await post(service, view, "control", control(view, session_id="different_browser_00001"))
    ).status_code == 409
    completed = await post(service, view, "complete", control(view))
    assert completed.status_code == 200, completed.text
    answers = {"help": {"status": "returned", "value": completed.json()["outcome"]}} if winner == "agent" else {}
    response = await service.client.post(
        f"{service.api}/runs/{run['id']}/resume", json={"calls": answers, "approvals": {}}, headers=runs_kit.fresh_key()
    )
    assert response.status_code == 201, response.text
    scripted_model.say("The task can continue after handback.")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, response.json()["id"]))["status"] == "completed"
    assert all(grant.state == "revoked" for grant in endpoint.grants.values())


async def open_desktop(service, run):
    result = await service.client.post(
        f"{service.api}/runs/{run['id']}/desktop-control", json={"mount": "workspace", "reason": "User correction"}
    )
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("provider_key", ["e2b", "daytona", "modal"])
async def test_takeover_during_generation_skips_stale_tool_then_resumes(
    service, scripted_model, runs_kit, monkeypatch, provider_key
):
    first, endpoint = await waiting(
        service, scripted_model, runs_kit, monkeypatch, start_only=True, provider_key=provider_key
    )
    gate = asyncio.Event()
    runs_kit.lookup(scripted_model, call_id="stale", gate=gate)
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    view = await open_desktop(service, first["run"])
    assert view["state"] == "viewing" and view["tool_call_id"] is None
    await maintenance.reconcile(service.runtime, view["id"])
    response = await service.client.get(f"{service.api}/runs/{first['run']['id']}/desktop-control")
    assert response.json()["state"] == "viewing"
    assert (await post(service, view, "view", control(view))).status_code == 200
    assert (await post(service, view, "control", control(view))).status_code == 409
    requested = await post(service, view, "request-control", {})
    assert requested.status_code == 200, requested.text
    assert requested.json()["state"] == "preparing"
    assert (await post(service, view, "control", control(view))).status_code == 409
    gate.set()
    await asyncio.wait_for(running, 15)
    run = await runs_kit.get_run(service, first["run"]["id"])
    assert run["status"] == "waiting", run
    assert run["wait_reason"] == "desktop_control"
    assert run["pending"] == {"calls": [], "approvals": [], "desktop_control_id": view["id"]}
    assert scripted_model.requests.empty(), "No model request may follow the pause fence"
    resume = {"approvals": {}, "calls": {}}
    denied = await service.client.post(
        f"{service.api}/runs/{run['id']}/resume", json=resume, headers=runs_kit.fresh_key()
    )
    assert denied.status_code == 409
    claimed = await post(service, view, "control", control(view))
    assert claimed.status_code == 200, claimed.text
    view = claimed.json()["assistance"]
    # Dropping a controller closes its input but does not answer the pause.
    assert (await post(service, view, "disconnect", control(view))).status_code == 200
    assert (await runs_kit.get_run(service, run["id"]))["status"] == "waiting"
    completed = await post(service, view, "complete", control(view))
    assert completed.status_code == 200, completed.text
    await maintenance.reconcile(service.runtime, view["id"])
    result = await service.client.post(
        f"{service.api}/runs/{run['id']}/resume", json=resume, headers=runs_kit.fresh_key()
    )
    assert result.status_code == 201, result.text
    scripted_model.say("I will inspect the changed desktop before further operations.")
    await (await runs_kit.attempt(service))
    successor = await runs_kit.get_run(service, result.json()["id"])
    assert successor["status"] == "completed", successor
    history = str(await scripted_model.request())
    assert "Not executed: the user requested desktop control" in history
    assert "The user returned desktop control" in history
    assert all(grant.state == "revoked" for grant in endpoint.grants.values())
    # Once observation/replanning released the reservation, later cleanup must not
    # rewrite the accepted handback outcome just because its source Run is old.
    from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow, DesktopTargetRow

    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        assert row.state == "consumed"
        target = await session.get(DesktopTargetRow, row.target_key)
        target.holder_id = None
        outcome = row.outcome
    await maintenance.reconcile(service.runtime, view["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        assert row.state == "consumed" and row.outcome == outcome


@pytest.mark.parametrize("provider_key", ["e2b", "daytona", "modal"])
async def test_unknown_tool_work_prevents_takeover(service, scripted_model, runs_kit, monkeypatch, provider_key):
    first, _ = await waiting(service, scripted_model, runs_kit, monkeypatch, start_only=True, provider_key=provider_key)
    gate = asyncio.Event()
    scripted_model.say("done", gate=gate)
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    view = await open_desktop(service, first["run"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopExecutionRow, first["run"]["id"])
        row.uncertain = True
    response = await post(service, view, "request-control", {})
    assert response.status_code == 409, response.text
    assert "desktop_background_work_unconfirmed" in response.text
    gate.set()
    await running


async def test_readonly_cleanup_does_not_pause_or_settle_active_agent(service, scripted_model, runs_kit, monkeypatch):
    from datetime import timedelta

    from a13n_service.infra.db import now
    from a13n_service.providers.environment import desktop_access as wire
    from a13n_service.runs.desktop_assistance import execution
    from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow

    first, endpoint = await waiting(service, scripted_model, runs_kit, monkeypatch, start_only=True)
    gate = asyncio.Event()
    scripted_model.say("Continue without interruption", gate=gate)
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    view = await open_desktop(service, first["run"])
    assert (await post(service, view, "view", control(view))).status_code == 200
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        row.deadline = await now(session) - timedelta(seconds=1)
        counter = await session.get(DesktopExecutionRow, first["run"]["id"])
        counter.inflight = 1
    endpoint.fail_revoke = True
    with pytest.raises(RuntimeError):
        await maintenance.reconcile(service.runtime, view["id"])
    async with transaction(service.runtime.storage) as session:
        assert await execution.request(session, first["run"]["id"]) is None
    endpoint.fail_revoke = False
    await maintenance.reconcile(service.runtime, view["id"])
    assert not any(isinstance(call, wire.Settle) for call in endpoint.calls)
    async with transaction(service.runtime.storage) as session:
        counter = await session.get(DesktopExecutionRow, first["run"]["id"])
        counter.inflight = 0
    gate.set()
    await running
    assert (await runs_kit.get_run(service, first["run"]["id"]))["status"] == "completed"


@pytest.mark.parametrize("tool_count", [1, 2])
async def test_admitted_tool_drains_and_is_not_replayed(service, scripted_model, runs_kit, monkeypatch, tool_count):
    from a13n_service.runs.boundaries import Boundaries

    first, _ = await waiting(service, scripted_model, runs_kit, monkeypatch, start_only=True)
    entered, release = asyncio.Event(), asyncio.Event()
    before = Boundaries.before_tool_execute
    count = 0

    # Execution runs the before-hooks inside the desktop fence, so a held hook holds an admitted tool.
    async def slow(self, ctx, *, call, tool_def, args):
        nonlocal count
        count += 1
        if count == tool_count:
            entered.set()
        await release.wait()
        return await before(self, ctx, call=call, tool_def=tool_def, args=args)

    monkeypatch.setattr(Boundaries, "before_tool_execute", slow)
    scripted_model._script(
        {
            "deltas": [
                {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": f"admitted-{index}",
                            "type": "function",
                            "function": {"name": "view", "arguments": json.dumps({"file_path": runs_kit.GUIDE})},
                        }
                        for index in range(tool_count)
                    ]
                }
            ],
            "interval": 0,
            "finish": "tool_calls",
            "gate": None,
            "to": None,
        }
    )
    running = await runs_kit.attempt(service)
    await asyncio.wait_for(entered.wait(), 10)
    view = await open_desktop(service, first["run"])
    assert (await post(service, view, "request-control", {})).status_code == 200
    assert (await post(service, view, "control", control(view))).status_code == 409
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopExecutionRow, first["run"]["id"])
        assert row.inflight == tool_count
    release.set()
    await asyncio.wait_for(running, 10)
    run = await runs_kit.get_run(service, first["run"]["id"])
    assert run["wait_reason"] == "desktop_control", run
    completed = await post(service, view, "complete", control(view))
    assert completed.status_code == 200, completed.text
    resumed = await service.client.post(
        f"{service.api}/runs/{run['id']}/resume", json={"approvals": {}, "calls": {}}, headers=runs_kit.fresh_key()
    )
    assert resumed.status_code == 201, resumed.text
    scripted_model.say("Continue with preserved tool results")
    await (await runs_kit.attempt(service))
    assert count == tool_count
    assert (await runs_kit.get_run(service, resumed.json()["id"]))["status"] == "completed"


async def test_completed_run_with_uncertain_work_cannot_grant_control(service, scripted_model, runs_kit, monkeypatch):
    # Request while generation is active, then simulate the worker's transient-failure release.
    first, _ = await waiting(service, scripted_model, runs_kit, monkeypatch, start_only=True)
    gate = asyncio.Event()
    scripted_model.say("done", gate=gate)
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    view = await open_desktop(service, first["run"])
    assert (await post(service, view, "request-control", {})).status_code == 200
    # Make the remaining work uncertain before the safe boundary.
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopExecutionRow, first["run"]["id"])
        row.uncertain = True
    # A final answer may finish the task, but must never grant control over uncertain work.
    gate.set()
    await running
    response = await post(service, view, "control", control(view))
    assert response.status_code == 409, response.text


async def test_worker_loss_after_request_never_replays_the_task(service, scripted_model, runs_kit, monkeypatch):
    from a13n_service.runs.seal import LeaseExpirer
    from a13n_service.runs.tables import AttemptRow
    from sqlalchemy import update

    first, _ = await waiting(service, scripted_model, runs_kit, monkeypatch, start_only=True)
    scripted_model.say("lost", gate=asyncio.Event())
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    view = await open_desktop(service, first["run"])
    assert (await post(service, view, "request-control", {})).status_code == 200
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(AttemptRow)
            .where(AttemptRow.run_id == first["run"]["id"])
            .values(lease_expires_at=AttemptRow.created_at)
        )
    await LeaseExpirer(service.runtime, batch=10)()
    run = await runs_kit.get_run(service, first["run"]["id"])
    assert run["status"] == "failed", run
    assert run["failure"]["code"] == "desktop_pause_attempt_lost"
    assert (await post(service, view, "control", control(view))).status_code == 409


async def test_task_tree_is_explicitly_refused_without_partial_pause(service, scripted_model, runs_kit, monkeypatch):
    first, _ = await waiting(service, scripted_model, runs_kit, monkeypatch, start_only=True)
    gate = asyncio.Event()
    scripted_model.say("done", gate=gate)
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    view = await open_desktop(service, first["run"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopExecutionRow, first["run"]["id"])
        row.supported = False
    denied = await post(service, view, "request-control", {})
    assert denied.status_code == 409 and "desktop_task_tree_unsupported" in denied.text
    assert (await runs_kit.get_run(service, first["run"]["id"]))["status"] == "running"
    gate.set()
    await running


async def test_expired_user_control_requires_cleanup_and_explicit_resume(
    service, scripted_model, runs_kit, monkeypatch
):
    from datetime import timedelta

    from a13n_service.infra.db import now
    from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow

    first, endpoint = await waiting(service, scripted_model, runs_kit, monkeypatch, start_only=True)
    gate = asyncio.Event()
    runs_kit.lookup(scripted_model, call_id="stale", gate=gate)
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    view = await open_desktop(service, first["run"])
    assert (await post(service, view, "request-control", {})).status_code == 200
    gate.set()
    await asyncio.wait_for(running, 15)
    claimed = await post(service, view, "control", control(view))
    assert claimed.status_code == 200
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        row.deadline = await now(session) - timedelta(seconds=1)
    endpoint.fail_revoke = True
    with pytest.raises(RuntimeError):
        await maintenance.reconcile(service.runtime, view["id"])
    path = f"{service.api}/runs/{first['run']['id']}/resume"
    body = {"calls": {}, "approvals": {}}
    denied = await service.client.post(path, json=body, headers=runs_kit.fresh_key())
    assert denied.status_code == 409
    endpoint.fail_revoke = False
    await maintenance.reconcile(service.runtime, view["id"])
    assert (await runs_kit.get_run(service, first["run"]["id"]))["status"] == "waiting"
    assert all(grant.state == "revoked" for grant in endpoint.grants.values())
    resumed = await service.client.post(path, json=body, headers=runs_kit.fresh_key())
    assert resumed.status_code == 201, resumed.text
    scripted_model.say("Observe the changed desktop before further operations.")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, resumed.json()["id"]))["status"] == "completed"


@pytest.mark.parametrize("provider_key", ["e2b", "daytona", "modal"])
async def test_initial_view_discovers_once_and_does_not_pause(
    service, scripted_model, runs_kit, monkeypatch, provider_key
):
    from a13n_service.providers.environment import desktop_access as wire

    first, endpoint = await waiting(
        service, scripted_model, runs_kit, monkeypatch, start_only=True, provider_key=provider_key
    )
    path = f"{service.api}/runs/{first['run']['id']}/desktop-control/view"
    body = {"mount": "workspace", "reason": "View only", "session_id": "browser_session_000001"}
    for _ in range(2):
        endpoint.calls.clear()
        response = await service.client.post(path, json=body)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        access = response.json()
        assert access["mode"] == "view" and access["assistance"]["state"] == "viewing"
        assert [call.operation for call in endpoint.calls] == ["describe", "grant"]
        assert isinstance(endpoint.calls[-1], wire.Grant) and endpoint.calls[-1].mode == "view"
        assert (await runs_kit.get_run(service, first["run"]["id"]))["status"] == "accepted"
    assert len(endpoint.grants) == 1
    assert (
        await post(
            service, access["assistance"], "disconnect", control(access["assistance"], session_id=body["session_id"])
        )
    ).status_code == 200


@pytest.mark.parametrize(
    "failure", ["readonly", "revocation", "route", "target", "generation", "grant", "cleanup", "superseded", "timeout"]
)
async def test_initial_view_fails_closed_and_releases_scope(service, scripted_model, runs_kit, monkeypatch, failure):
    from contextlib import asynccontextmanager

    from a13n_service.providers.environment import desktop_access as wire
    from a13n_service.runs.desktop_assistance import service as assistance
    from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow
    from a13n_service.runs.environments.tables import EnvironmentRow
    from sqlalchemy import select

    first, endpoint = await waiting(
        service, scripted_model, runs_kit, monkeypatch, start_only=True, provider_key="modal"
    )
    closed = False

    @asynccontextmanager
    async def scoped(runtime, selected, *, operation_id):
        async def send(request):
            if isinstance(request, wire.Grant) and failure == "grant":
                raise RuntimeError("Grant outcome unknown")
            reply = await endpoint.dispatch(runtime, selected, request, operation_id=operation_id)
            if isinstance(request, wire.Grant) and failure == "timeout":
                await asyncio.Event().wait()
            if isinstance(request, wire.Describe):
                if failure in {"readonly", "revocation", "route"}:
                    field, value = {
                        "readonly": ("read_only_enforced", False),
                        "revocation": ("revocation_fences_grants", False),
                        "route": ("websocket_url", "ws://external.example/"),
                    }[failure]
                    return reply.model_copy(update={field: value})
                if failure == "target":
                    async with transaction(runtime.storage) as session:
                        env = await session.get(EnvironmentRow, selected.environment_id)
                        handle = dict(env.handle)
                        handle["state"] = {
                            **handle["state"],
                            "state": {**handle["state"]["state"], "target_id": "replacement"},
                        }
                        env.handle = handle
                if failure == "generation":
                    # A previously opened context must not adopt a new generation.
                    async with transaction(runtime.storage) as session:
                        row = await session.scalar(
                            select(DesktopAssistanceRow).where(DesktopAssistanceRow.run_id == first["run"]["id"])
                        )
                        row.generation = "b" * 32
            if isinstance(request, wire.Grant) and failure == "superseded":
                async with transaction(runtime.storage) as session:
                    row = await session.scalar(
                        select(DesktopAssistanceRow).where(DesktopAssistanceRow.run_id == first["run"]["id"])
                    )
                    row.operation_id = "different-operation"
            return reply

        try:
            yield send
        finally:
            nonlocal closed
            closed = True
            if failure == "cleanup":
                raise RuntimeError("Cleanup unconfirmed")

    if failure == "generation":
        await open_desktop(service, first["run"])
        endpoint.calls.clear()
    monkeypatch.setattr(assistance, "access_dispatch", scoped)
    if failure == "timeout":
        monkeypatch.setattr(assistance, "_ACCESS_TIMEOUT_SECONDS", 1)
    response = await service.client.post(
        f"{service.api}/runs/{first['run']['id']}/desktop-control/view",
        json={"mount": "workspace", "reason": "View only", "session_id": "browser_session_000001"},
    )
    assert response.status_code >= 400, response.text
    assert closed
    assert "websocket_url" not in response.text and "test-pass" not in response.text
    if failure in {"readonly", "revocation", "route", "target", "generation"}:
        assert not endpoint.grants
    async with transaction(service.runtime.storage) as session:
        row = await session.scalar(
            select(DesktopAssistanceRow).where(DesktopAssistanceRow.run_id == first["run"]["id"])
        )
        if failure in {"generation", "grant", "cleanup", "timeout"}:
            assert row.state == "blocked" and row.outcome is None
