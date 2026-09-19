"""Real HTTP Device discovery, accepted directory snapshots and independent execution."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
from datetime import timedelta
from pathlib import Path

import pytest
from a13n_envd_client import EIPDeviceConnection
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_service.environments.domain import (
    CreateProviderRequest,
    ExistingEnvironmentSelection,
    RegisterEnvironmentRequest,
)
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentRecord
from a13n_service.environments.mount_domain import AcceptedRunMount, AddEnvironmentMountRequest
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.environments.mounts import RunEnvironmentMountService
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.environments.selection import Omitted
from a13n_service.environments.service import EnvironmentService
from a13n_service.environments.websocket.admission import OnlineAdmission, OnlineEvidence
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.environment_preview import has_input_environment
from a13n_service.interactions.environment_selection import (
    EnvironmentDefault,
    RetainedRunEnvironment,
    select_run_environment,
)
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.thread_creation import allocate_thread
from a13n_service.interactions.thread_domain import CreateThreadRequest
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import short_session, transaction
from sqlalchemy import func, select

from tests.hooks.support import hook_actor, seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import AGENT_ID, NOW, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _authority, _worker
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


@pytest.fixture
async def http_device(interaction_sessions, tmp_path):
    binary = os.environ.get("A13N_ENVD_TEST_BINARY")
    if binary is None:
        pytest.skip("Set A13N_ENVD_TEST_BINARY to exercise Service against native envd")
    root = tmp_path / "device"
    root.mkdir()
    for name in ("alpha", "beta"):
        (root / name).mkdir()
    credential = tmp_path / "credential"
    credential.write_text("service-test-token")
    configuration = tmp_path / "device.json"
    configuration.write_text(json.dumps({"device_id": "service-test-device", "default_working_directory": str(root)}))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        str(Path(binary).resolve()),
        "--config",
        str(configuration),
        env={
            "A13N_ENVD_RUNTIME_DIR": str(tmp_path / "runtime"),
            "A13N_ENVD_TRANSPORT": "http",
            "A13N_ENVD_HTTP_BIND": f"127.0.0.1:{port}",
            "A13N_ENVD_HTTP_CREDENTIAL_FILE": str(credential),
            "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "loopback",
            "LANG": "C.UTF-8",
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await seed_hook_actor_access(interaction_sessions)
        protector = SecretProtector.from_base64(
            encoded_key=base64.b64encode(b"k" * 32).decode(), encryption_key_id="test"
        )
        service = EnvironmentService(
            interaction_sessions, ProviderCatalog(select_builtin_environment_providers(("http_envd",))), protector
        )
        provider = await service.create_provider(
            actor=hook_actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateProviderRequest(
                type="http_envd",
                name="Device",
                configuration={"endpoint": f"http://127.0.0.1:{port}"},
                credential={"token": "service-test-token"},
            ),
        )
        environment = await service.create_environment(
            actor=hook_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="register-device",
            request=RegisterEnvironmentRequest(
                provider_id=provider.id, configuration={}, device_id="service-test-device"
            ),
        )
        async with asyncio.timeout(10):
            while True:
                try:
                    await service.device_info(actor=hook_actor(), environment_id=environment.id)
                    break
                except EnvironmentManagementError:
                    if process.returncode is not None:
                        pytest.fail("Native envd exited before discovery")
                    await asyncio.sleep(0.02)
        yield service, provider, environment, root
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 5)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise
        assert process.returncode == 0, stderr.decode(errors="replace")


async def test_discovery_is_session_free_bounded_and_use_authorized(http_device, interaction_sessions, monkeypatch):
    service, _, environment, root = http_device

    async def unexpected_session(*args, **kwargs):
        pytest.fail("Browsing must not open an execution Session")

    monkeypatch.setattr(EIPDeviceConnection, "open_session", unexpected_session)
    assert environment.device_id == "service-test-device"
    info = await service.device_info(actor=hook_actor(), environment_id=environment.id)
    assert info.model_dump() == {
        "environment_id": environment.id,
        "path_style": "posix",
        "default_working_directory": str(root),
        "directory_discovery": True,
    }
    first = await service.device_directories(actor=hook_actor(), environment_id=environment.id, limit=1)
    second = await service.device_directories(
        actor=hook_actor(), environment_id=environment.id, offset=first.next_offset, limit=1
    )
    assert [item.name for page in (first, second) for item in page.entries] == ["alpha", "beta"]
    with pytest.raises(EnvironmentManagementError) as error:
        await service.device_directories(actor=hook_actor(), environment_id=environment.id, path=str(root / "absent"))
    assert error.value.code == "environment_directory_unavailable"
    assert await service.device_info(actor=hook_actor(), environment_id=environment.id) == info
    async with transaction(interaction_sessions) as database:
        for model in (RunRecord, ThreadRecord, SessionRecord, RunEnvironmentMountRecord):
            assert await database.scalar(select(func.count()).select_from(model)) == 0
        roles = (
            await database.scalars(select(RoleBindingRecord).where(RoleBindingRecord.workspace_id == WORKSPACE_ID))
        ).all()
        for role in roles:
            role.role_key = "viewer"
    with pytest.raises(EnvironmentManagementError) as denied:
        await service.device_info(actor=hook_actor(), environment_id=environment.id)
    assert denied.value.code == "environment_not_found"


async def test_acceptance_freezes_default_outside_sql_and_replay_needs_no_device(
    http_device, interaction_sessions, interaction_object_store, monkeypatch
):
    service, provider, environment, root = http_device
    original = service.devices.describe
    observations = []

    async def describe(target):
        assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        observations.append(target)
        return await original(target)

    monkeypatch.setattr(service.devices, "describe", describe)
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        environment_id=environment.id,
        devices=service.devices,
        idempotency_key="captured-directory",
    )
    assert len(observations) == 1
    async with transaction(interaction_sessions) as database:
        stored = await database.get(RunRecord, run.id)
        assert stored.environment_working_directory == str(root)
        thread = await database.get(ThreadRecord, run.thread_id)
        assert thread.default_environment_working_directory == str(root)
        thread.default_environment_working_directory = str(root / "beta")
        (await database.get(EnvironmentProviderRecord, provider.id)).enabled = False
    await _accept_root(
        interaction_sessions,
        interaction_object_store,
        environment_id=environment.id,
        devices=service.devices,
        idempotency_key="captured-directory",
    )
    assert len(observations) == 1
    async with short_session(interaction_sessions) as database:
        assert (await database.get(RunRecord, run.id)).environment_working_directory == str(root)


async def test_default_resolution_reauthorizes_after_external_io(
    http_device, interaction_sessions, interaction_object_store, monkeypatch
):
    service, provider, environment, _ = http_device
    original = service.devices.describe

    async def describe(target):
        assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        result = await original(target)
        async with transaction(interaction_sessions) as database:
            (await database.get(EnvironmentProviderRecord, provider.id)).enabled = False
        return result

    monkeypatch.setattr(service.devices, "describe", describe)
    with pytest.raises(EnvironmentManagementError):
        await _accept_root(
            interaction_sessions, interaction_object_store, environment_id=environment.id, devices=service.devices
        )
    async with short_session(interaction_sessions) as database:
        for model in (RunRecord, ThreadRecord, SessionRecord):
            assert await database.scalar(select(func.count()).select_from(model)) == 0


async def test_empty_thread_and_retained_run_keep_complete_binding(
    http_device, interaction_sessions, interaction_object_store
):
    service, _, environment, root = http_device
    thread = await allocate_thread(
        interaction_sessions,
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        body=CreateThreadRequest(environment=ExistingEnvironmentSelection(environment_id=environment.id)),
        idempotency_key="empty",
        admission=OnlineAdmission(interaction_sessions, devices=service.devices),
    )
    assert thread.default_environment_working_directory == str(root)
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        environment_id=environment.id,
        environment_working_directory=str(root / "alpha"),
    )
    async with transaction(interaction_sessions) as database:
        source = (await database.get(RunRecord, run.id)).to_resource()
        current = await database.get(ThreadRecord, run.thread_id)
        current.default_environment_working_directory = str(root / "beta")
        inherited = await select_run_environment(
            database,
            run=source,
            workspace_id=WORKSPACE_ID,
            intent=RetainedRunEnvironment(run.id, run.thread_id),
            online=OnlineEvidence({}),
        )
        assert inherited.environment_working_directory == str(root / "alpha")
        current_selection = await select_run_environment(
            database,
            run=source,
            workspace_id=WORKSPACE_ID,
            intent=EnvironmentDefault.thread,
            online=OnlineEvidence({}),
        )
        assert current_selection.environment_working_directory == str(root / "beta")
        has_environment = await has_input_environment(
            database,
            actor=hook_actor(),
            agent_id=AGENT_ID,
            choice=Omitted.UNSET,
            inherited_id=environment.id,
            inherited_working_directory=str(root / "beta"),
        )
        assert has_environment


async def test_primary_and_mount_same_device_have_independent_sessions(
    http_device, interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    service, _, environment, root = http_device
    sessions = []
    original = EIPDeviceConnection.open_session

    async def opened(device, **kwargs):
        value = await original(device, **kwargs)
        sessions.append(value)
        return value

    monkeypatch.setattr(EIPDeviceConnection, "open_session", opened)
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        environment_id=environment.id,
        environment_working_directory=str(root / "alpha"),
    )
    mounts = RunEnvironmentMountService(
        interaction_sessions, OnlineAdmission(interaction_sessions, devices=service.devices), clock=lambda: NOW
    )
    receipt = await mounts.add(
        actor=hook_actor(),
        run_id=run.id,
        idempotency_key="same-device-mount",
        request=AddEnvironmentMountRequest(
            name="other", environment_id=environment.id, working_directory=str(root / "beta")
        ),
    )
    assert receipt.working_directory == str(root / "beta")
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    context = await prepare_permissions(interaction_sessions, run, _authority(claim))
    lifecycle = EnvironmentLifecycle(
        interaction_sessions, service.catalog, service.protector, clock=lambda: NOW + timedelta(seconds=2)
    )
    async with short_session(interaction_sessions) as database:
        mount = AcceptedRunMount.from_record(await database.get(RunEnvironmentMountRecord, (run.id, "other")))
    primary = await prepare_run_environment(lifecycle, context)
    sibling = await prepare_run_environment(lifecycle, context, mount=mount)
    try:
        assert len(sessions) == 2 and sessions[0] is not sessions[1]
        for adapter, name, directory in ((primary, "workspace", "alpha"), (sibling, "other", "beta")):
            await adapter.enter(mount_id=name)
            await adapter.ensure_ready(frozenset({"files"}))
            assert adapter.descriptor.working_directory == str(root / directory)
            await adapter.operations.files.write_text(str(root / directory / "marker.txt"), name, mode="create")
            assert (root / directory / "marker.txt").read_text() == name
        await primary.close()
        assert (await sibling.operations.files.read_text(str(root / "beta" / "marker.txt"))).text == "other"
        assert await service.device_info(actor=hook_actor(), environment_id=environment.id)
        async with short_session(interaction_sessions) as database:
            stored = await database.get(EnvironmentRecord, environment.id)
            assert stored.external_configuration["configuration"] == {}
            assert stored.state["state"] == {"device_id": "service-test-device"}
    finally:
        await primary.close()
        await sibling.close()


async def test_invalid_directory_fails_only_execution_and_cleans_http_owner(
    http_device, interaction_sessions, interaction_object_store, tmp_path
):
    service, _, environment, root = http_device
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        environment_id=environment.id,
        environment_working_directory=str(root / "missing"),
    )
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    context = await prepare_permissions(interaction_sessions, run, _authority(claim))
    lifecycle = EnvironmentLifecycle(
        interaction_sessions, service.catalog, service.protector, clock=lambda: NOW + timedelta(seconds=2)
    )
    from a13n_harness.providers.environment.errors import EnvironmentProviderError

    with pytest.raises(EnvironmentProviderError):
        await prepare_run_environment(lifecycle, context)
    assert (
        await service.device_info(actor=hook_actor(), environment_id=environment.id)
    ).default_working_directory == str(root)
    async with short_session(interaction_sessions) as database:
        assert (await database.get(RunRecord, run.id)).environment_working_directory == str(root / "missing")


async def test_mount_omission_captures_default_once_and_receipt_is_retained(
    http_device, interaction_sessions, interaction_object_store, monkeypatch
):
    service, _, environment, root = http_device
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    mounts = RunEnvironmentMountService(
        interaction_sessions, OnlineAdmission(interaction_sessions, devices=service.devices), clock=lambda: NOW
    )
    original = service.devices.describe
    calls = 0

    async def describe(target):
        nonlocal calls
        calls += 1
        assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        return await original(target)

    monkeypatch.setattr(service.devices, "describe", describe)
    request = AddEnvironmentMountRequest(name="default", environment_id=environment.id)
    receipt = await mounts.add(actor=hook_actor(), run_id=run.id, idempotency_key="default-directory", request=request)
    assert receipt.working_directory == str(root)

    async def changed(target):
        return (await original(target)).model_copy(update={"default_working_directory": str(root / "beta")})

    monkeypatch.setattr(service.devices, "describe", changed)
    assert (
        await mounts.add(actor=hook_actor(), run_id=run.id, idempotency_key="default-directory", request=request)
        == receipt
    )
    assert calls == 1
    async with short_session(interaction_sessions) as database:
        stored = await database.get(RunEnvironmentMountRecord, (run.id, "default"))
        assert stored.working_directory == str(root)
        assert (await database.get(ThreadRecord, run.thread_id)).default_environment_id is None


async def test_worker_model_tools_map_primary_and_live_device_directories(
    http_device, interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    """Exercise virtual paths through the real Worker, not direct provider operations."""
    from unittest.mock import Mock

    from a13n_service.models.model_factory import NativeModelFactory
    from a13n_service.settings import Settings
    from anyio import create_task_group, fail_after, sleep
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from .worker_helpers import worker_runtime

    service, _, environment, root = http_device
    (root / "alpha/source.txt").write_text("Primary Device directory")
    (root / "beta/reference.txt").write_text("Additional Device directory")
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        environment_id=environment.id,
        environment_working_directory=str(root / "alpha"),
    )
    mounts = RunEnvironmentMountService(
        interaction_sessions, OnlineAdmission(interaction_sessions, devices=service.devices)
    )
    opened = []
    original = EIPDeviceConnection.open_session

    async def open_session(device, **kwargs):
        session = await original(device, **kwargs)
        opened.append(session.session_id)
        return session

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    requests = 0

    async def model(messages, info):
        nonlocal requests
        requests += 1
        returns = [
            part
            for message in messages
            if message.kind == "request"
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if requests == 1:
            await mounts.add(
                actor=hook_actor(),
                run_id=run.id,
                idempotency_key="model-boundary-addition",
                request=AddEnvironmentMountRequest(
                    name="reference-files",
                    environment_id=environment.id,
                    working_directory=str(root / "beta"),
                ),
            )
            yield {0: DeltaToolCall(name="view", json_args='{"file_path":"source.txt"}', tool_call_id="primary")}
        elif requests == 2:
            assert "Primary Device directory" in str(returns[-1].content)
            assert "/environment/reference-files" in str(messages)
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": f"/environment/reference-files{root}/beta/reference.txt"}),
                    tool_call_id="additional",
                )
            }
        elif requests == 3:
            assert "Additional Device directory" in str(returns[-1].content)
            # A selected cwd is not a file access root. The same
            # binding can read another directory on its Device.
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": f"/environment/reference-files{root}/alpha/source.txt"}),
                    tool_call_id="outside-cwd",
                )
            }
        else:
            assert "Primary Device directory" in str(returns[-1].content)
            yield "Read both selected Device directories."

    factory = Mock(spec=NativeModelFactory)
    factory.build.return_value = FunctionModel(stream_function=model)
    async with worker_runtime(
        interaction_sessions,
        interaction_object_store,
        tmp_path,
        monkeypatch,
        settings=Settings(service={"build_version": "test"}, worker={"concurrency": 1, "poll_interval_seconds": 0.02}),
        model_factory=factory,
        environment_catalog=service.catalog,
    ) as (worker, _):
        loop = worker.execution_loop
        assert loop is not None
        with fail_after(30):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(interaction_sessions) as database:
                        row = await database.get(RunRecord, run.id)
                        if row.status in {"completed", "failed"}:
                            assert row.status == "completed", row.failure_json
                            break
                    await sleep(0.02)
                await loop.drain()
                await loop.wait_stopped()
    assert requests == 4
    assert len(set(opened)) == 2
    async with short_session(interaction_sessions) as database:
        mount = await database.get(RunEnvironmentMountRecord, (run.id, "reference-files"))
        assert mount.application_status == "ready"
        assert mount.working_directory == str(root / "beta")
        assert mount.applied_attempt_id is not None
    observed = (await mounts.list(actor=hook_actor(), run_id=run.id)).items[0]
    assert observed.application_status == "ready"
    assert observed.applied_attempt_id == mount.applied_attempt_id
    assert observed.observed_at is not None
