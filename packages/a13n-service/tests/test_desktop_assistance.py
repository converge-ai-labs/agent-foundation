"""Assistance crosses real HTTP, SQL admission, checkpoint and deferred-resume boundaries."""

from datetime import timedelta
from uuid import uuid4

import pytest
from a13n_harness.environment.models import EnvironmentState
from a13n_service.infra.db import now, transaction
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.desktop_assistance import maintenance
from a13n_service.runs.desktop_assistance import service as assistance
from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow, DesktopTargetRow
from a13n_service.runs.environments.adapters import provider_identity
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from pydantic import SecretStr
from sqlalchemy import select

pytestmark = pytest.mark.anyio
GENERATION = "a" * 32


class Endpoint:
    def __init__(self):
        self.grants = {}
        self.calls = []
        self.fail_revoke = False

    async def dispatch(self, runtime, target, request, *, operation_id):
        self.calls.append(request)
        if isinstance(request, wire.Describe):
            return wire.Description(
                protocol_version=1,
                generation=GENERATION,
                port=6081,
                max_ttl_seconds=60,
                read_only_enforced=True,
                revocation_fences_grants=True,
                websocket_url="wss://desktop.example/websockify",
            )
        if isinstance(request, wire.Settle):
            assert not any(grant.mode == "control" and grant.state == "active" for grant in self.grants.values())
            return wire.Settlement(generation=GENERATION, input_cleanup_complete=True)
        if isinstance(request, wire.Grant):
            self.grants[request.id] = wire.AccessGrant(
                id=request.id,
                generation=GENERATION,
                epoch=request.epoch,
                mode=request.mode,
                state="active",
                remaining_seconds=60,
                token=SecretStr("test-token"),
                password=SecretStr("test-pass"),
            )
        elif isinstance(request, wire.Inspect) and request.operation == "revoke":
            if self.fail_revoke:
                raise RuntimeError("simulated lost revocation response")
            self.grants[request.id] = self.grants[request.id].model_copy(
                update={"state": "revoked", "remaining_seconds": 0}
            )
        return self.grants[request.id]


async def waiting(service, scripted_model, runs_kit, monkeypatch, *, start_only=False, provider_key="e2b"):
    await runs_kit.pause_sweeps(service)
    from a13n_harness.environment.models import EnvironmentAvailability, EnvironmentPermissionSet
    from a13n_service.runs.environments import execution

    from . import environments_support as support

    permissions = EnvironmentPermissionSet(operations={"environment.computer.observe", "environment.computer.click"})
    monkeypatch.setattr(
        support,
        "DESCRIPTOR",
        support.DESCRIPTOR.model_copy(
            update={"operation_families": frozenset({"computer"}), "permissions": permissions}
        ),
    )
    monkeypatch.setattr(
        support.FakeExecution,
        "availability",
        property(lambda self: EnvironmentAvailability(status="available", ready_families=frozenset({"computer"}))),
    )

    async def connector(runtime, target, **kwargs):
        support.BACKEND.instances[target.environment_id] = "running"
        return support.FakeConnector(target.environment_id, target.state)

    monkeypatch.setattr(execution, "execution_connector", connector)
    endpoint = Endpoint()
    monkeypatch.setattr(assistance, "dispatch", endpoint.dispatch)
    monkeypatch.setattr(maintenance, "dispatch", endpoint.dispatch)
    response = await service.client.post(
        f"{service.api}/environment-providers",
        json={
            "type": provider_key,
            "name": "Desktop",
            "credential": {"api_key": "test-unused-key"},
            "config": {"organization_id": "test-org"} if provider_key == "daytona" else {},
        },
    )
    assert response.status_code == 201, response.text
    recipe = {
        "desktop": True,
        "desktop_assistance": True,
        "snapshot" if provider_key == "daytona" else "template": "test-desktop",
    }
    response = await service.client.post(
        f"{service.api}/environment-templates",
        json={"name": "Desktop", "provider_id": response.json()["id"], "config": {"recipe": recipe}},
    )
    assert response.status_code == 201, response.text
    response = await service.client.post(f"{service.api}/environments", json={"template_id": response.json()["id"]})
    assert response.status_code == 201, response.text
    environment_id = response.json()["id"]
    async with transaction(service.runtime.storage) as session:
        environment = await session.get(EnvironmentRow, environment_id)
        environment.provider_identity = provider_identity(
            service.runtime.registry, await read_provider(session, EnvironmentProviderRow, environment.provider_id)
        )
        environment.status = "ready"
        environment.operation_id = environment.operation_started_at = None
        environment.handle = Handle(
            recipe=recipe,
            state=EnvironmentState(
                provider_key=provider_key,
                state_version="1",
                state={
                    "target_id" if provider_key == "daytona" else "sandbox_id": "test-" + uuid4().hex,
                },
            ),
        ).model_dump(mode="json")
    agent = await runs_kit.create_agent(
        service, scripted_model, toolsets={"computer": {"enabled": True}, "configuration": {"enabled": True}}
    )
    first = await runs_kit.start_thread(
        service,
        agent,
        "Ask for help",
        environments=[
            {
                "name": "workspace",
                "environment_id": environment_id,
                "permission_ceiling": {"operations": ["environment.computer.observe", "environment.computer.click"]},
            }
        ],
    )
    if start_only:
        return first, endpoint
    scripted_model.call(
        "request_desktop_assistance", {"mount": "workspace", "reason": "Please choose the file"}, call_id="help"
    )
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, first["run"]["id"])
    assert run["status"] == "waiting", run.get("failure")
    response = await service.client.get(f"{service.api}/runs/{run['id']}/desktop-assistance/help")
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["state"] == "awaiting_user", view
    return run, view, endpoint


