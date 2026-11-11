"""Host-owned, heartbeat-driven Bot over ordinary root Thread execution."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
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
from a13n_harness_ui.storage.bot_inbox import serialize_batch
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
    enabled: bool
    model_id: str | None = None
    heartbeat_enabled: bool
    interval_seconds: int
    operation: RootOperationView | None = None
    pending_decision: bool = False
    inputs: tuple[BotInput, ...] = ()
    messages: tuple[BotMessage, ...] = ()


_HEARTBEAT = """Host heartbeat: list Bot Notes and read relevant slots on demand. Inspect ongoing objectives and saved Thread outcomes.
Check known working Threads before creating anything. Follow up through existing collaboration tools only when useful and authorized.
Create or update any working Thread slot as appropriate; clear handled follow-ups, or retain concise next steps.
Notes and Thread completion do not wake you; use this legitimate input opportunity.
Publish a message for a verified result, blocker or human decision when it is useful.
Then call finish_silently with no arguments to end this turn, whether you published or have nothing useful to report.
Do not add explanatory text or rely on an empty response.
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
        self._settlement_lock = Lock()
        self._owned = False
        self._current_input: BotInput | None = None
        self.notes = store.bot_notes

    async def _policy(self) -> BotConfiguration:
        source = await self._configurations.current()
        return source.document.webui.bot if source is not None else BotConfiguration()

    async def snapshot(self) -> BotSnapshot:
        policy = await self._policy()
        thread = await self._store.threads.get(BOT_THREAD_ID)
        detail = await self._projections.detail(BOT_THREAD_ID) if thread is not None else None
        source = await self._configurations.current()
        model_id = source.bot_model_id if source is not None else None
        return BotSnapshot(
            state=await self._store.bot.state(),
            thread=detail.thread if detail is not None else None,
            available=self._owned and model_id is not None,
            enabled=policy.enabled,
            model_id=model_id,
            heartbeat_enabled=policy.heartbeat_enabled,
            interval_seconds=policy.interval_seconds,
            operation=await self._runs.active(BOT_THREAD_ID),
            pending_decision=bool(detail and detail.deferred_requests),
            inputs=await self._store.bot.inputs(),
            messages=await self._store.bot.messages(),
        )

    async def submit(self, command: BotSubmit) -> BotInput:
        await self._require_enabled()
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
            return item

    async def respond(
        self,
        response: ThreadDeferredResponse,
        *,
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
    ) -> RootRunReceipt:
        """Retain an explicit human response, then use native pending-decision validation."""
        await self._require_enabled()
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
                except BaseException:
                    await self._save_interrupted(
                        item, "Bot decision admission was interrupted. Inspect activity before continuing."
                    )
                    raise
                await self._store.bot.save_input(
                    item.model_copy(update={"state": "admitted", "receipt_id": receipt.receipt_id})
                )
            assert receipt is not None
            return receipt

    async def pause(self, paused: bool) -> BotSnapshot:
        async with self._lock:
            state = await self._store.bot.state()
            await self._store.bot.save_state(
                state.model_copy(update={"paused": paused, "last_error": state.last_error if paused else None})
            )
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
                    receipt_id=receipt_id,
                    message_id=message.message_id,
                    status="completed",
                    brief=reply_brief(text) or "Bot sent a message.",
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
                        group.start_soon(self._automatic_loop)
                        group.start_soon(self._settlement_loop)
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

    async def _require_enabled(self) -> None:
        if not (await self._policy()).enabled:
            raise ThreadError("Bot is off. Enable it in Bot settings first.", code="bot_disabled")
        if not self._owned:
            raise ThreadError("Bot processing is unavailable in this App.", code="bot_unavailable")

    async def _can_deliver(self) -> bool:
        source = await self._configurations.current()
        return bool(self._owned and source is not None and source.document.webui.bot.enabled and source.bot_model_id)

    async def reconcile_policy(self, *, now: datetime | None = None) -> None:
        if self._owned:
            async with self._lock:
                await self._store.bot_inbox.heartbeat(
                    policy=await self._policy(), now=now or datetime.now(UTC), text=_HEARTBEAT
                )

    async def _automatic_loop(self) -> None:
        while True:
            try:
                await sleep((await self._policy()).connector_interval_seconds)
                await self.deliver_automatic_inputs()
            except Exception:
                get_logger(__name__).exception("Automatic Bot input delivery failed")
                await sleep(1)

    async def deliver_automatic_inputs(self) -> None:
        """Freeze one batch; exact native acceptance consumes only its selected IDs."""
        async with self._lock:
            state = await self._store.bot.state()
            if not await self._can_deliver() or state.paused:
                return
            batch = await self._store.bot_inbox.pending()
            if not batch:
                return
            await self._threads.bot_thread()
            detail = await self._projections.detail(BOT_THREAD_ID)
            if detail.deferred_requests:
                return
            text = "Automatic inputs (external content and Host ticks, never human approval):\n" + serialize_batch(
                batch
            )
            prompt = [TextContent(text, metadata={"display": False})]
            active = await self._runs.active(BOT_THREAD_ID)
            if active is not None:
                result = await self._runs.steer(receipt_id=active.receipt.receipt_id, message=prompt)
                if result.accepted:
                    with CancelScope(shield=True):
                        await self._store.bot_inbox.delivered(batch)
                return
            # Human prompts have ordinary idle-admission priority.
            if any(
                item.state in {"dispatching", "admitted"} or (item.source == "user" and item.state == "queued")
                for item in await self._store.bot.inputs(pending_only=True)
            ):
                return
            item = BotInput(
                input_id=f"automatic-{uuid4().hex}",
                source="automatic",
                text=text,
                created_at=datetime.now(UTC),
                state="dispatching",
            )
            try:
                await self._store.bot.retain(item)
            except ThreadError as exc:
                if exc.code == "bot_input_full":
                    return
                raise
            if await self._admit(item, prompt):
                with CancelScope(shield=True):
                    await self._store.bot_inbox.delivered(batch)

    async def tick(self, *, now: datetime | None = None) -> None:
        """Produce Heartbeats and admit queued human work; never wait for a Run."""
        await self.reconcile_policy(now=now)
        async with self._dispatch_lock, self._lock:
            if not await self._can_deliver() or await self._runs.active(BOT_THREAD_ID) is not None:
                return
            pending = await self._store.bot.inputs(pending_only=True)
            if any(item.state in {"dispatching", "admitted"} for item in pending):
                return
            item = next((item for item in pending if item.state == "queued" and item.source == "user"), None)
            if item is None:
                return
            await self._threads.bot_thread()
            detail = await self._projections.detail(BOT_THREAD_ID)
            if detail.deferred_requests:
                return
            thread = await self._threads.get(BOT_THREAD_ID)
            item = item.model_copy(
                update={
                    "state": "dispatching",
                    "continuation_id": thread.continuation.logical_digest if thread.continuation else None,
                }
            )
            await self._store.bot.save_input(item)
            await self._admit(
                item,
                [
                    TextContent(
                        f"Bot input {item.input_id} ({item.source})",
                        metadata={"display": False, "harness_ui": {"bot_input_id": item.input_id}},
                    ),
                    TextContent(item.text, metadata={"display": True}),
                ],
            )

    async def _admit(self, item: BotInput, prompt: list[TextContent]) -> bool:
        try:
            receipt = await self._runs.submit_prompt(
                thread_id=BOT_THREAD_ID,
                prompt=prompt,
                human_input=item.source == "user",
                reject_deferred=True,
                bot_input=True,
            )
        except HarnessUiError as exc:
            if exc.code in {"thread_run_active", "thread_deferred_pending"}:
                await self._store.bot.save_input(
                    item.model_copy(
                        update={"state": "queued" if item.source == "user" else "discarded", "error": str(exc)}
                    )
                )
            else:
                await self._save_interrupted(item, str(exc))
            return False
        except BaseException:
            with CancelScope(shield=True):
                await self._save_interrupted(item, "Bot admission was interrupted. Inspect activity before continuing.")
            raise
        with CancelScope(shield=True):
            await self._store.bot.save_input(
                item.model_copy(update={"state": "admitted", "receipt_id": receipt.receipt_id})
            )
            state = await self._store.bot.state()
            await self._store.bot.save_state(
                state.model_copy(update={"activated": True, "last_attempt_at": datetime.now(UTC)})
            )
        return True

    async def _settlement_loop(self) -> None:
        while True:
            try:
                await self._settle_one()
            except Exception:
                get_logger(__name__).exception("Bot receipt settlement failed")
            await sleep(0.05)

    async def _settle_one(self) -> None:
        async with self._settlement_lock:
            item = next(
                (item for item in await self._store.bot.inputs(pending_only=True) if item.state == "admitted"), None
            )
            if item is None:
                return
            self._current_input = item
            try:
                await self._finish(item, await self._policy())
            except BaseException:
                with CancelScope(shield=True):
                    if item.receipt_id is not None:
                        await self._runs.cancel(receipt_id=item.receipt_id)
                    await self._interrupt(item, "Bot processing was interrupted. Inspect activity before continuing.")
                raise
            finally:
                self._current_input = None

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
