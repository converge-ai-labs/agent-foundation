from __future__ import annotations

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import (
    NewThreadDefaults,
    RootOperationStatus,
    RunModelOverrides,
    ThreadConfigurationMutationInput,
    ThreadConfigurationPatch,
)
from pydantic_ai.models.function import FunctionModel

from .test_app import _settings
from .test_thread_collaboration import configuration, controller

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("overrides", [None, RunModelOverrides(thinking="high"), RunModelOverrides(fast=False)])
async def test_default_model_override_patch_clear_and_restart(tmp_path, monkeypatch, overrides):
    root = configuration(tmp_path)

    async def stream(messages, info):
        yield "Completed"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread(defaults=NewThreadDefaults(default_model_id="model-secondary"))
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Saved default", model_overrides=overrides)
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert (await app.inspect_operation_configuration(receipt.receipt_id)).agent.model_id == "model-secondary"
        # A patch to another axis retains the Model and advances the same CAS head.
        changed = await app.patch_thread_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutationInput(
                expected_version=1, patch=ThreadConfigurationPatch(project_id=None)
            ),
        )
        assert changed.configuration.default_model_id == "model-secondary"
        with pytest.raises(ThreadError, match="version changed"):
            await app.patch_thread_configuration(
                thread_id=thread.thread_id,
                mutation=ThreadConfigurationMutationInput(
                    expected_version=1, patch=ThreadConfigurationPatch(default_model_id=None)
                ),
            )
        # Ordinary generic creation inherits a sticky default without Sidekick.
        copied = await controller(app).create_thread(
            source_thread_id=thread.thread_id,
            prompt="Copy selections",
            title=None,
            agent_id=None,
        )
        assert (await app.wait_root_operation(copied["receipt"]["receipt_id"])).status is RootOperationStatus.completed
        assert (await app.get_thread(copied["thread_id"])).thread.configuration.default_model_id == "model-secondary"
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        assert (await app.get_thread(thread.thread_id)).thread.configuration.default_model_id == "model-secondary"
        cleared = await app.patch_thread_configuration(
            thread_id=thread.thread_id,
            mutation=ThreadConfigurationMutationInput(
                expected_version=2, patch=ThreadConfigurationPatch(default_model_id=None)
            ),
        )
        assert cleared.configuration.default_model_id is None
        inspection = await app.inspect_thread_configuration(thread.thread_id)
        assert inspection.next_model_id == "model-primary"
        assert inspection.captured.agent.model_id == "model-secondary"
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Follow Agent")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert (await app.inspect_operation_configuration(receipt.receipt_id)).agent.model_id == "model-primary"


async def test_missing_default_model_rejects_creation_and_patch_without_fallback(tmp_path):
    root = configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        with pytest.raises(ThreadError, match="Model"):
            await app.create_thread(defaults=NewThreadDefaults(default_model_id="model-missing"))
        assert (await app.list_threads()).total == 0
        thread = await app.create_thread()
        with pytest.raises(ThreadError, match="Model"):
            await app.patch_thread_configuration(
                thread_id=thread.thread_id,
                mutation=ThreadConfigurationMutationInput(
                    expected_version=1, patch=ThreadConfigurationPatch(default_model_id="model-missing")
                ),
            )
        saved = (await app.get_thread(thread.thread_id)).thread.configuration
        assert saved.version == 1 and saved.default_model_id is None
