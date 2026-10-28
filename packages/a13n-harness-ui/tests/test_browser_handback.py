"""Handback registration and event-driven delivery, without model polling."""

import pytest
from a13n_harness_ui.browser_handback import BLOCKED, HANDBACK, BrowserHandback
from anyio import Event, create_task_group, fail_after, wait_all_tasks_blocked

pytestmark = pytest.mark.anyio


async def test_only_blocked_threads_are_registered_and_repeated_takeovers_coalesce():
    notices = BrowserHandback()
    delivered = []

    async def deliver(thread, prompt, current):
        assert current()
        delivered.append((thread, prompt))
        return True

    notices.deliver = deliver
    async with create_task_group() as group:
        notices.start(group)
        notices.takeover()
        assert notices.blocked("thread-b") == BLOCKED
        notices.blocked("thread-b")
        notices.blocked("thread-c")
        notices.user_input("thread-b")
        await wait_all_tasks_blocked()
        assert not delivered
        notices.released()
        notices.takeover()  # Retake before delivery; no stale availability input.
        await wait_all_tasks_blocked()
        assert not delivered
        notices.released()
        await wait_all_tasks_blocked()
        assert sorted(delivered) == [("thread-b", HANDBACK), ("thread-c", HANDBACK)]
        notices.released()
        await wait_all_tasks_blocked()
        assert len(delivered) == 2
        notices.stop()


async def test_failed_admission_waits_for_lifecycle_retry_not_a_timer():
    notices = BrowserHandback()
    attempts = []
    ready = False

    async def deliver(thread, prompt, current):
        attempts.append(thread)
        return ready and current()

    notices.deliver = deliver
    async with create_task_group() as group:
        notices.start(group)
        notices.takeover()
        notices.blocked("thread-b")
        notices.released()
        await wait_all_tasks_blocked()
        assert attempts == ["thread-b"]
        ready = True
        notices.retry("thread-b")
        await wait_all_tasks_blocked()
        assert attempts == ["thread-b", "thread-b"]
        notices.retry("thread-b")
        await wait_all_tasks_blocked()
        assert len(attempts) == 2
        notices.stop()


async def test_explicit_stop_cancels_delivery_and_new_input_does_not_restore_old_notice():
    notices = BrowserHandback()
    entered = Event()
    calls = []

    async def deliver(thread, prompt, current):
        calls.append(current)
        entered.set()
        await Event().wait()
        return True

    notices.deliver = deliver
    async with create_task_group() as group:
        notices.start(group)
        notices.takeover()
        notices.blocked("thread-b")
        notices.released()
        with fail_after(2):
            await entered.wait()
        notices.suppress("thread-b")
        assert not calls[0]()
        assert "suppressed" in notices.blocked("thread-b")
        notices.user_input("thread-b")
        await wait_all_tasks_blocked()
        assert len(calls) == 1
        notices.stop()


