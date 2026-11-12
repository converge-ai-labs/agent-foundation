"""Real admission/storage/ownership with a deterministic Envd boundary for update races."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPMethodError
from a13n_harness.environment.models import EnvironmentError
from a13n_service.infra.db import short_session, transaction
from a13n_service.runs.attempts import LeaseLost
from a13n_service.runs.claim import claim
from a13n_service.runs.environments.egress.synchronization import Synchronizer
from a13n_service.runs.environments.egress.tables import SessionCredentialRow
from a13n_service.runs.environments.tables import ThreadExecutionContextRow, ThreadSessionBindingRow
from a13n_service.runs.schemas import EnvironmentMount, Outcome
from a13n_service.runs.seal import seal
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow, ToolExecutionRow
from sqlalchemy import select

from .environments_support import start, with_fake_providers
from .test_session_credentials import selection

pytestmark = pytest.mark.anyio


class Device:
    def __init__(self):
        self.descriptor = SimpleNamespace(device_id="device-test", generation=18446744073709551615)
        self.opens = self.attaches = 0
        self.session = None

    async def open_session(self, *, configuration, required_methods):
        self.opens += 1
        self.session = Session(self, configuration)
        return self.session

    async def attach_session(self, descriptor, *, owner_epoch):
        self.attaches += 1
        assert self.session is not None
        assert descriptor.session_id == self.session.session_id
        self.session.descriptor = self.session.descriptor.model_copy(update={"owner_epoch": owner_epoch})
        return self.session


class Session:
    def __init__(self, device, configuration):
        self.client = self
        self.session_id = f"native-{device.opens}"
        self.is_closed = False
        self.detaches = 0
        self.updates = []
        self.after_update = None
        self.values = {item.env: item.value for item in configuration.egress.secrets}
        self.descriptor = eip.SessionDescriptor(
            device_id=device.descriptor.device_id,
            generation=device.descriptor.generation,
            session_id=self.session_id,
            owner_epoch=1,
            working_directory="/workspace",
            path_style="posix",
            system_info=eip.SystemInfo(os="linux"),
            available_methods=("egress.update",),
            limits=eip.EIPLimits(
                max_request_bytes=65536,
                max_response_bytes=65536,
                max_concurrent_operations=4,
                max_processes=4,
                max_operation_duration_ms=10000,
                max_output_preview_bytes=1024,
                max_output_bytes_per_stream=65536,
                max_transfer_frame_bytes=65536,
                max_concurrent_file_transfers=2,
                max_file_transfer_bytes=65536,
                max_file_bytes=65536,
            ),
            execution_features=eip.ExecutionFeatures(
                process_count_limit=False,
                memory_bytes_limit=False,
                cpu_time_limit=False,
                signal_interrupt=False,
                signal_terminate=False,
            ),
            lifecycle=eip.SessionLifecyclePolicy(idle_timeout_ms=30000),
            boundary=eip.ExecutionBoundary(
                sandbox=eip.DisabledSandbox(mode="disabled"),
                egress="controlled",
                privilege_gain_blocked=True,
                backend="linux_bubblewrap",
                policy_digest="a" * 64,
            ),
            egress=eip.EgressStatus(
                revision=1,
                destinations=configuration.egress.destinations,
                secrets=tuple(
                    eip.EgressSecretStatus(env=item.env, sentinel="private-sentinel", inject_hosts=item.inject_hosts)
                    for item in configuration.egress.secrets
                ),
            ),
        )

    @asynccontextmanager
    async def use(self):
        yield

    async def describe(self):
        return self.descriptor

    async def detach(self):
        self.detaches += 1

    async def close(self):
        self.is_closed = True

    async def egress_update(self, params):
        status = self.descriptor.egress
        assert status is not None and params.expected_revision == status.revision
        self.updates.append(params)
        for name in params.remove_secrets:
            self.values.pop(name, None)
        self.values.update({item.env: item.value for item in params.set_secrets})
        status = status.model_copy(update={"revision": status.revision + 1})
        self.descriptor = self.descriptor.model_copy(update={"egress": status})
        if self.after_update is not None:
            callback, self.after_update = self.after_update, None
            await callback()
        return eip.EgressUpdateResult(egress=status)


@pytest.fixture
async def prepared(service, runs_kit):
    await runs_kit.pause_sweeps(service)
    tasks = [task for task in service.runtime.tasks.services if task.get_name() == "session-credentials"]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    env = await with_fake_providers(service)
    created = await service.client.post(
        f"{service.api}/egress-credentials",
        json={
            "name": "GitHub",
            "allowed_hosts": ["api.github.com"],
            "value": "initial-private-token",
        },
    )
    assert created.status_code == 201, created.text
    started = await start(
        env,
        options={
            "environment": {
                "mode": "fresh",
                "template_id": env.template["id"],
                "session_configuration": selection(created.json()["id"]),
            }
        },
    )
    assert started["run"] is not None, started
    (lease,) = await claim(service.runtime, worker_id="credential-worker", worker_build="test", limit=1)
    mount = EnvironmentMount.model_validate(started["run"]["environment_mounts"][0])
    borrower = await service.runtime.environment_contexts.get(lease, mount)
    device = Device()
    options = dict(
        target=mount.environment_id,
        expected_device_id=device.descriptor.device_id,
        configuration=eip.SessionConfiguration(),
        required_methods=frozenset(),
        expected_boundary=None,
    )
    return SimpleNamespace(
        service=service,
        credential=created.json(),
        lease=lease,
        mount=mount,
        borrower=borrower,
        device=device,
        options=options,
        synchronizer=Synchronizer(service.runtime),
    )


async def rotate(prepared, value):
    service = prepared.service
    path = f"{service.api}/egress-credentials/{prepared.credential['id']}"
    current = await service.client.get(path)
    response = await service.client.put(
        f"{path}/value", json={"value": value}, headers={"if-match": current.headers["etag"]}
    )
    assert response.status_code == 202, response.text
    return response.json()


async def applied(prepared):
    async with short_session(prepared.service.runtime.storage) as db:
        return (
            await db.scalars(
                select(SessionCredentialRow).where(
                    SessionCredentialRow.thread_id == prepared.lease.thread_id,
                )
            )
        ).one()


async def test_rotate_live_and_idle_then_attach_latest_without_recreating(prepared):
    p = prepared
    assert (await applied(p)).applied_credential_version is None
    async with p.borrower.borrow(p.device, **p.options) as session:
        native_id = session.session_id
        assert session.values == {"GH_TOKEN": "initial-private-token"}
        row = await applied(p)
        assert row.applied_credential_version == 1
        assert row.applied_device_generation == Decimal(18446744073709551615)
        await rotate(p, "live-private-token")
        assert await p.synchronizer.scan() == 1
        assert session.values == {"GH_TOKEN": "live-private-token"}
        assert (await applied(p)).applied_credential_version == 2
        async with short_session(p.service.runtime.storage) as db:
            binding = await db.get_one(ThreadSessionBindingRow, (p.lease.thread_id, p.mount.environment_id, "default"))
            saved = json.dumps(binding.head)
            assert "private-token" not in saved and "private-sentinel" not in saved
    calls = (p.device.opens, p.device.attaches, len(session.updates))
    await rotate(p, "idle-private-token")
    await rotate(p, "latest-private-token")
    assert await p.synchronizer.scan() == 0
    assert (p.device.opens, p.device.attaches, len(session.updates)) == calls
    async with p.borrower.borrow(p.device, **p.options) as restored:
        assert restored.session_id == native_id
        assert restored.values == {"GH_TOKEN": "latest-private-token"}
        assert (await applied(p)).applied_credential_version == 4
    assert p.device.opens == 1


async def test_rotation_during_update_records_only_sent_version_and_repairs_unknown_result(prepared):
    p = prepared
    async with p.borrower.borrow(p.device, **p.options) as session:
        await rotate(p, "second-token")

        async def rotate_again():
            await rotate(p, "third-token")

        session.after_update = rotate_again
        assert await p.synchronizer.scan() == 1
        assert (await applied(p)).applied_credential_version == 2
        assert session.values["GH_TOKEN"] == "second-token"
        assert await p.synchronizer.scan() == 1
        assert (await applied(p)).applied_credential_version == 3
        await rotate(p, "fourth-token")

        async def lost_response():
            raise TimeoutError

        session.after_update = lost_response
        assert await p.synchronizer.scan() == 0
        assert session.values["GH_TOKEN"] == "fourth-token"
        assert (await applied(p)).applied_credential_version == 3
        assert await p.synchronizer.scan() == 0  # Backoff does not claim success.
        assert await p.synchronizer.scan(force=True) == 1
        assert (await applied(p)).applied_credential_version == 4


async def test_idle_revocation_removes_on_attach_and_refuses_new_commands(prepared):
    p = prepared
    async with p.borrower.borrow(p.device, **p.options):
        pass
    path = f"{p.service.api}/egress-credentials/{p.credential['id']}"
    current = await p.service.client.get(path)
    response = await p.service.client.post(f"{path}/revoke", headers={"if-match": current.headers["etag"]})
    assert response.status_code == 202
    assert await p.synchronizer.scan() == 0 and p.device.attaches == 0
    with pytest.raises(EnvironmentError, match="revoked"):
        async with p.borrower.borrow(p.device, **p.options):
            pytest.fail("revoked credentials cannot prepare successfully")
    assert p.device.opens == 1 and p.device.attaches == 1
    assert p.device.session.values == {}
    assert (await applied(p)).applied_credential_version == 2


async def test_lost_owner_cannot_publish_applied_version(prepared):
    p = prepared
    async with p.borrower.borrow(p.device, **p.options) as session:
        await rotate(p, "new-token")

        async def transfer():
            async with transaction(p.service.runtime.storage) as db:
                row = await db.get_one(ThreadExecutionContextRow, p.lease.thread_id)
                row.generation += 1

        session.after_update = transfer
        assert await p.synchronizer.scan() == 0
        assert (await applied(p)).applied_credential_version == 1
        with pytest.raises(LeaseLost):
            async with p.borrower.borrow(p.device, **p.options):
                pytest.fail("stale owner must refuse new borrowers")


async def test_unknown_refresh_of_current_version_is_retried(prepared):
    p = prepared
    async with p.borrower.borrow(p.device, **p.options) as session:

        async def lost_response():
            raise TimeoutError("private transport details")

        session.after_update = lost_response
        with pytest.raises(TimeoutError):
            async with p.borrower.borrow(p.device, **p.options):
                pytest.fail("prepare must wait for a confirmed update")
        assert (await applied(p)).applied_credential_version == 1
        assert p.borrower.owner.sync_failed
        assert await p.synchronizer.scan() == 0
        assert await p.synchronizer.scan(force=True) == 1
        assert not p.borrower.owner.sync_failed


async def test_new_native_incarnation_cannot_inherit_previous_application_success(prepared, monkeypatch):
    p = prepared
    async with p.borrower.borrow(p.device, **p.options):
        original = p.device.session.session_id
    assert (await applied(p)).applied_credential_version == 1
    # A Device restart proves the old native Session cannot be reused.
    p.device.descriptor.generation -= 1
    open_session = p.device.open_session

    async def lost_response():
        raise TimeoutError

    async def open_with_unknown_sync(**kwargs):
        session = await open_session(**kwargs)
        session.after_update = lost_response
        return session

    monkeypatch.setattr(p.device, "open_session", open_with_unknown_sync)
    with pytest.raises(TimeoutError):
        async with p.borrower.borrow(p.device, **p.options):
            pytest.fail("unconfirmed native application must refuse preparation")
    assert p.device.session.session_id != original
    row = await applied(p)
    assert row.applied_credential_version is None and row.applied_session_id is None
    async with p.borrower.borrow(p.device, **p.options) as restored:
        row = await applied(p)
        assert row.applied_session_id == restored.session_id
        assert row.applied_device_generation == Decimal(p.device.descriptor.generation)
        assert row.applied_credential_version == 1


async def test_revision_conflict_reloads_latest_value(prepared, monkeypatch):
    p = prepared
    async with p.borrower.borrow(p.device, **p.options) as session:
        await rotate(p, "second-token")
        update = session.egress_update
        calls = 0

        async def competing_update(params):
            nonlocal calls
            calls += 1
            if calls == 1:
                await rotate(p, "third-token")
                raise EIPMethodError(
                    eip.EIPError(
                        code=-32060,
                        message="Concurrent egress update",
                        data=eip.EIPErrorData(
                            error_type=eip.ErrorType.CONFLICT, retry_hint="never", dispatch_stage="pre_dispatch"
                        ),
                    )
                )
            return await update(params)

        monkeypatch.setattr(session, "egress_update", competing_update)
        assert await p.synchronizer.scan() == 1
        assert calls == 2
        assert session.values == {"GH_TOKEN": "third-token"}
        assert (await applied(p)).applied_credential_version == 3


async def test_retained_tool_keeps_rotation_live_after_run_ends_then_applies_revoke(prepared):
    p = prepared
    async with p.borrower.borrow(p.device, **p.options) as session:
        current = datetime.now(UTC)
        async with transaction(p.service.runtime.storage) as db:
            run = await db.get_one(RunRow, p.lease.run_id)
            db.add(
                ToolExecutionRow(
                    id="exec-credential-background",
                    organization_id=run.organization_id,
                    workspace_id=run.workspace_id,
                    thread_id=run.thread_id,
                    run_id=run.id,
                    attempt_id=p.lease.attempt_id,
                    worker_id=p.lease.worker_id,
                    lease_token_hash="test-lease",
                    lease_expires_at=current + timedelta(seconds=60),
                    status="running",
                    created_at=current,
                    record={},
                    environment_mounts=run.environment_mounts,
                )
            )
            await seal(
                db,
                p.service.runtime,
                await db.get_one(ThreadRow, run.thread_id),
                run,
                await db.get_one(AttemptRow, p.lease.attempt_id),
                Outcome.cancelled(),
                at=current,
            )
        await rotate(p, "background-token")
        assert await p.synchronizer.scan() == 1
        assert session.values == {"GH_TOKEN": "background-token"}
        path = f"{p.service.api}/egress-credentials/{p.credential['id']}"
        credential = await p.service.client.get(path)
        revoked = await p.service.client.post(f"{path}/revoke", headers={"if-match": credential.headers["etag"]})
        assert revoked.status_code == 202, revoked.text
        assert await p.synchronizer.scan() == 1
        assert session.values == {}
        assert (await applied(p)).applied_credential_version == 3
