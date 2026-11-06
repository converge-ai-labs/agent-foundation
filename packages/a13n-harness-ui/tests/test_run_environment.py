"""Run selection and continuation tests; native Sandbox isolation is tested separately."""

from __future__ import annotations

import os

import pytest
from a13n_harness.capabilities.browser import BrowserUseCapability
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.browser import Binding
from a13n_harness_ui.composition import ResolvedRunComposition
from a13n_harness_ui.environment_bindings import EnvironmentSelectionPatch
from a13n_harness_ui.errors import CompositionError, RunCoordinationError
from a13n_harness_ui.root_execution import _selection
from a13n_harness_ui.storage import ThreadConfigurationMutation, ThreadConfigurationPatch
from a13n_harness_ui.storage.continuations import read_continuation
from a13n_harness_ui.surfaces import ExternalToolResult, NewThreadDefaults, RootOperationStatus, ThreadDeferredResponse

from .test_app import _CompletedReconstructor, _DeferredReconstructor, _settings, _write_configuration
from .test_device_runtime import requires_envd

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def _stub_sandbox_runtime(app, thread_id, monkeypatch):
    executor = app._root_runs._executor
    native = await executor._compositions.publish(
        await app.current_configuration(), _selection(await app._threads.get(thread_id))
    )
    prepare = executor._environments.prepare
    captures = []

    async def prepare_without_isolation(composition):
        captures.append(composition)
        # Exercise real admission, capture, checkpointing and resumption without
        # requiring an envd binary. Only runtime entry uses the native fixture.
        return await prepare(composition.model_copy(update={"environment_profile": native.value.environment_profile}))

    monkeypatch.setattr(executor._environments, "prepare", prepare_without_isolation)
    return captures


@pytest.mark.parametrize("projectless", [False, True])
async def test_run_environment_override_is_captured_without_changing_thread(tmp_path, monkeypatch, projectless):
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread(defaults=NewThreadDefaults(project_id=None) if projectless else None)
        captures = await _stub_sandbox_runtime(app, thread.thread_id, monkeypatch)
        for override, expected in [("environment-sandbox", "environment-sandbox"), (None, "environment-native")]:
            receipt = await app.submit_thread(
                thread_id=thread.thread_id,
                prompt="Continue",
                environment=None if override is None else EnvironmentSelectionPatch(environment_profile_id=override),
            )
            outcome = await app.wait_root_operation(receipt.receipt_id)
            assert outcome.status is RootOperationStatus.completed
            assert captures[-1].environment_profile.profile_id == expected
            assert captures[-1].thread_configuration_version == thread.configuration.version
            assert (await app.get_thread(thread.thread_id)).thread.configuration == thread.configuration
            stored = await app._threads.get(thread.thread_id)
            assert stored.continuation is not None
            checkpoint = await read_continuation(app._store.objects, stored.continuation, include_display=True)
            saved = await app._store.objects.read_model(checkpoint.run_composition, ResolvedRunComposition)
            assert saved.environment_profile.profile_id == expected


@pytest.fixture
def browser_runtime(monkeypatch):
    from unittest.mock import AsyncMock, Mock

    from a13n_harness.capabilities.browser.cdp import Chromium
    from a13n_harness_ui.browser import HostBrowsers

    runtime = Chromium(Mock())
    runtime.close = AsyncMock()

    async def launch(host):
        host.runtime = runtime

    monkeypatch.setattr(HostBrowsers, "launch", launch)
    return runtime


async def test_sandbox_run_does_not_inherit_host_browser(tmp_path, monkeypatch, browser_runtime):
    capabilities = []

    class Reconstructor(_CompletedReconstructor):
        def reconstruct(self, composition, *, root_capabilities=(), **kwargs):
            capabilities.append(tuple(cap for cap in root_capabilities if isinstance(cap, BrowserUseCapability)))
            return super().reconstruct(composition, root_capabilities=root_capabilities, **kwargs)

    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=_write_configuration(tmp_path),
        host_mode="webui",
        share_computer=True,
    ) as app:
        app._root_runs._executor._agents = Reconstructor()
        thread = await app.create_thread()
        host = app.browsers()
        await host.prepare()
        binding = await host.admit(thread.thread_id)
        assert binding is not None
        captures = await _stub_sandbox_runtime(app, thread.thread_id, monkeypatch)
        for profile, expected_browser in [("environment-sandbox", False), (None, True)]:
            receipt = await app.submit_thread(
                thread_id=thread.thread_id,
                prompt="Continue",
                environment=None if profile is None else EnvironmentSelectionPatch(environment_profile_id=profile),
            )
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
            assert captures[-1].environment_profile.profile_id == (profile or "environment-native")
            assert bool(capabilities[-1]) is expected_browser
            assert (await app.get_thread(thread.thread_id)).thread.configuration == thread.configuration
            assert host.binding(thread.thread_id) == binding