async def test_new_episode_block_during_delivery_survives_prior_acceptance():
    notices = BrowserHandback()
    calls = []

    async def deliver(thread, prompt, current):
        calls.append(current)
        if len(calls) == 1:
            notices.takeover()
            notices.blocked(thread)
            notices.released()
            assert not current()
        return True

    notices.deliver = deliver
    async with create_task_group() as group:
        notices.start(group)
        notices.takeover()
        notices.blocked("thread-b")
        notices.released()
        await wait_all_tasks_blocked()
        assert len(calls) == 2
        notices.stop()


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("goal", [False, True])
async def test_app_handback_steers_active_or_wakes_idle_without_resuming_goal(tmp_path, monkeypatch, active, goal):
    from functools import partial
    from unittest.mock import AsyncMock

    from a13n_harness.capabilities import BrowserUseCapability
    from a13n_harness.capabilities.browser import BrowserBackend, BrowserHumanControl, BrowserObservation
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.composition import AgentReconstructor
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from .test_app import _settings, _write_configuration

    path = _write_configuration(tmp_path)
    waiting, proceed, delivered, resumed = Event(), Event(), Event(), Event()
    backend = AsyncMock(spec=BrowserBackend)
    backend.observe.side_effect = BrowserHumanControl("Human takeover")
    stage = 0
    original = AgentReconstructor.reconstruct

    def reconstruct(self, composition, **kwargs):
        capabilities = kwargs.pop("root_capabilities", ())
        return original(
            self,
            composition,
            root_capabilities=(
                *capabilities,
                BrowserUseCapability(backend, on_human_control=partial(notices.blocked, thread.thread_id)),
            ),
            **kwargs,
        )

    async def model(messages, info):
        nonlocal stage
        current = stage
        stage += 1
        if current == 0:
            yield {0: DeltaToolCall(name="browser_observe", json_args="{}", tool_call_id="blocked")}
        elif current == 1:
            waiting.set()
            if active:
                await proceed.wait()
            yield "Waiting for handback." + ("\n[GOAL_BLOCKED]" if goal else "")
        elif current == 2:
            yield {0: DeltaToolCall(name="browser_observe", json_args="{}", tool_call_id="fresh")}
        else:
            resumed.set()
            yield "Browser work continued."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(AgentReconstructor, "reconstruct", reconstruct)
    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread()
        notices = app._browsers.handback
        original_deliver = notices.deliver
        receipts = []

        async def delivery(thread_id, prompt, current):
            accepted = await original_deliver(thread_id, prompt, current)
            if accepted:
                operation = await app.active_root_operation(thread_id)
                receipts.append(operation.receipt.receipt_id if operation else None)
                delivered.set()
            return accepted

        notices.deliver = delivery
        notices.takeover()
        first = await app.submit_thread(
            thread_id=thread.thread_id, prompt="Use the browser", mode="goal" if goal else "normal"
        )
        with fail_after(15):
            await waiting.wait()
            if not active:
                outcome = await app.wait_root_operation(first.receipt_id)
                if goal:
                    assert outcome.goal.status == "blocked"
            backend.observe.side_effect = None
            backend.observe.return_value = BrowserObservation("Restored", "fresh", ())
            notices.released()
            await delivered.wait()
            if active:
                assert receipts == [first.receipt_id]
                proceed.set()
            else:
                assert receipts[0] != first.receipt_id
            await resumed.wait()
            while (operation := await app.active_root_operation(thread.thread_id)) is not None:
                outcome = await app.wait_root_operation(operation.receipt.receipt_id)
                if not active:
                    assert outcome.goal is None
        assert stage == 4
        assert backend.observe.await_count == 2
        backend.wait_for_handback.assert_not_awaited()
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        hints = [
            part
            for entry in transcript.entries
            for part in entry.parts
            if part.metadata.source_id == "browser.handback"
        ]
        assert len(hints) == 1 and not hints[0].metadata.display


@pytest.mark.parametrize("change", ["stop", "archive", "clear"])
async def test_app_explicit_lifecycle_change_suppresses_handback(tmp_path, monkeypatch, change):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.surfaces import ThreadMetadataMutation, ThreadMetadataPatch
    from pydantic_ai.models.function import FunctionModel

    from .test_app import _settings, _write_configuration

    entered = Event()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        entered.set()
        if change == "stop":
            await Event().wait()
        yield "Waiting"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        thread = await app.create_thread()
        notices = app._browsers.handback
        notices.takeover()
        notices.blocked(thread.thread_id)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Start")
        with fail_after(15):
            await entered.wait()
            if change == "stop":
                await app.cancel_root_operation(receipt.receipt_id)
            await app.wait_root_operation(receipt.receipt_id)
            if change == "archive":
                await app.update_thread_metadata(
                    thread_id=thread.thread_id,
                    mutation=ThreadMetadataMutation(
                        expected_version=thread.metadata_version, patch=ThreadMetadataPatch(archived=True)
                    ),
                )
            elif change == "clear":
                detail = await app.get_thread(thread.thread_id)
                await app.clear_thread_context(
                    thread_id=thread.thread_id, expected_continuation_id=detail.continuation_id
                )
            notices.released()
            await wait_all_tasks_blocked()
        assert calls == 1
        assert await app.active_root_operation(thread.thread_id) is None
        assert thread.thread_id not in notices._pending


