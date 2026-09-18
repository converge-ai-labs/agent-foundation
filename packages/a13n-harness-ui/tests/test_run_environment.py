"""Run selection and continuation tests; native Sandbox isolation is tested separately."""

from __future__ import annotations

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import ResolvedRunComposition
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
                thread_id=thread.thread_id, prompt="Continue", environment_profile_id=override
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
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Run", environment_profile_id=profile)
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.failed
        assert prepared == ([] if profile == "missing-profile" else ["environment-sandbox"])
        assert (await app.get_thread(thread.thread_id)).thread.configuration == thread.configuration
        if profile == "missing-profile":
            assert outcome.failure is not None and outcome.failure.code == "environment_profile_missing"


@pytest.mark.parametrize("patch", [None, {"project_id": None}, {"environment_profile_id": "environment-native"}])
async def test_deferred_response_retains_run_environment_unless_explicitly_changed(tmp_path, monkeypatch, patch):
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        app._root_runs._executor._agents = _DeferredReconstructor()
        thread = await app.create_thread()
        captures = await _stub_sandbox_runtime(app, thread.thread_id, monkeypatch)
        receipt = await app.submit_thread(
            thread_id=thread.thread_id, prompt="Ask", environment_profile_id="environment-sandbox"
        )
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.suspended
        detail = await app.get_thread(thread.thread_id)
        assert detail.continuation_id is not None
        responses = (ExternalToolResult(request_id=detail.deferred_requests[0].request_id, result="Answer"),)
        stale = await app.respond_thread(
            thread_id=thread.thread_id,
            response=ThreadDeferredResponse(expected_continuation_id="0" * 64, responses=responses),
        )
        assert (await app.wait_root_operation(stale.receipt_id)).status is RootOperationStatus.failed
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
        updated = (await app.get_thread(thread.thread_id)).thread.configuration
        assert updated.environment_profile_id == "environment-native"
        assert updated.version == thread.configuration.version + (patch is not None)
        next_run = await app.submit_thread(thread_id=thread.thread_id, prompt="New task")
        assert (await app.wait_root_operation(next_run.receipt_id)).status is RootOperationStatus.completed
        assert captures[-1].environment_profile.profile_id == "environment-native"


async def test_automatic_interaction_timeout_retains_run_environment(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "tools:\n  interaction_timeout_seconds: 120\n")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _DeferredReconstructor()
        thread = await app.create_thread()
        captures = await _stub_sandbox_runtime(app, thread.thread_id, monkeypatch)
        receipt = await app.submit_thread(
            thread_id=thread.thread_id, prompt="Ask", environment_profile_id="environment-sandbox"
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
