"""Run selection and continuation tests; native Sandbox isolation is tested separately."""

from __future__ import annotations

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import ResolvedRunComposition
from a13n_harness_ui.environment_bindings import EnvironmentSelectionPatch
from a13n_harness_ui.errors import CompositionError, RunCoordinationError
from a13n_harness_ui.root_execution import _selection
from a13n_harness_ui.storage import StoredContinuation, ThreadConfigurationMutation, ThreadConfigurationPatch
from a13n_harness_ui.surfaces import ExternalToolResult, NewThreadDefaults, RootOperationStatus, ThreadDeferredResponse

from .test_app import _CompletedReconstructor, _DeferredReconstructor, _settings, _write_configuration

pytestmark = pytest.mark.anyio


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
            checkpoint = await app._store.objects.read_model(stored.continuation, StoredContinuation)
            saved = await app._store.objects.read_model(checkpoint.run_composition, ResolvedRunComposition)
            assert saved.environment_profile.profile_id == expected


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

    from a13n_environment import EnvironmentConnector
    from a13n_environment.direct_local.provider import DIRECT_LOCAL
    from a13n_environment.errors import (
        EnvironmentManagementCancelled,
        EnvironmentManagementError,
        EnvironmentProviderErrorCategory,
        provider_error,
    )
    from a13n_environment.models import EnvironmentError, EnvironmentState
    from a13n_harness import RunBindings
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
        reconstructed = SimpleNamespace(adapter=SimpleNamespace(preserves_host_paths=False))
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
                raise EnvironmentManagementCancelled(observed, "op-create")
            if failure == "management_error":
                error = provider_error(
                    "test_managed", "provider_unknown_outcome", EnvironmentProviderErrorCategory.UNKNOWN_OUTCOME
                )
                raise EnvironmentManagementError(error, observed, "op-create", "env-test")
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
                ):
                    pytest.fail("Failed execution must not be published")
        else:
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else EnvironmentManagementError):
                await service.prepare(composition)
            connector.open.assert_not_awaited()
        head = await app._store.environment_states.get(key)
        assert head is not None and head.state is not None
        saved = await app._store.objects.read_model(head.state, StoredEnvironmentState)
        assert saved.state == observed