def control(view, session_id="browser_session_one_00001"):
    return {"session_id": session_id, "epoch": view["epoch"]}


async def post(service, view, action, body):
    return await service.client.post(f"{service.api}/desktop-assistance/{view['id']}/{action}", json=body)


@pytest.mark.parametrize("provider_key", ["e2b", "daytona"])
async def test_human_grants_revoke_before_exact_resume(service, scripted_model, runs_kit, monkeypatch, provider_key):
    run, view, endpoint = await waiting(service, scripted_model, runs_kit, monkeypatch, provider_key=provider_key)
    resume = {"approvals": {}, "calls": {"help": {"status": "returned", "value": {"outcome": "completed"}}}}
    response = await service.client.post(
        f"{service.api}/runs/{run['id']}/resume", json=resume, headers=runs_kit.fresh_key()
    )
    assert response.status_code == 409, response.text
    viewed = await post(service, view, "view", control(view))
    assert viewed.status_code == 200, viewed.text
    assert viewed.headers["cache-control"] == "no-store"
    assert viewed.json()["mode"] == "view"
    claimed = await post(service, view, "control", control(view))
    assert claimed.status_code == 200, claimed.text
    view = claimed.json()["assistance"]
    rival = await post(service, view, "control", control(view, "browser_session_two_00002"))
    assert rival.status_code == 409
    completed = await post(service, view, "complete", {**control(view), "note": "File chosen"})
    assert completed.status_code == 200, completed.text
    assert all(grant.state == "revoked" for grant in endpoint.grants.values())
    assert isinstance(endpoint.calls[-1], wire.Settle)
    assert "password" not in completed.text and "test-token" not in completed.text
    # A sweep or late browser disconnect must not reopen an already answered wait.
    await maintenance.reconcile(service.runtime, view["id"])
    disconnected = await post(service, view, "disconnect", control(view))
    assert disconnected.status_code == 200, disconnected.text
    assert disconnected.json()["state"] == "ready_to_resume"
    assert disconnected.json()["outcome"] == completed.json()["outcome"]
    resume["calls"]["help"]["value"] = completed.json()["outcome"]
    response = await service.client.post(
        f"{service.api}/runs/{run['id']}/resume", json=resume, headers=runs_kit.fresh_key()
    )
    assert response.status_code == 201, response.text
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        target = await session.get(DesktopTargetRow, row.target_key)
        assert row.state == "consumed" and row.successor_id == response.json()["id"]
        assert target.holder_id == row.id and not row.observed