@pytest.mark.parametrize("profile", ["missing-profile", "environment-sandbox"])
async def test_invalid_or_unavailable_run_environment_never_falls_back(tmp_path, monkeypatch, profile):
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread = await app.create_thread()
        prepared = []

        async def unavailable(composition):
            prepared.append(composition.environment_profile.profile_id)
            raise RuntimeError("Sandbox unavailable")

        monkeypatch.setattr(app._root_runs._executor._environments, "prepare", unavailable)
        if profile == "missing-profile":
            with pytest.raises(CompositionError) as error:
                await app.submit_thread(
                    thread_id=thread.thread_id,
                    prompt="Run",
                    environment=EnvironmentSelectionPatch(environment_profile_id=profile),
                )
            assert error.value.code == "environment_profile_missing"
            assert prepared == []
        else:
            receipt = await app.submit_thread(
                thread_id=thread.thread_id,
                prompt="Run",
                environment=EnvironmentSelectionPatch(environment_profile_id=profile),
            )
            outcome = await app.wait_root_operation(receipt.receipt_id)
            assert outcome.status is RootOperationStatus.failed
            assert prepared == ["environment-sandbox"]
        assert (await app.get_thread(thread.thread_id)).thread.configuration == thread.configuration


@pytest.mark.parametrize(
    "patch", [None, {"project_id": None}, {"environment_profile_id": "environment-native"}, {"local_roots": ()}]
)
async def test_deferred_response_retains_run_environment_unless_explicitly_changed(tmp_path, monkeypatch, patch):
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        app._root_runs._executor._agents = _DeferredReconstructor()
        thread = await app.create_thread()
        captures = await _stub_sandbox_runtime(app, thread.thread_id, monkeypatch)
        override_root = tmp_path / "run-only"
        override_root.mkdir()
        receipt = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt="Ask",
            environment=EnvironmentSelectionPatch(
                environment_profile_id="environment-sandbox", local_roots=(str(override_root),)
            ),
        )
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.suspended
        detail = await app.get_thread(thread.thread_id)
        assert detail.continuation_id is not None
        responses = (ExternalToolResult(request_id=detail.deferred_requests[0].request_id, result="Answer"),)
        with pytest.raises(RunCoordinationError) as error:
            await app.respond_thread(
                thread_id=thread.thread_id,
                response=ThreadDeferredResponse(expected_continuation_id="0" * 64, responses=responses),
            )
        assert error.value.code == "thread_continuation_conflict"
        assert len(captures) == 1
        resumed = await app.respond_thread(
            thread_id=thread.thread_id,
            response=ThreadDeferredResponse(expected_continuation_id=detail.continuation_id, responses=responses),
            mutation=(
                None
                if patch is None
                else ThreadConfigurationMutation(
                    expected_version=thread.configuration.version, patch=ThreadConfigurationPatch(**patch)
                )
            ),
        )
        assert (await app.wait_root_operation(resumed.receipt_id)).status is RootOperationStatus.completed
        expected = "environment-native" if patch and "environment_profile_id" in patch else "environment-sandbox"
        assert [capture.environment_profile.profile_id for capture in captures] == ["environment-sandbox", expected]
        assert captures[0].project_roots == (str(override_root),)
        assert captures[1].project_roots == (() if patch and "local_roots" in patch else (str(override_root),))
        updated = (await app.get_thread(thread.thread_id)).thread.configuration
        assert updated.environment_profile_id == "environment-native"
        assert updated.version == thread.configuration.version + (patch is not None)
        next_run = await app.submit_thread(thread_id=thread.thread_id, prompt="New task")
        assert (await app.wait_root_operation(next_run.receipt_id)).status is RootOperationStatus.completed
        assert captures[-1].environment_profile.profile_id == "environment-native"
        assert captures[-1].project_roots == updated.local_roots