@pytest.mark.parametrize("suspend_during_delivery", [False, True])
async def test_app_handback_waits_for_real_approval_resolution(tmp_path, monkeypatch, suspend_during_delivery):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.surfaces import ApprovalDecision, ThreadDeferredResponse
    from pydantic_ai import Tool
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.models.function import DeltaToolCall

    from .test_app import _reconstructed, _settings, _write_configuration

    calls, effects = 0, []
    delivered, attempted, proceed = Event(), Event(), Event()
    if not suspend_during_delivery:
        proceed.set()

    async def change():
        effects.append("approved")
        return "done"

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            await proceed.wait()
            yield {0: DeltaToolCall(name="change", json_args="{}", tool_call_id="approval")}
        else:
            yield "Finished"

    class Reconstructor:
        def reconstruct(self, composition, *, root_capabilities=(), **kwargs):
            return _reconstructed(
                model, (*root_capabilities, Capability(id="approval", tools=[Tool(change, requires_approval=True)]))
            )

    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        app._root_runs._executor._agents = Reconstructor()
        thread = await app.create_thread()
        notices = app._browsers.handback
        notices.takeover()
        notices.blocked(thread.thread_id)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Start")
        if suspend_during_delivery:
            original_detail = app._projections.detail

            async def stale_detail(thread_id):
                snapshot = await original_detail(thread_id)
                if not proceed.is_set():
                    assert not snapshot.deferred_requests
                    proceed.set()
                    await app.wait_root_operation(receipt.receipt_id)
                return snapshot

            monkeypatch.setattr(app._projections, "detail", stale_detail)
        else:
            await app.wait_root_operation(receipt.receipt_id)
        original = notices.deliver

        async def delivery(thread_id, prompt, current):
            accepted = await original(thread_id, prompt, current)
            if accepted:
                delivered.set()
            attempted.set()
            return accepted

        notices.deliver = delivery
        notices.released()
        # Projection and Run settlement can be blocked on I/O while tasks are idle.
        with fail_after(15):
            await attempted.wait()
        assert calls == 1 and effects == [] and not delivered.is_set()
        assert thread.thread_id in notices._pending
        detail = await app.get_thread(thread.thread_id)
        assert len(detail.deferred_requests) == 1
        await app.respond_thread(
            thread_id=thread.thread_id,
            response=ThreadDeferredResponse(
                expected_continuation_id=detail.continuation_id,
                responses=(ApprovalDecision(request_id="approval", approved=True),),
            ),
        )
        with fail_after(15):
            await delivered.wait()
            while (active := await app.active_root_operation(thread.thread_id)) is not None:
                await app.wait_root_operation(active.receipt.receipt_id)
        assert effects == ["approved"]
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert (
            sum(part.metadata.source_id == "browser.handback" for entry in transcript.entries for part in entry.parts)
            == 1
        )


async def test_app_retake_while_native_steering_waits_preserves_notice(tmp_path, monkeypatch):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.models.function import FunctionModel

    entered, proceed = Event(), Event()

    async def model(messages, info):
        entered.set()
        await proceed.wait()
        yield "Finished"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(
        _settings_for_test(tmp_path), configuration_path=_configuration_for_test(tmp_path)
    ) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Start")
        with fail_after(15):
            await entered.wait()
            operation = app._root_runs._operations[receipt.receipt_id]
            bridge = operation.stream.context._steering
            notices = app._browsers.handback
            notices.takeover()
            notices.blocked(thread.thread_id)
            async with bridge._lock:
                notices.released()
                await wait_all_tasks_blocked()
                notices.takeover()
            await wait_all_tasks_blocked()
            assert not bridge._pending_requests
            assert thread.thread_id in notices._pending
            notices.released()
            await wait_all_tasks_blocked()
            assert len(bridge._pending_requests) == 1
            assert thread.thread_id not in notices._pending
            proceed.set()
            await app.wait_root_operation(receipt.receipt_id)


async def test_app_new_input_after_stop_registers_before_admission_publication(tmp_path, monkeypatch):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.models.function import FunctionModel

    entered, submitted = Event(), Event()
    results = []
    receipts = []

    async def model(messages, info):
        results.append(notices.blocked(thread.thread_id))
        entered.set()
        yield "Waiting for human control to end."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(
        _settings_for_test(tmp_path), configuration_path=_configuration_for_test(tmp_path)
    ) as app:
        thread = await app.create_thread()
        notices = app._browsers.handback
        notices.takeover()
        notices.suppress(thread.thread_id)
        original = app._root_runs._publish_change

        async def publish(operation, **kwargs):
            if operation.status == "preparing":
                await entered.wait()
            await original(operation, **kwargs)

        async def submit():
            receipts.append(await app.submit_thread(thread_id=thread.thread_id, prompt="New work"))
            submitted.set()

        monkeypatch.setattr(app._root_runs, "_publish_change", publish)
        async with create_task_group() as group:
            group.start_soon(submit)
            with fail_after(15):
                await submitted.wait()
                await app.wait_root_operation(receipts[0].receipt_id)
            assert results == [BLOCKED]
            assert thread.thread_id in notices._pending


def _settings_for_test(tmp_path):
    from .test_app import _settings

    return _settings(tmp_path / "state")


def _configuration_for_test(tmp_path):
    from .test_app import _write_configuration

    return _write_configuration(tmp_path)
