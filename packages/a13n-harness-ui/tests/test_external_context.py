"""Trusted external business data has a different lifetime from Harness history."""

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.external_context import ExternalContext
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import RootOperationStatus
from pydantic_ai.models.function import FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


async def test_root_context_survives_restart_history_clear_and_explicit_replacement(tmp_path, monkeypatch):
    path = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")
    captured = []

    async def model(messages, info):
        assert "private-tenant" not in str(messages)
        yield "Done"

    async def resolve(self, context, model_id):
        captured.append((dict(context.deps.metadata), dict(context.deps.request_context.headers)))
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        thread = await app.create_thread()
        supplied = ExternalContext(metadata={"tenant": "private-tenant"}, headers={"X-Tenant": "private-tenant"})
        await app.set_thread_external_context(thread_id=thread.thread_id, context=supplied)
        supplied.metadata["tenant"] = "caller-mutation"
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="First")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        selected = await app.get_thread(thread.thread_id)
        await app.clear_thread_context(thread_id=thread.thread_id, expected_continuation_id=selected.continuation_id)
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="After restart")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        await app.set_thread_external_context(thread_id=thread.thread_id, context=ExternalContext(metadata={"new": 2}))
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Replace")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        await app.set_thread_external_context(thread_id=thread.thread_id, context=ExternalContext())
        for prompt in ("Clear", "Omit"):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
    assert captured == [
        ({"tenant": "private-tenant"}, {"x-tenant": "private-tenant"}),
        ({"tenant": "private-tenant"}, {"x-tenant": "private-tenant"}),
        ({"new": 2}, {}),
        ({}, {}),
        ({}, {}),
    ]