async def test_automatic_interaction_timeout_retains_run_environment(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "tools:\n  interaction_timeout_seconds: 120\n")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _DeferredReconstructor()
        thread = await app.create_thread()
        captures = await _stub_sandbox_runtime(app, thread.thread_id, monkeypatch)
        override_root = tmp_path / "run-only"
        override_root.mkdir()
        receipt = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt="Ask",
            environment=EnvironmentSelectionPatch(
                environment_profile_id="environment-sandbox", local_roots=(str(override_root),)
            ),
        )
        await app.wait_root_operation(receipt.receipt_id)
        pending = app._root_runs._interaction_waits[thread.thread_id]
        with monkeypatch.context() as clock:
            clock.setattr("a13n_harness_ui.root_run.monotonic", lambda: pending.deadline + 1)
            await app._root_runs._expire_interaction(thread.thread_id, pending)
        latest = await app._root_runs.active(thread.thread_id) or await app._root_runs.latest(thread.thread_id)
        assert latest is not None and latest.receipt.receipt_id != receipt.receipt_id
        assert (await app.wait_root_operation(latest.receipt.receipt_id)).status is RootOperationStatus.completed
        assert [capture.environment_profile.profile_id for capture in captures] == ["environment-sandbox"] * 2
        assert (await app.get_thread(thread.thread_id)).thread.configuration == thread.configuration


@pytest.mark.parametrize("failure", ["management_error", "cancel", "execution_error"])
async def test_management_state_is_published_before_execution_and_after_partial_failure(tmp_path, monkeypatch, failure):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from a13n_harness import RunBindings
    from a13n_harness.environment.execution import EnvironmentConnector
    from a13n_harness.environment.models import EnvironmentError, EnvironmentState
    from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
    from a13n_harness_ui.extensions.environment_adapters import EnvironmentBindingCancelled, EnvironmentBindingError
    from a13n_harness_ui.storage import EnvironmentBindingKey, StoredEnvironmentState

    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        thread = await app.create_thread()
        executor = app._root_runs._executor
        captured = await executor._compositions.publish(
            await app.current_configuration(), _selection(await app._threads.get(thread.thread_id))
        )
        composition = captured.value
        profile = composition.environment_profile.model_copy(update={"provider_key": "test_managed"})
        composition = composition.model_copy(update={"environment_profile": profile})
        service = executor._environments
        observed = EnvironmentState(provider_key="test_managed", state_version="1", state={"target_id": "allocated"})
        key = EnvironmentBindingKey(
            thread_id=thread.thread_id,
            environment_profile_id=profile.profile_id,
            profile_digest=profile.behavior_digest,
            adapter_key=profile.adapter_key,
            normalized_root=str(composition.project_roots[0]),
        )
        reconstructed = SimpleNamespace(profile=profile, adapter=SimpleNamespace(preserves_host_paths=False))
        monkeypatch.setattr(service._reconstructor, "reconstruct", lambda profile: reconstructed)
        connector = Mock(
            spec=EnvironmentConnector,
            state=observed,
            provider_key="test_managed",
            environment_id="env-test",
            descriptor=DIRECT_LOCAL.execution_connector({"root": {"path": str(tmp_path)}}).descriptor,
            open=AsyncMock(side_effect=EnvironmentError("Target unavailable", code="environment_unavailable")),
        )

        async def bind(*args, **kwargs):
            if failure == "cancel":
                raise EnvironmentBindingCancelled(observed)
            if failure == "management_error":
                raise EnvironmentBindingError("Target creation failed", observed)
            return connector

        monkeypatch.setattr(service._reconstructor, "bind", bind)
        if failure == "execution_error":
            plan = await service.prepare(composition)
            connector.open.assert_not_awaited()
            with pytest.raises(EnvironmentError):
                async with plan.runtime.bind(
                    thread_id=thread.thread_id,
                    run_id="run-test",
                    instance=RunBindings.embedded().instance,
                    host_refs={},
                ) as environment:
                    await environment.files.stat("/workspace/value.txt")
        else:
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else EnvironmentBindingError):
                await service.prepare(composition)
            connector.open.assert_not_awaited()
        head = await app._store.environment_states.get(key)
        assert head is not None and head.state is not None
        saved = await app._store.objects.read_model(head.state, StoredEnvironmentState)
        assert saved.state == observed