async def test_expired_controller_is_revoked_without_answering_wait(service, scripted_model, runs_kit, monkeypatch):
    _, view, endpoint = await waiting(service, scripted_model, runs_kit, monkeypatch)
    claimed = await post(service, view, "control", control(view))
    assert claimed.status_code == 200, claimed.text
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        row.lease_expires_at = await now(session) - timedelta(seconds=1)
    await maintenance.reconcile(service.runtime, view["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        assert row.state == "awaiting_user" and row.outcome is None and row.controller_id is None
    assert all(grant.state == "revoked" for grant in endpoint.grants.values())


@pytest.mark.parametrize("provider_key", ["e2b", "daytona"])
async def test_lost_revoke_blocks_resume_and_reconciles(service, scripted_model, runs_kit, monkeypatch, provider_key):
    _, view, endpoint = await waiting(service, scripted_model, runs_kit, monkeypatch, provider_key=provider_key)
    claimed = await post(service, view, "control", control(view))
    assert claimed.status_code == 200, claimed.text
    view = claimed.json()["assistance"]
    endpoint.fail_revoke = True
    response = await post(service, view, "complete", control(view))
    assert response.status_code >= 500, response.text
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        assert row.state == "blocked" and row.outcome is None
    endpoint.fail_revoke = False
    await maintenance.reconcile(service.runtime, view["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        assert row.state == "awaiting_user" and row.outcome is None


async def test_successor_requires_observation_and_next_model_boundary(service, scripted_model, runs_kit, monkeypatch):
    from a13n_harness.environment.models import EnvironmentAction as Action
    from a13n_harness.environment.models import EnvironmentError
    from a13n_service.runs.claim import claim
    from a13n_service.runs.desktop_assistance import admission

    run, view, _ = await waiting(service, scripted_model, runs_kit, monkeypatch)
    completed = await post(service, view, "complete", control(view))
    assert completed.status_code == 200, completed.text
    response = await service.client.post(
        f"{service.api}/runs/{run['id']}/resume",
        json={"approvals": {}, "calls": {"help": {"status": "returned", "value": completed.json()["outcome"]}}},
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    [lease] = await claim(service.runtime, worker_id="test-desktop", worker_build="test", limit=1)
    async with transaction(service.runtime.storage) as session:
        row = await session.get(EnvironmentRow, view["environment_id"])
        state = Handle.model_validate(row.handle).state

    async def execute(action):
        async with admission.operation(service.runtime, lease, view["environment_id"], state, action):
            pass

    await admission.replan(service.runtime, lease)
    for action in [
        Action.COMPUTER_CLICK,
        Action.COMPUTER_ZOOM,
        Action.SHELL_EXEC,
        Action.PROCESS_START,
        Action.FILE_WRITE_TEXT,
    ]:
        with pytest.raises(EnvironmentError, match="Observe the desktop"):
            await execute(action)
    await execute(Action.COMPUTER_DESCRIBE)
    await admission.replan(service.runtime, lease)
    with pytest.raises(EnvironmentError):
        await execute(Action.COMPUTER_CLICK)
    await execute(Action.COMPUTER_OBSERVE)
    with pytest.raises(EnvironmentError):
        await execute(Action.COMPUTER_CLICK)
    await admission.replan(service.runtime, lease)
    await execute(Action.COMPUTER_CLICK)
    # A failed read is drained; a possibly dispatched mutation remains unknown.
    for action in [Action.COMPUTER_OBSERVE, Action.COMPUTER_ZOOM]:
        with pytest.raises(ValueError):
            async with admission.operation(service.runtime, lease, view["environment_id"], state, action):
                raise ValueError("failed screenshot")
    async with transaction(service.runtime.storage) as session:
        target = await session.get(DesktopTargetRow, admission.target_key(state))
        assert target.inflight == 0
    # Invalid key chords never reached the desktop; they must not prevent handoff.
    with pytest.raises(EnvironmentError, match="invalid chord"):
        async with admission.operation(
            service.runtime, lease, view["environment_id"], state, Action.COMPUTER_PRESS_KEYS
        ):
            raise EnvironmentError(
                "invalid chord", code="environment_request_invalid", details={"dispatch_stage": "pre_dispatch"}
            )
    async with transaction(service.runtime.storage) as session:
        target = await session.get(DesktopTargetRow, admission.target_key(state))
        assert target.inflight == 0
    # The same error code without non-dispatch evidence is not proof of safety.
    with pytest.raises(EnvironmentError):
        async with admission.operation(
            service.runtime, lease, view["environment_id"], state, Action.COMPUTER_PRESS_KEYS
        ):
            raise EnvironmentError("uncertain chord", code="environment_request_invalid")
    async with transaction(service.runtime.storage) as session:
        target = await session.get(DesktopTargetRow, admission.target_key(state))
        assert target.inflight == 1
    with pytest.raises(ValueError):
        async with admission.operation(service.runtime, lease, view["environment_id"], state, Action.COMPUTER_CLICK):
            raise ValueError("outcome unknown")
    async with transaction(service.runtime.storage) as session:
        target = await session.get(DesktopTargetRow, admission.target_key(state))
        assert target.inflight == 2


@pytest.mark.parametrize("provider_key", ["e2b", "daytona"])
async def test_alias_admission_and_lifecycle_share_native_reservation(
    service, scripted_model, runs_kit, monkeypatch, provider_key
):
    from a13n_service.infra.errors import ServiceError
    from a13n_service.infra.ids import new_object_id
    from a13n_service.runs.desktop_assistance import admission
    from a13n_service.runs.environments.lifecycle import in_use

    _, view, _ = await waiting(service, scripted_model, runs_kit, monkeypatch, provider_key=provider_key)
    async with transaction(service.runtime.storage) as session:
        original = await session.get(EnvironmentRow, view["environment_id"])
        # A second registered alias must not create a second authority domain.
        values = {column.name: getattr(original, column.name) for column in EnvironmentRow.__table__.columns}
        values["id"] = new_object_id("env")
        alias = EnvironmentRow(**values)
        session.add(alias)
        await session.flush()
        assert await session.scalar(select(in_use(alias.id)))
        with pytest.raises(ServiceError):
            await admission.admit_mounts(session, [alias])


async def test_close_releases_controller_but_preserves_wait(service, scripted_model, runs_kit, monkeypatch):
    run, view, endpoint = await waiting(service, scripted_model, runs_kit, monkeypatch)
    claim = await post(service, view, "control", control(view))
    view = claim.json()["assistance"]
    disconnected = await post(service, view, "disconnect", control(view))
    assert disconnected.status_code == 200, disconnected.text
    assert disconnected.json()["state"] == "awaiting_user" and disconnected.json()["outcome"] is None
    assert all(grant.state == "revoked" for grant in endpoint.grants.values())
    assert (await runs_kit.get_run(service, run["id"]))["status"] == "waiting"
    second = await post(
        service, disconnected.json(), "control", control(disconnected.json(), "second_browser_session_0001")
    )
    assert second.status_code == 200, second.text
    assert second.json()["assistance"]["epoch"] > view["epoch"]


async def test_wait_deadline_revokes_all_connections_without_resuming(service, scripted_model, runs_kit, monkeypatch):
    run, view, endpoint = await waiting(service, scripted_model, runs_kit, monkeypatch)
    assert (await post(service, view, "view", control(view))).status_code == 200
    assert (await post(service, view, "control", control(view))).status_code == 200
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        row.deadline = await now(session) - timedelta(seconds=1)
    await maintenance.reconcile(service.runtime, view["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopAssistanceRow, view["id"])
        target = await session.get(DesktopTargetRow, row.target_key)
        assert row.state == "abandoned" and target.holder_id is None
    assert all(grant.state == "revoked" for grant in endpoint.grants.values())
    assert (await runs_kit.get_run(service, run["id"]))["status"] == "waiting"


async def test_permission_loss_revokes_controller_and_viewer_cannot_connect(
    service, scripted_model, runs_kit, monkeypatch
):
    _, view, endpoint = await waiting(service, scripted_model, runs_kit, monkeypatch)
    accounts = f"{service.workspace}/service-accounts"
    account = await service.client.post(accounts, json={"name": "desktop-runner", "role": "runner"})
    assert account.status_code == 201, account.text
    key = await service.client.post(f"{accounts}/{account.json()['id']}/keys", json={"name": "desktop"})
    headers = {"authorization": "Bearer " + key.json()["secret"]}
    path = f"{service.api}/desktop-assistance/{view['id']}/control"
    claimed = await service.client.post(path, json=control(view), headers=headers)
    assert claimed.status_code == 200, claimed.text
    account_path = f"{accounts}/{account.json()['id']}"
    response = await service.client.patch(
        account_path, json={"role": "viewer"}, headers=runs_kit.if_match(account.json())
    )
    assert response.status_code == 200, response.text
    denied = await service.client.post(
        path + "?renew=true", json=control(claimed.json()["assistance"]), headers=headers
    )
    assert denied.status_code == 403, denied.text
    await maintenance.reconcile(service.runtime, view["id"])
    assert all(grant.state == "revoked" for grant in endpoint.grants.values())
    denied = await service.client.post(
        f"{service.api}/desktop-assistance/{view['id']}/view", json=control(view), headers=headers
    )
    assert denied.status_code == 403
