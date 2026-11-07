"""Admitted versions fence every view of a continuing Thread / Device Session."""

import socket

import pytest
from a13n_envd_client import EIPDeviceConnection
from a13n_harness.environment.envd_policy import EnvdSessionConfiguration
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentError
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection, EnvironmentSelectionPatch

from .test_app import _settings, _write_configuration
from .test_device_runtime import TOKEN, daemon, device_path, requires_envd, resource

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@requires_envd
async def test_admitted_versions_replace_sessions_and_fence_stale_views(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    path = _write_configuration(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    recipe = resource("http", endpoint)
    (tmp_path / "devices").mkdir()
    (tmp_path / "devices/build.yaml").write_text(recipe.model_dump_json())
    opened = []
    original_open = EIPDeviceConnection.open_session

    async def open_session(self, *args, **kwargs):
        result = await original_open(self, *args, **kwargs)
        opened.append(result.session_id)
        return result

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    async with daemon(tmp_path, "http", endpoint=endpoint) as root:
        (root / "second").mkdir()
        async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
            thread = await app.create_thread()
            executor = app._root_runs._executor
            service = executor._environments
            selection = EnvironmentBindingSelection(
                device_id=recipe.id,
                alias="build",
                working_directory=device_path(root),
                session=EnvdSessionConfiguration(working_directory=device_path(root)),
            )

            async def capture(selected):
                result = await executor.capture(
                    thread_id=thread.thread_id,
                    prompt="capture only",
                    environment=EnvironmentSelectionPatch(
                        environment_bindings=(selected,), default_environment=selected.alias
                    ),
                )
                return result.published.value

            first = await capture(selection)
            assert opened == []  # Admission does not connect or open.
            mount, _ = await service._prepare_device_mount(first, first.environment_bindings[0])
            active = await mount.environment.open()
            try:
                renamed = selection.model_copy(
                    update={"alias": "renamed", "working_directory": device_path(root / "second")}
                )
                compatible = await capture(renamed)
                assert compatible.session_versions == first.session_versions
                second_mount, _ = await service._prepare_device_mount(compatible, compatible.environment_bindings[0])
                second_active = await second_mount.environment.open()
                await second_active.close()
                assert len(opened) == 1
                changed = selection.model_copy(
                    update={"session": EnvdSessionConfiguration(working_directory=device_path(root / "second"))}
                )
                with pytest.raises(EnvironmentError) as busy:
                    await capture(changed)
                assert busy.value.code == "environment_busy"
                assert (await app._store.envd_sessions.get(thread.thread_id, recipe.id)).version == 1
            finally:
                await active.close()

            second = await capture(changed)
            assert second.session_versions[recipe.id] == 2
            next_mount, _ = await service._prepare_device_mount(second, second.environment_bindings[0])
            next_active = await next_mount.environment.open()
            await next_active.close()
            assert len(set(opened)) == 2
            third = await capture(selection)
            assert third.session_versions[recipe.id] == 3
            current_mount, _ = await service._prepare_device_mount(third, third.environment_bindings[0])
            current = await current_mount.environment.open()
            try:
                assert len(set(opened)) == 3
                # Old same-process connectors must fail even when their policy matches again.
                with pytest.raises(EnvironmentError) as stale:
                    await mount.environment.open()
                assert stale.value.code == "envd_session_conflict"
                with pytest.raises(EnvironmentError):
                    await service.prepare(first)
                head = await app._store.envd_sessions.get(thread.thread_id, recipe.id)
                assert head.version == head.session_version == 3
                assert head.session.descriptor.session_id == opened[-1]
            finally:
                await current.close()
            await executor.capture(
                thread_id=thread.thread_id,
                prompt="remove",
                environment=EnvironmentSelectionPatch(environment_bindings=(), default_environment=None),
            )
            fourth = await capture(selection)
            assert fourth.session_versions[recipe.id] == 5
            last_mount, _ = await service._prepare_device_mount(fourth, fourth.environment_bindings[0])
            last = await last_mount.environment.open()
            await last.close()
            assert len(set(opened)) == 4


async def test_multi_device_admission_is_atomic_and_checks_durable_work(tmp_path):
    from a13n_harness_ui.composition.models import ResolvedEnvironmentBinding
    from a13n_harness_ui.errors import StoreConflictError
    from a13n_harness_ui.storage.ownership import OwnershipLocks

    from .test_tool_execution_retention import execution

    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        thread = await app.create_thread()
        executor = app._root_runs._executor
        service = executor._environments
        captured = (await executor.capture(thread_id=thread.thread_id, prompt="capture")).published.value
        first = ResolvedEnvironmentBinding(
            device=resource("http", "http://127.0.0.1:1"),
            selection=EnvironmentBindingSelection(device_id="device-build", alias="build", working_directory="/"),
        )
        second = ResolvedEnvironmentBinding(
            device=first.device.model_copy(update={"id": "device-z"}),
            selection=first.selection.model_copy(update={"device_id": "device-z", "alias": "second"}),
        )
        original = await service.admit(captured.model_copy(update={"environment_bindings": (first, second)}))
        before = await app._store.envd_sessions.list(thread.thread_id)
        changed = first.model_copy(
            update={
                "selection": first.selection.model_copy(
                    update={"session": EnvdSessionConfiguration(working_directory="/new")}
                )
            }
        )
        candidate = original.model_copy(update={"environment_bindings": (changed, second)})
        other = OwnershipLocks(service._owners.directory)
        assert await other.acquire(f"{thread.thread_id}:device-z")
        try:
            with pytest.raises(EnvironmentError, match="Another App"):
                await service.admit(candidate)
            assert await app._store.envd_sessions.list(thread.thread_id) == before
        finally:
            await other.close()

        # A conflict on the final row also rolls back earlier writes inside the transaction.
        with pytest.raises(StoreConflictError):
            await app._store.envd_sessions.select_many(
                thread.thread_id,
                {
                    first.device.id: (
                        before[first.device.id],
                        before[first.device.id].model_copy(update={"version": 2}),
                    ),
                    second.device.id: (None, before[second.device.id]),
                },
            )
        assert await app._store.envd_sessions.list(thread.thread_id) == before

        # No process-local borrower exists: the durable nonterminal call still blocks replacement.
        pending = execution(thread_id=thread.thread_id, terminal=False)
        await app._store.tool_executions.save(pending)
        with pytest.raises(EnvironmentError, match="Retained tools"):
            await service.admit(candidate)
        assert await app._store.envd_sessions.list(thread.thread_id) == before
        await app._store.tool_executions.save(execution(thread_id=thread.thread_id))
        accepted = await service.admit(candidate)
        assert accepted.session_versions[first.device.id] == 2
        assert accepted.session_versions[second.device.id] == 1


@requires_envd
@pytest.mark.parametrize("close_completed", [False, True])
async def test_uncertain_retirement_retries_original_target_after_app_restart(tmp_path, monkeypatch, close_completed):
    from a13n_envd_client import EIPSession
    from a13n_harness_ui.devices import DeviceConnections

    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    path = _write_configuration(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    old_recipe = resource("http", endpoint)
    new_recipe = resource("http", endpoint.replace("127.0.0.1", "localhost"))
    (tmp_path / "devices").mkdir()
    (tmp_path / "devices/build.yaml").write_text(old_recipe.model_dump_json())
    events = []
    opened = []
    original_open = EIPDeviceConnection.open_session
    original_close = EIPSession.close
    original_acquire = DeviceConnections.acquire_device
    fail_close = True

    async def open_session(self, *args, **kwargs):
        events.append("open")
        result = await original_open(self, *args, **kwargs)
        opened.append(result.session_id)
        return result

    async def close(self):
        nonlocal fail_close
        events.append("close")
        if fail_close:
            fail_close = False
            if close_completed:
                await original_close(self)
            raise TimeoutError("uncertain close")
        return await original_close(self)

    async def acquire(self, recipe):
        events.append(recipe.transport.configuration.endpoint)
        return await original_acquire(self, recipe)

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    monkeypatch.setattr(EIPSession, "close", close)
    monkeypatch.setattr(DeviceConnections, "acquire_device", acquire)
    settings = _settings(tmp_path / "data")
    async with daemon(tmp_path, "http", endpoint=endpoint) as root:
        async with open_harness_ui_app(settings, configuration_path=path) as app:
            thread = await app.create_thread()
            executor = app._root_runs._executor
            service = executor._environments
            selection = EnvironmentBindingSelection(
                device_id=old_recipe.id,
                alias="build",
                working_directory=device_path(root),
                session=EnvdSessionConfiguration(working_directory=device_path(root)),
            )
            first = (
                await executor.capture(
                    thread_id=thread.thread_id,
                    prompt="capture",
                    environment=EnvironmentSelectionPatch(
                        environment_bindings=(selection,), default_environment="build"
                    ),
                )
            ).published.value
            mount, _ = await service._prepare_device_mount(first, first.environment_bindings[0])
            active = await mount.environment.open()
            await active.close()
            changed = first.environment_bindings[0].model_copy(update={"device": new_recipe})
            second = await service.admit(first.model_copy(update={"environment_bindings": (changed,)}))
            next_mount, _ = await service._prepare_device_mount(second, changed)
            with pytest.raises(EnvironmentProviderError) as failure:
                await next_mount.environment.open()
            assert isinstance(failure.value.__cause__, TimeoutError)
            head = await app._store.envd_sessions.get(thread.thread_id, old_recipe.id)
            assert head.version == 2 and head.session_version == 1
            assert head.session.status == "retiring"
            assert head.session_target == old_recipe
            assert len(opened) == 1

        events.clear()
        async with open_harness_ui_app(settings, configuration_path=path) as successor:
            service = successor._root_runs._executor._environments
            next_mount, _ = await service._prepare_device_mount(second, changed)
            active = await next_mount.environment.open()
            await active.close()
            assert len(opened) == 2 and opened[0] != opened[1]
            assert endpoint in events and events.index(endpoint) < events.index("open")
            if not close_completed:
                assert events.index("close") < events.index("open")
            head = await successor._store.envd_sessions.get(thread.thread_id, old_recipe.id)
            assert head.session_target == new_recipe
            assert head.session_version == 2 and head.session.status == "active"


@requires_envd
@pytest.mark.parametrize("carrier", ["http", "websocket"])
@pytest.mark.parametrize("close_completed", [False, True])
async def test_secret_rotation_preserves_exact_work_and_reconciles_retirement(
    tmp_path, monkeypatch, carrier, close_completed
):
    import asyncio

    from a13n_envd_client import EIPSession
    from a13n_harness.environment.envd_policy import EnvdEgressConfiguration

    from .test_device_runtime import app_listener
    from .test_tool_execution_retention import execution

    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    monkeypatch.setenv("SESSION_SECRET", "fixture-secret-a")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    recipe = resource(carrier, endpoint)
    opened = []
    original_open = EIPDeviceConnection.open_session
    original_close = EIPSession.close
    fail_close = False

    async def open_session(self, *, configuration, **kwargs):
        # Exercise real Host secret resolution and native Session lifetimes without
        # requiring privileged controlled-egress setup. Firewall behavior is tested by Envd.
        assert configuration.egress.secrets[0].value.startswith("fixture-secret-")
        result = await original_open(self, configuration=configuration.model_copy(update={"egress": None}), **kwargs)
        opened.append(result.session_id)
        return result

    async def close(self):
        nonlocal fail_close
        if fail_close:
            fail_close = False
            if close_completed:
                await original_close(self)
            raise TimeoutError("uncertain secret rotation")
        return await original_close(self)

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    monkeypatch.setattr(EIPSession, "close", close)
    async with app_listener(tmp_path, recipe) as (app, _, ws):
        async with daemon(tmp_path, carrier, endpoint=endpoint if carrier == "http" else ws) as root:
            thread = await app.create_thread()
            executor = app._root_runs._executor
            service = executor._environments
            selection = EnvironmentBindingSelection(
                device_id=recipe.id,
                alias="build",
                working_directory=device_path(root),
                session=EnvdSessionConfiguration(
                    working_directory=device_path(root),
                    egress=EnvdEgressConfiguration.model_validate(
                        {
                            "destinations": {"mode": "allowlist"},
                            "secrets": [
                                {
                                    "env": "API_KEY",
                                    "source": {"kind": "environment", "name": "SESSION_SECRET"},
                                    "inject_hosts": ["example.com"],
                                }
                            ],
                        }
                    ),
                ),
            )
            captured = (
                await executor.capture(
                    thread_id=thread.thread_id,
                    prompt="capture",
                    environment=EnvironmentSelectionPatch(
                        environment_bindings=(selection,), default_environment="build"
                    ),
                )
            ).published.value

            async def acquire(original_session_id=None):
                mount, _ = await service._prepare_device_mount(
                    captured, captured.environment_bindings[0], original_session_id=original_session_id
                )
                return await mount.environment.open()

            async def head():
                return await app._store.envd_sessions.get(thread.thread_id, recipe.id)

            active = await acquire()
            original = await head()
            original_id = original.session.descriptor.session_id
            try:
                monkeypatch.setenv("SESSION_SECRET", "fixture-secret-b")
                with pytest.raises(EnvironmentError) as busy:
                    await acquire()
                assert busy.value.code == "environment_busy"
                assert await head() == original
                exact = await acquire(original_id)
                await exact.close()
                assert await head() == original
            finally:
                await active.close()

            # Durable work also blocks rotation after the final in-process borrower exits.
            await app._store.tool_executions.save(execution(thread_id=thread.thread_id, terminal=False))
            with pytest.raises(EnvironmentError) as busy:
                await acquire()
            assert busy.value.code == "environment_busy"
            assert await head() == original
            monkeypatch.delenv("SESSION_SECRET")
            exact = await acquire(original_id)
            await exact.close()
            restored = await head()
            assert restored.session.descriptor.session_id == original_id
            assert restored.session.descriptor.owner_epoch > original.session.descriptor.owner_epoch
            assert restored.session.configuration_digest == original.session.configuration_digest
            assert len(opened) == 1
            await app._store.tool_executions.save(execution(thread_id=thread.thread_id))

            monkeypatch.setenv("SESSION_SECRET", "fixture-secret-b")
            first, second = await asyncio.gather(acquire(), acquire())
            try:
                rotated = await head()
                assert rotated.version == rotated.session_version == original.version
                assert rotated.session.descriptor.session_id != original_id
                assert rotated.session.configuration_digest != original.session.configuration_digest
                assert len(opened) == 2
            finally:
                await first.close()
                await second.close()
            repeated = await acquire()
            await repeated.close()
            assert len(opened) == 2

            # Even reverting the secret cannot revive an incarnation whose close was uncertain.
            monkeypatch.setenv("SESSION_SECRET", "fixture-secret-c")
            fail_close = True
            with pytest.raises(EnvironmentProviderError) as failed:
                await acquire()
            assert isinstance(failed.value.__cause__, TimeoutError)
            assert (await head()).session.status == "retiring"
            assert len(opened) == 2
            monkeypatch.setenv("SESSION_SECRET", "fixture-secret-b")
            retried = await acquire()
            await retried.close()
            current = await head()
            assert current.session.status == "active"
            assert len(set(opened)) == 3
            with pytest.raises(EnvironmentError) as lost:
                await acquire(original_id)
            assert lost.value.code == "environment_not_found"
            assert await head() == current
            assert len(opened) == 3
            assert "fixture-secret-" not in current.model_dump_json()
            assert "fixture-secret-" not in captured.model_dump_json()

            # An owned CodeAct runner is admitted before its first lazy mount access.
            # It must not block its own rotation, but other unfinished calls still do.
            import json

            from a13n_harness import AgentContext
            from a13n_harness_ui.model_runtime import HarnessUiModelResolver
            from a13n_harness_ui.surfaces import RootOperationStatus
            from pydantic_ai import RunContext
            from pydantic_ai.messages import ToolReturnPart
            from pydantic_ai.models.function import DeltaToolCall, FunctionModel

            from .test_tool_executions import install_slow_tool

            # Native file/shell tools are not CodeAct-eligible. Exercise an explicitly
            # opted-in integration tool using the same lazy BoundEnvironment API.
            async def slow(ctx: RunContext[AgentContext]):
                try:
                    return (
                        await ctx.deps.environment.files.read_text(
                            "/environment/build" + device_path(root / "rotation.txt")
                        )
                    ).text
                except EnvironmentError as error:
                    return {"code": error.code}

            install_slow_tool(monkeypatch, slow)
            (root / "rotation.txt").write_text("rotated through CodeAct")
            observed = []
            attempted = False

            async def model(messages, info):
                nonlocal attempted
                if not attempted:
                    attempted = True
                    monkeypatch.setenv("SESSION_SECRET", "fixture-secret-d")
                    if not observed:
                        await app._store.tool_executions.save(
                            execution("exec-other", thread_id=thread.thread_id, terminal=False)
                        )
                    yield {
                        0: DeltaToolCall(
                            name="run_code",
                            json_args=json.dumps({"code": "await slow()"}),
                            tool_call_id=f"rotation-{len(observed)}",
                        )
                    }
                else:
                    returns = [
                        part
                        for message in messages
                        if message.kind == "request"
                        for part in message.parts
                        if isinstance(part, ToolReturnPart)
                    ]
                    observed.append((str(returns[-1].content), await head()))
                    yield "Attempt completed."

            async def resolve(self, context, model_id):
                return FunctionModel(stream_function=model)

            monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
            for _ in range(2):
                attempted = False
                receipt = await app.submit_thread(
                    thread_id=thread.thread_id,
                    prompt="Read the remote file through CodeAct.",
                    environment=EnvironmentSelectionPatch(
                        environment_bindings=(selection,), default_environment="workspace"
                    ),
                )
                async with asyncio.timeout(30):
                    operation = await app.wait_root_operation(receipt.receipt_id)
                assert operation.status is RootOperationStatus.completed, operation
                assert operation.outcome.execution.output == "Attempt completed."
                await app._store.tool_executions.save(execution("exec-other", thread_id=thread.thread_id))
            assert "environment_busy" in observed[0][0], observed
            assert observed[0][1] == current
            assert "rotated through CodeAct" in observed[1][0], observed
            assert len(set(opened)) == 4
            assert (await head()).version == current.version
            records = await app._store.tool_executions.list(thread.thread_id, offset=0, limit=10)
            assert len([record for record in records if record.tool_name == "run_code"]) == 2