@pytest.mark.parametrize("sandbox_default", [False, True])
async def test_captured_full_control_browser_survives_next_run_default_changes(
    tmp_path, sandbox_default, browser_runtime
):
    import asyncio
    from unittest.mock import AsyncMock

    from a13n_harness.capabilities.browser.cdp import Page
    from a13n_harness.capabilities.browser.scope import CdpBrowserBackend

    from .test_app import _reconstructed

    entered, proceed = asyncio.Event(), asyncio.Event()
    used = []

    class Reconstructor:
        def reconstruct(self, composition, *, root_capabilities=(), **kwargs):
            capability = next(cap for cap in root_capabilities if isinstance(cap, BrowserUseCapability))

            async def model(messages, info):
                used.append(await capability.backend.tabs("list"))
                entered.set()
                await proceed.wait()
                used.append(await capability.backend.tabs("list"))
                yield "done"

            return _reconstructed(model, root_capabilities)

    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=_write_configuration(tmp_path),
        host_mode="webui",
        share_computer=True,
    ) as app:
        app._root_runs._executor._agents = Reconstructor()
        thread = await app.create_thread(
            defaults=NewThreadDefaults(
                environment_profile_id="environment-sandbox" if sandbox_default else "environment-native"
            )
        )
        host = app.browsers()
        runtime = browser_runtime
        page = Page("tab", "session")
        runtime.pages[page.target] = page
        runtime.send = AsyncMock(return_value={"targetInfos": [{"type": "page", "targetId": "tab"}]})
        host.bindings[thread.thread_id] = Binding(status="ready", scope=CdpBrowserBackend(runtime, page))
        receipt = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt="Use browser",
            environment=EnvironmentSelectionPatch(environment_profile_id="environment-native"),
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await app.update_thread_configuration(
                thread_id=thread.thread_id,
                mutation=ThreadConfigurationMutation(
                    expected_version=thread.configuration.version,
                    patch=ThreadConfigurationPatch(environment_profile_id="environment-sandbox"),
                ),
            )
        finally:
            proceed.set()
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert len(used) == 2 and used[0][0].target == used[1][0].target == "tab"


@pytest.mark.parametrize("alias", ["workspace-2", "thread-files"])
@pytest.mark.parametrize("profile", ["environment-native", pytest.param("environment-envd", marks=requires_envd)])
async def test_recovery_prepares_only_captured_target_without_unrelated_roots(tmp_path, monkeypatch, alias, profile):
    from a13n_harness import AgentIdentityRef, AgentInstanceContext
    from a13n_harness.environment.models import EnvironmentAction

    path = _write_configuration(tmp_path)
    if profile == "environment-envd":
        path.write_text(path.read_text() + f"envd:\n  executable: {os.environ['A13N_ENVD_TEST_BINARY']}\n")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread(defaults=NewThreadDefaults(environment_profile_id=profile))
        executor = app._root_runs._executor
        published = await executor._compositions.publish(
            await app.current_configuration(), _selection(await app._threads.get(thread.thread_id))
        )
        target = tmp_path / "retained"
        target.mkdir()
        (target / "evidence.txt").write_text("original target")
        thread_root = await executor._environments._thread_files.touch(thread.thread_id)
        (thread_root / "evidence.txt").write_text("original target")
        composition = published.value.model_copy(
            update={"project_roots": (str(tmp_path / "removed-root"), str(target))}
        )

        async def unrelated(*args, **kwargs):
            pytest.fail("Recovery must not prepare unrelated mounts or Run extensions")

        monkeypatch.setattr(executor._environments._reconstructor, "create_extensions", unrelated)
        monkeypatch.setattr(executor._environments, "_prepare_host_files_mount", unrelated)
        runtime = await executor._environments.prepare_recovery(composition, alias)
        instance = AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject=thread.thread_id), agent_instance_id="agent-recovery"
        )
        async with runtime.bind(
            thread_id=thread.thread_id, run_id="run-recovery", instance=instance, host_refs={}
        ) as bound:
            await runtime._activate()
            assert tuple(item.name for item in bound.snapshot.mounts) == (alias,)
            assert EnvironmentAction.SHELL_EXEC in bound.snapshot.mounts[0].permission_ceiling.operations
            assert (await bound.files.read_text("evidence.txt")).text == "original target"


