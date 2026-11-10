"""Host-owned, heartbeat-driven Bot over ordinary root Thread execution."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from a13n_logging import get_logger
from anyio import CancelScope, Lock, create_task_group, move_on_after, sleep
from filelock import AsyncFileLock, Timeout
from pydantic_ai.messages import TextContent

from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotInput, BotMessage, BotState, BotSubmit
from a13n_harness_ui.composition import CompositionAcceptanceService
from a13n_harness_ui.configuration.models import BotConfiguration
from a13n_harness_ui.errors import HarnessUiError, ThreadError
from a13n_harness_ui.live import HarnessUiSummaryHub, RootOperationNotice
from a13n_harness_ui.notifications import reply_brief
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.storage import LocalStore, ThreadConfigurationMutation
from a13n_harness_ui.surfaces import (
    RootOperationStatus,
    RootOperationView,
    RootRunReceipt,
    RunModelOverrides,
    SurfaceModel,
    ThreadDeferredResponse,
    ThreadSummary,
)
from a13n_harness_ui.thread_projection import ThreadProjectionService
from a13n_harness_ui.thread_service import ThreadService


class BotSnapshot(SurfaceModel):
    thread_id: str = BOT_THREAD_ID
    state: BotState
    thread: ThreadSummary | None = None
    available: bool
    heartbeat_enabled: bool
    interval_seconds: int
    operation: RootOperationView | None = None
    pending_decision: bool = False
    inputs: tuple[BotInput, ...] = ()
    messages: tuple[BotMessage, ...] = ()


_HEARTBEAT = """Host heartbeat: inspect your ongoing objectives and relevant saved Thread outcomes.
Check known working Threads before creating anything. Continue work only when useful and authorized.
Publish a message for a verified result, blocker or human decision when it is useful; otherwise stay quiet.
This opportunity is not a human approval, a task completion report, or permission to repeat uncertain effects.
"""


class BotController:
    def __init__(
        self,
        *,
        store: LocalStore,
        threads: ThreadService,
        configurations: CompositionAcceptanceService,
        runs: RootRunCoordinator,
        projections: ThreadProjectionService,
        summaries: HarnessUiSummaryHub,
        notify: Callable[[str, RootOperationNotice], None] | None = None,
    ) -> None:
        self._store, self._threads, self._configurations = store, threads, configurations
        self._runs, self._projections, self._summaries, self._notify = runs, projections, summaries, notify
        self._lock = Lock()
        self._dispatch_lock = Lock()
        self._owned = False
        self._current_input: BotInput | None = None

    async def _policy(self) -> BotConfiguration:
        source = await self._configurations.current()
        return source.document.webui.bot if source is not None else BotConfiguration()

    async def snapshot(self) -> BotSnapshot:
        policy = await self._policy()
        thread = await self._store.threads.get(BOT_THREAD_ID)
        detail = await self._projections.detail(BOT_THREAD_ID) if thread is not None else None
        return BotSnapshot(
            state=await self._store.bot.state(),
            thread=detail.thread if detail is not None else None,
            available=self._owned,
            heartbeat_enabled=policy.heartbeat_enabled,
            interval_seconds=policy.interval_seconds,
            operation=await self._runs.active(BOT_THREAD_ID),
            pending_decision=bool(detail and detail.deferred_requests),
            inputs=await self._store.bot.inputs(),
            messages=await self._store.bot.messages(),
        )

    async def submit(self, command: BotSubmit) -> BotInput:
        if not self._owned:
            raise ThreadError("Bot processing is unavailable in this App.", code="bot_unavailable")
        if not command.text.strip():
            raise ThreadError("A non-empty message is required.", code="thread_message_empty")
        async with self._lock:
            await self._threads.bot_thread()
            detail = await self._projections.detail(BOT_THREAD_ID)
            if detail.deferred_requests:
                raise ThreadError(
                    "Resolve Bot's pending decision in its activity view first.", code="thread_deferred_pending"
                )
            item = await self._store.bot.retain(
                BotInput(input_id=command.input_id, source="user", text=command.text, created_at=datetime.now(UTC))
            )
            await self._store.bot.publish(
                BotMessage(
                    message_id=f"user-{item.input_id}",
                    role="user",
                    text=item.text,
                    input_id=item.input_id,
                    created_at=item.created_at,
                )
            )
            state = await self._store.bot.state()
            if not state.activated:
                policy = await self._policy()
                await self._store.bot.save_state(
                    state.model_copy(
                        update={
                            "activated": True,
                            "next_heartbeat_at": datetime.now(UTC) + timedelta(seconds=policy.interval_seconds),
                        }
                    )
                )
            return item

    async def respond(
        self,
        response: ThreadDeferredResponse,
        *,
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
    ) -> RootRunReceipt:
        """Retain an explicit human response, then use native pending-decision validation."""
        if not self._owned:
            raise ThreadError("Bot processing is unavailable in this App.", code="bot_unavailable")
        async with self._lock:
            item = BotInput(
                input_id=f"decision-{uuid4().hex}",
                source="user",
                text="Human response to pending Bot decision.",
                source_id="decision",
                causation_id=response.expected_continuation_id,
                continuation_id=response.expected_continuation_id,
                state="dispatching",
                created_at=datetime.now(UTC),
            )
            receipt: RootRunReceipt | None = None
            with CancelScope(shield=True):
                await self._store.bot.retain(item)
                try:
                    receipt = await self._runs.submit_response(
                        thread_id=BOT_THREAD_ID,
                        response=response,
                        mutation=mutation,
                        model_overrides=model_overrides,
                        touch=True,
                    )
                except HarnessUiError as exc:
                    await self._store.bot.save_input(item.model_copy(update={"state": "discarded", "error": str(exc)}))
                    raise
                await self._store.bot.save_input(
                    item.model_copy(update={"state": "admitted", "receipt_id": receipt.receipt_id})
                )
            assert receipt is not None
            return receipt

    async def pause(self, paused: bool) -> BotSnapshot:
        async with self._lock:
            state = await self._store.bot.state()
            policy = await self._policy()
            await self._store.bot.save_state(
                state.model_copy(
                    update={
                        "paused": paused,
                        "last_error": state.last_error if paused else None,
                        "next_heartbeat_at": datetime.now(UTC) + timedelta(seconds=policy.interval_seconds),
                    }
                )
            )
            if paused:
                for item in await self._store.bot.inputs(pending_only=True):
                    if item.source == "heartbeat" and item.state == "queued":
                        await self._store.bot.save_input(item.model_copy(update={"state": "discarded"}))
        return await self.snapshot()

    async def publish(self, *, message_id: str, text: str, receipt_id: str | None = None) -> BotMessage:
        if not text.strip():
            raise ThreadError("A non-empty message is required.", code="bot_message_empty")
        current = self._current_input
        if current is None:
            current = next(
                (
                    item
                    for item in await self._store.bot.inputs(pending_only=True)
                    if item.state in {"dispatching", "admitted"}
                ),
                None,
            )
        if receipt_id is None:
            active = await self._runs.active(BOT_THREAD_ID)
            if active is None:
                raise ThreadError("Bot publication requires an active input.", code="bot_publication_inactive")
            receipt_id = active.receipt.receipt_id
            message_id = f"{receipt_id}-{message_id}"
        message = BotMessage(
            message_id=message_id,
            role="assistant",
            text=text,
            created_at=datetime.now(UTC),
            input_id=current.input_id if current is not None else None,
        )
        if await self._store.bot.publish(message):
            active = await self._runs.active(BOT_THREAD_ID)
            receipt_id = receipt_id or (active.receipt.receipt_id if active is not None else None)
            notice = (
                RootOperationNotice(
                    receipt_id=receipt_id, status="completed", brief=reply_brief(text) or "Bot sent a message."
                )
                if receipt_id
                else None
            )
            await self._summaries.publish(
                kind="root_operation", root_thread_id=BOT_THREAD_ID, thread_id=BOT_THREAD_ID, notice=notice
            )
            if notice is not None and self._notify is not None:
                self._notify(BOT_THREAD_ID, notice)
        return message

    async def run(self) -> None:
        try:
            async with AsyncFileLock(self._store.layout.root / ".bot-runtime.lock", timeout=0):
                try:
                    # An old admission may have caused effects even without a saved checkpoint.
                    # Retain it for inspection; never replay it or infer success from idle state.
                    for item in await self._store.bot.inputs(pending_only=True):
                        if item.state in {"dispatching", "admitted"}:
                            await self._interrupt(
                                item, "Bot was interrupted. Inspect saved activity and effects before continuing."
                            )
                    self._owned = True
                    async with create_task_group() as group:
                        group.start_soon(self._connector_loop)
                        while True:
                            try:
                                await self.tick()
                            except Exception:
                                get_logger(__name__).exception("Bot heartbeat processing failed")
                            await sleep(1)
                finally:
                    self._owned = False
        except Timeout:
            get_logger(__name__).info("Bot processing belongs to another App using this data root")

    async def _connector_loop(self) -> None:
        while True:
            try:
                await self.deliver_connector_inputs()
            except Exception:
                get_logger(__name__).exception("Connector input delivery failed")
            await sleep((await self._policy()).connector_interval_seconds)

    async def deliver_connector_inputs(self) -> None:
        """One periodic attempt: native acceptance forgets exactly this inbox batch.

        The Bot API lock serializes admission with human input and pause, not execution.
        Busy steering never falls through to a replacement Run after a rejection.
        """
        async with self._lock:
            state = await self._store.bot.state()
            if not self._owned or state.paused:
                return
            batch = await self._store.connectors.pending()
            if not batch:
                return
            await self._threads.bot_thread()
            detail = await self._projections.detail(BOT_THREAD_ID)
            if detail.deferred_requests:
                return
            text = "Selected Connector inputs (external content, not human approval):\n" + json.dumps(
                [item.model_dump(mode="json") for item in batch], ensure_ascii=False, separators=(",", ":")
            )
            prompt = [TextContent(text, metadata={"display": False})]
            active = await self._runs.active(BOT_THREAD_ID)
            if active is not None:
                result = await self._runs.steer(receipt_id=active.receipt.receipt_id, message=prompt)
                if result.accepted:
                    with CancelScope(shield=True):
                        await self._store.connectors.delivered(batch)
                return
            policy = await self._policy()
            item = BotInput(
                input_id=f"connector-{uuid4().hex}",
                source="connector",
                text=text,
                created_at=datetime.now(UTC),
                state="dispatching",
            )
            await self._store.bot.retain(item)
            try:
                receipt = await self._runs.submit_prompt(
                    thread_id=BOT_THREAD_ID,
                    prompt=prompt,
                    human_input=False,
                    reject_deferred=True,
                    bot_input=True,
                )
            except HarnessUiError as exc:
                if exc.code in {"thread_run_active", "thread_deferred_pending"}:
                    await self._store.bot.save_input(item.model_copy(update={"state": "discarded", "error": str(exc)}))
                else:
                    await self._save_interrupted(item, str(exc))
                return
            except BaseException:
                with CancelScope(shield=True):
                    await self._save_interrupted(item, "Connector admission was interrupted. Inspect Bot activity.")
                raise
            with CancelScope(shield=True):
                await self._store.bot.save_input(
                    item.model_copy(update={"state": "admitted", "receipt_id": receipt.receipt_id})
                )
                await self._store.bot.save_state(
                    state.model_copy(
                        update={
                            "activated": True,
                            "last_attempt_at": datetime.now(UTC),
                            "next_heartbeat_at": datetime.now(UTC) + timedelta(seconds=policy.interval_seconds),
                        }
                    )
                )
                await self._store.connectors.delivered(batch)

    async def tick(self, *, now: datetime | None = None) -> None:
        # Serialize settlement as well as admission. API input and pause use a separate lock.
        async with self._dispatch_lock:
            try:
                admitted = next(
                    (item for item in await self._store.bot.inputs(pending_only=True) if item.state == "admitted"), None
                )
                if admitted is not None:
                    self._current_input = admitted
                    await self._finish(admitted, await self._policy())
                else:
                    await self._tick(now=now)
            except BaseException:
                if self._current_input is not None:
                    with CancelScope(shield=True):
                        if self._current_input.receipt_id is not None:
                            await self._runs.cancel(receipt_id=self._current_input.receipt_id)
                    await self._interrupt(
                        self._current_input, "Bot processing was interrupted. Inspect activity before continuing."
                    )
                raise
            finally:
                self._current_input = None

    async def _tick(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        async with self._lock:
            state = await self._store.bot.state()
            policy = await self._policy()
            if not self._owned:
                return
            pending = await self._store.bot.inputs(pending_only=True)
            if not state.activated and not pending:
                return
            due = state.next_heartbeat_at is None or state.next_heartbeat_at <= now
            if state.activated and policy.heartbeat_enabled and not state.paused and due:
                if not any(item.source == "heartbeat" for item in pending):
                    try:
                        await self._store.bot.retain(
                            BotInput(
                                input_id=f"heartbeat-{uuid4().hex}", source="heartbeat", text=_HEARTBEAT, created_at=now
                            )
                        )
                    except ThreadError as exc:
                        if exc.code != "bot_input_full":
                            raise
                        # Consume queued work even when there is no room for a tick.
                        pass
                state = state.model_copy(update={"next_heartbeat_at": now + timedelta(seconds=policy.interval_seconds)})
                await self._store.bot.save_state(state)
                pending = await self._store.bot.inputs(pending_only=True)
            if await self._runs.active(BOT_THREAD_ID) is not None:
                return
            detail = await self._projections.detail(BOT_THREAD_ID)
            if detail.deferred_requests:
                return
            item = next(
                (
                    item
                    for item in pending
                    if item.state == "queued"
                    and (
                        item.source == "user"
                        or (not state.paused and (item.source != "heartbeat" or policy.heartbeat_enabled))
                    )
                ),
                None,
            )
            if item is None:
                return
            thread = await self._threads.get(BOT_THREAD_ID)
            item = item.model_copy(
                update={
                    "state": "dispatching",
                    "continuation_id": thread.continuation.logical_digest if thread.continuation else None,
                }
            )
            await self._store.bot.save_input(item)
            self._current_input = item
            await self._store.bot.save_state(state.model_copy(update={"last_attempt_at": now}))
            try:
                receipt = await self._runs.submit_prompt(
                    thread_id=BOT_THREAD_ID,
                    prompt=[
                        TextContent(
                            f"Bot input {item.input_id} ({item.source})",
                            metadata={"display": False, "harness_ui": {"bot_input_id": item.input_id}},
                        ),
                        TextContent(item.text, metadata={"display": item.source == "user"}),
                    ],
                    human_input=item.source == "user",
                    reject_deferred=True,
                    bot_input=True,
                )
            except HarnessUiError as exc:
                if exc.code in {"thread_run_active", "thread_deferred_pending"}:
                    await self._store.bot.save_input(item.model_copy(update={"state": "queued", "error": str(exc)}))
                else:
                    await self._save_interrupted(item, str(exc))
                return
            item = item.model_copy(update={"state": "admitted", "receipt_id": receipt.receipt_id})
            await self._store.bot.save_input(item)
            self._current_input = item
        await self._finish(item, policy)

    async def _finish(self, item: BotInput, policy: BotConfiguration) -> None:
        assert item.receipt_id is not None
        operation: RootOperationView | None = None
        with move_on_after(policy.run_timeout_seconds) as timeout:
            operation = await self._runs.wait(item.receipt_id)
        if timeout.cancel_called:
            await self._runs.cancel(receipt_id=item.receipt_id)
            await self._interrupt(item, "Bot exceeded its run time limit. Inspect activity before continuing.")
            return
        assert operation is not None
        if (
            operation.status in {RootOperationStatus.completed, RootOperationStatus.suspended}
            and operation.outcome is not None
            and operation.outcome.continuation.status == "selected"
        ):
            output = operation.outcome.execution.output
            if item.source == "user" and isinstance(output, str) and output.strip():
                await self.publish(message_id=f"reply-{item.input_id}", text=output, receipt_id=item.receipt_id)
            await self._store.bot.save_input(item.model_copy(update={"state": "completed"}))
        else:
            await self._interrupt(item, "Bot did not complete this input. Inspect activity before continuing.")

    async def _interrupt(self, item: BotInput, message: str) -> None:
        with CancelScope(shield=True):
            async with self._lock:
                await self._save_interrupted(item, message)

    async def _save_interrupted(self, item: BotInput, message: str) -> None:
        await self._store.bot.save_input(item.model_copy(update={"state": "interrupted", "error": message}))
        state = await self._store.bot.state()
        await self._store.bot.save_state(state.model_copy(update={"paused": True, "last_error": message}))