@pytest.mark.parametrize("remove_management", [False, True])
async def test_management_publication_preserves_session_and_rejects_stale_head(tmp_path, remove_management):
    from datetime import UTC, datetime

    from a13n_envd_client.eip.v1 import EIPLimits, ExecutionFeatures, SessionDescriptor
    from a13n_harness.environment.models import EnvironmentState
    from a13n_harness.environment.session_recovery import EnvdSessionState
    from a13n_harness_ui.errors import StoreConflictError
    from a13n_harness_ui.storage import EnvironmentBindingKey, StoredEnvironmentState
    from a13n_harness_ui.storage.objects import ObjectKind

    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        thread = await app.create_thread()
        environments = app._root_runs._executor._environments
        key = EnvironmentBindingKey(
            thread_id=thread.thread_id,
            environment_profile_id="environment-native",
            profile_digest="a" * 64,
            adapter_key="native",
            normalized_root=str(tmp_path),
        )
        session = EnvdSessionState(
            target="fixture",
            descriptor=SessionDescriptor.model_validate(
                {
                    "device_id": "fixture-device",
                    "generation": 1,
                    "session_id": "fixture-session",
                    "path_style": "posix",
                    "system_info": {"os": "linux"},
                    "working_directory": "/work",
                    "owner_epoch": 7,
                    "limits": dict.fromkeys(EIPLimits.model_fields, 1024),
                    "execution_features": dict.fromkeys(ExecutionFeatures.model_fields, False),
                    "lifecycle": {"idle_timeout_ms": 3600000},
                    "boundary": {
                        "sandbox": {"mode": "disabled"},
                        "egress": "inherit",
                        "privilege_gain_blocked": False,
                        "backend": "native",
                        "policy_digest": "b" * 64,
                    },
                }
            ),
        )
        original = EnvironmentState(provider_key="direct_local", state_version="1", state={"version": 1})
        initial = (
            await app._store.objects.publish_model(
                object_kind=ObjectKind.environment_state,
                value=StoredEnvironmentState(
                    binding=key, state=original, envd_session=session, created_at=datetime.now(UTC)
                ),
            )
        ).ref
        await app._store.environment_states.select(key=key, expected=None, replacement=initial)
        observed = None if remove_management else original.model_copy(update={"state": {"version": 2}})
        publication = await environments._publish_state(key, initial, original, observed)
        saved = await app._store.objects.read_model(publication.replacement, StoredEnvironmentState)
        assert saved.envd_session == session
        assert saved.state == observed
        with pytest.raises(StoreConflictError):
            await environments._publish_state(key, initial, original, observed)
        assert (await app._store.environment_states.get(key)).state == publication.replacement
        recovered = await environments._session_context(key)
        assert recovered.ownership is not None
        async with recovered.ownership() as saved:
            assert saved == session
        import gc
        import weakref

        reference = weakref.ref(recovered)
        del recovered
        gc.collect()
        assert reference() is None
        assert not environments._sessions and not environments._owners._held
        recovered = await environments._session_context(key)
        async with recovered.ownership() as saved:
            assert saved == session


@pytest.mark.parametrize("blocked", ["startup", "control_lock", "lifecycle"])
async def test_run_completes_without_waiting_for_browser(tmp_path, monkeypatch, browser_runtime, blocked):
    import asyncio

    from a13n_harness_ui.browser import HostBrowsers
    from a13n_harness_ui.browser_context import BrowserContextCapability

    entered, release = asyncio.Event(), asyncio.Event()
    captured = []

    async def launch(host):
        # A connected CDP runtime is not ready until initialization finishes.
        host.runtime = browser_runtime
        entered.set()
        await release.wait()

    class Reconstructor(_CompletedReconstructor):
        def reconstruct(self, composition, *, root_capabilities=(), **kwargs):
            captured.append(root_capabilities)
            return super().reconstruct(composition, root_capabilities=root_capabilities, **kwargs)

    if blocked == "startup":
        monkeypatch.setattr(HostBrowsers, "launch", launch)
    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=_write_configuration(tmp_path),
        host_mode="webui",
        share_computer=True,
    ) as app:
        app._root_runs._executor._agents = Reconstructor()
        thread = await app.create_thread()
        host = app.browsers()
        lock = None
        try:
            if blocked == "startup":
                await asyncio.wait_for(entered.wait(), 5)
                assert host.runtime_state().status == "starting"
            else:
                await host.prepare()
                lock = host.control_lock if blocked == "control_lock" else host.lifecycle
                await lock.acquire()
            async with asyncio.timeout(5):
                receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Continue without waiting")
                assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
            context = next(cap for cap in captured[-1] if isinstance(cap, BrowserContextCapability))
            tools = [cap for cap in captured[-1] if isinstance(cap, BrowserUseCapability)]
            assert bool(tools) is (blocked != "startup")
            if blocked == "startup":
                assert context.backend is None and "starting" in context.unavailable
                assert context.get_toolset() is None
                release.set()
                await host.prepare()
                assert host.runtime_state().status == "ready"
                assert context.backend is None  # Recovery cannot mutate an admitted Run.
                receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Try the next Run")
                assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
                assert any(isinstance(cap, BrowserUseCapability) for cap in captured[-1])
        finally:
            release.set()
            if lock is not None:
                lock.release()
