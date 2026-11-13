"""Host-owned, heartbeat-driven Bot over ordinary root Thread execution."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from a13n_harness.input import RunInputValue
from a13n_logging import get_logger
from anyio import CancelScope, Event, Lock, create_task_group, move_on_after, sleep
from filelock import AsyncFileLock, Timeout
from pydantic_ai.messages import TextContent

from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotInput, BotMessage, BotNotesPage, BotProse, BotState, BotSubmit
from a13n_harness_ui.composition import CompositionAcceptanceService
from a13n_harness_ui.configuration.models import BotConfiguration
from a13n_harness_ui.connectors.models import SetupOffer
from a13n_harness_ui.display_history import DisplayPublication, assistant_text
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
from a13n_harness_ui.thread_files import ComposerAttachmentReference, ComposerInput, ThreadAttachment, ThreadFiles
from a13n_harness_ui.thread_projection import ThreadProjectionService
from a13n_harness_ui.thread_service import ThreadService


class BotSnapshot(SurfaceModel):
    thread_id: str = BOT_THREAD_ID
    state: BotState
    thread: ThreadSummary | None = None
    available: bool
    enabled: bool
    agent_id: str | None = None
    model_id: str | None = None
    heartbeat_enabled: bool
    interval_seconds: int
    operation: RootOperationView | None = None
    pending_decision: bool = False
    inputs: tuple[BotInput, ...] = ()
    messages: tuple[BotMessage, ...] = ()
    prose: tuple[BotProse, ...] = ()


WORKSPACE_INTRODUCTION = BotSubmit(
    input_id="input-workspace-introduction",
    text=(
        "Help me get oriented in this workspace. Discover the configured Projects and their roots, "
        "read relevant Project memory and existing Thread notes or outcomes, and build a concise workspace map "
        "using your memory tools when available. Tell me what you found, distinguish verified facts from "
        "uncertainty, and ask what I would like to work on next. This first conversation is for orientation "
        "and memory maintenance; do not start implementation or change project configuration."
    ),
)


_HEARTBEAT = """Host heartbeat: read HEARTBEAT.md in your working directory if it exists and follow its current checklist.
Create or update HEARTBEAT.md as useful to manage future checks.
List Bot Notes and read relevant slots on demand. Inspect ongoing objectives and saved Thread outcomes.
Maintain your Bot memory workspace map in bounded passes: check relevant, changed or unknown Projects,
consult source memory indexes before selected files, and correct stale understanding with source references.
Do not reread every Project in full or invent work because a Project exists.
Check known working Threads before creating anything. Follow up through existing collaboration tools only when useful and authorized.
Create or update any working Thread slot as appropriate; clear handled follow-ups, or retain concise next steps.
Notes and Thread completion do not wake you. Explicit Thread messages are separate inputs.
Coordinate automatic reports through tools; do not acknowledge them to the human.
Work through tools without a preamble or routine progress narration. Keep check details in notes and tasks.
Output only a concise important verified result, actionable blocker or human decision.
Every visible text segment marks unread and notifies the human; do not announce routine checks or acknowledgements.
When nothing useful is due or you already published the complete response, call finish_silently with no arguments.
For a silent exit, do not add explanatory text or rely on an empty response.
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
        files: ThreadFiles,
        prepare_input: Callable[[ComposerInput], Awaitable[RunInputValue]],
        notify: Callable[[str, RootOperationNotice], None] | None = None,
    ) -> None:
        self._store, self._threads, self._configurations = store, threads, configurations
        self._runs, self._projections, self._summaries, self._notify = runs, projections, summaries, notify
        self._files, self._prepare_input = files, prepare_input
        self._lock = Lock()
        self._settlement_lock = Lock()
        self._inbox_changed = Event()
        self._owned = False
        self.notes = store.bot_notes
        self.notes.on_change = self._note_changed

    async def _note_changed(self, thread_id: str) -> None:
        await self._summaries.publish(kind="thread_work", root_thread_id=thread_id, thread_id=thread_id)

    async def _policy(self) -> BotConfiguration:
        source = await self._configurations.current()
        return source.document.webui.bot if source is not None else BotConfiguration()

    async def list_notes(self, *, limit: int = 20, offset: int = 0) -> BotNotesPage:
        return BotNotesPage.model_validate_json(
            json.dumps(await self.notes.list(caller=BOT_THREAD_ID, limit=limit, offset=offset))
        )

    async def collaboration_status(self) -> dict[str, bool]:
        return {"enabled": (await self._policy()).enabled, "paused": (await self._store.bot.state()).paused}

    async def receive_thread_message(
        self, *, source_thread_id: str, source_thread_title: str | None, text: str, kind: Literal["task", "report"]
    ) -> dict[str, object]:
        input_id = await self._store.bot_inbox.thread_message(
            source_thread_id=source_thread_id,
            source_thread_title=source_thread_title,
            text=text,
            kind=kind,
        )
        self._inbox_changed.set()
        return {"ok": True, "mode": "inbox", "input_id": input_id}

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
            agent_id=source.bot_agent_id if source is not None else None,
            model_id=model_id,
            heartbeat_enabled=policy.heartbeat_enabled,
            interval_seconds=policy.interval_seconds,
            operation=await self._runs.active(BOT_THREAD_ID),
            pending_decision=bool(detail and detail.deferred_requests),
            inputs=await self._store.bot.inputs(),
            messages=await self._store.bot.messages(),
            prose=await self._saved_prose(),
        )

    async def _saved_prose(self, *, current_run_only: bool = False) -> tuple[BotProse, ...]:
        from a13n_harness_ui.storage.checkpoint_lock import checkpoint_lock
        from a13n_harness_ui.storage.continuations import read_continuation
        from a13n_harness_ui.storage.display import read_display

        async with checkpoint_lock(self._store.layout.root, BOT_THREAD_ID):
            thread = await self._store.threads.get(BOT_THREAD_ID)
            if thread is None or thread.continuation is None:
                return ()
            stored = await read_continuation(self._store.objects, thread.continuation, include_display=True)
            if stored.display is None:
                return ()
            display = await read_display(self._store.objects, stored.display)
        return tuple(
            BotProse(
                item_id=item.id,
                text=text,
                created_at=item.started_at,
                final=item.id in display.completed,
            )
            for item in display.items
            if assistant_text(item)
            and (
                not current_run_only
                or (
                    display.continuation is not None
                    and item.content.get("responseGroup") == display.continuation.response_groups.get("root")
                )
            )
            and isinstance(text := item.content.get("text"), str)
            and not (isinstance(metadata := item.content.get("metadata"), dict) and metadata.get("display") is False)
            and text.strip()
        )

    async def submit(self, command: BotSubmit) -> BotInput:
        await self._require_enabled()
        retained: BotInput | None = None
        with CancelScope(shield=True):
            async with self._lock:
                retained = await self._submit_locked(command)
        assert retained is not None
        return retained

    async def _submit_locked(self, command: BotSubmit) -> BotInput:
        """Ordinary user admission, shared by assisted introduction and direct chat."""
        await self._require_enabled()
        await self._threads.bot_thread()
        parts: list[str | ThreadAttachment] = []
        for part in command.parts:
            parts.append(
                part if isinstance(part, str) else (await self._files.read(BOT_THREAD_ID, part.attachment_id))[0]
            )
        previous = await self._store.bot.get_input(command.input_id)
        if previous is not None:
            item = await self._store.bot.retain(
                BotInput(
                    input_id=command.input_id,
                    source="user",
                    text=command.text,
                    parts=tuple(parts),
                    created_at=datetime.now(UTC),
                )
            )
            if item.state == "discarded":
                raise ThreadError(item.error or "Bot input was rejected.", code="bot_input_rejected")
            await self._publish_user(item)
            return item
        detail = await self._projections.detail(BOT_THREAD_ID)
        if detail.deferred_requests:
            raise ThreadError(
                "Resolve Bot's pending decision in the conversation first.", code="thread_deferred_pending"
            )
        thread = await self._threads.get(BOT_THREAD_ID)
        authored = await self._prepare_input(
            ComposerInput(
                parts=tuple(
                    part if isinstance(part, str) else ComposerAttachmentReference(part.attachment_id)
                    for part in command.parts
                )
                if command.parts
                else (command.text,),
            )
        )
        proposed = BotInput(
            input_id=command.input_id,
            source="user",
            text=command.text,
            parts=tuple(parts),
            created_at=datetime.now(UTC),
            state="dispatching",
            continuation_id=thread.continuation.logical_digest if thread.continuation else None,
        )
        item = await self._store.bot.retain(proposed)
        prompt = [
            TextContent(
                f"Bot input {item.input_id} ({item.source})",
                metadata={"display": False, "harness_ui": {"bot_input_id": item.input_id}},
            ),
            *([authored] if isinstance(authored, str) else authored),
        ]
        active = await self._runs.active(BOT_THREAD_ID)
        if active is None:
            await self._admit(item, prompt)
        else:
            item = item.model_copy(update={"receipt_id": active.receipt.receipt_id})
            # Native acceptance and its local correlation form one cancellation boundary.
            try:
                result = await self._runs.steer(
                    receipt_id=active.receipt.receipt_id, message=prompt, immediate=True, touch=True
                )
                if result.accepted:
                    await self._store.bot.save_input(item.model_copy(update={"state": "admitted"}))
            except BaseException:
                await self._save_interrupted(
                    item,
                    "Bot steering was interrupted. Review the reported error and working Thread results before continuing.",
                )
                raise
            if not result.accepted:
                error = "Bot is not accepting input. Send a new message when ready."
                await self._store.bot.save_input(item.model_copy(update={"state": "discarded", "error": error}))
                raise ThreadError(error, code="bot_input_rejected")
        retained = await self._store.bot.get_input(item.input_id)
        assert retained is not None
        await self._publish_user(retained)
        return retained

    async def introduce_workspace(self) -> None:
        """Send one visible ordinary user input when an unused Bot first becomes usable."""
        with CancelScope(shield=True):
            async with self._lock:
                state = await self._store.bot.state()
                if not await self._can_deliver() or state.paused or state.activated:
                    return
                if await self._store.bot.inputs() or await self._store.bot.messages():
                    return
                thread = await self._store.threads.get(BOT_THREAD_ID)
                if thread is not None and thread.continuation is not None:
                    return
                if await self._runs.active(BOT_THREAD_ID) is not None:
                    return
                if thread is not None and (await self._projections.detail(BOT_THREAD_ID)).deferred_requests:
                    return
                try:
                    await self._submit_locked(WORKSPACE_INTRODUCTION)
                finally:
                    # The Host authored this first message; even rejected admission must be visible.
                    item = await self._store.bot.get_input(WORKSPACE_INTRODUCTION.input_id)
                    if item is not None:
                        await self._publish_user(item)

    async def _publish_user(self, item: BotInput) -> None:
        published = await self._store.bot.publish(
            BotMessage(
                message_id=f"user-{item.input_id}",
                role="user",
                text=item.text,
                parts=item.parts,
                input_id=item.input_id,
                created_at=item.created_at,
            )
        )
        if published:
            await self._summaries.publish(kind="thread", root_thread_id=BOT_THREAD_ID, thread_id=BOT_THREAD_ID)

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
                        item,
                        "Bot decision admission was interrupted. Review the reported error and working Thread results before continuing.",
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
        if not paused:
            self.signal_inputs()
        return await self.snapshot()

    async def publish(
        self,
        *,
        message_id: str,
        text: str,
        receipt_id: str | None = None,
        connector_setups: tuple[SetupOffer, ...] = (),
        display_ids: tuple[str, ...] = (),
    ) -> BotMessage:
        if receipt_id is None:
            # A tool can run before native admission returns. Wait for its input
            # correlation before publishing so quiet completion retains unread state.
            async with self._lock:
                return await self._publish(
                    message_id=message_id, text=text, connector_setups=connector_setups, display_ids=display_ids
                )
        # Settlement already owns the control lock.
        return await self._publish(
            message_id=message_id,
            text=text,
            receipt_id=receipt_id,
            connector_setups=connector_setups,
            display_ids=display_ids,
        )

    async def publish_display(self, publication: DisplayPublication, stopped: bool = False) -> None:
        """Notify once per ended segment, including partial text when execution stops."""
        for item in publication.items:
            if (
                assistant_text(item)
                and (stopped or item.state != "in_progress")
                and isinstance(text := item.content.get("text"), str)
                and text.strip()
                and not (
                    isinstance(metadata := item.content.get("metadata"), dict) and metadata.get("display") is False
                )
            ):
                await self.publish(
                    message_id=_prose_message_id(item.id),
                    text=text,
                    display_ids=(item.id,),
                )

    async def _publish(
        self,
        *,
        message_id: str,
        text: str,
        receipt_id: str | None = None,
        connector_setups: tuple[SetupOffer, ...] = (),
        display_ids: tuple[str, ...] = (),
    ) -> BotMessage:
        if not text.strip():
            raise ThreadError("A non-empty message is required.", code="bot_message_empty")
        if receipt_id is None:
            active = await self._runs.active(BOT_THREAD_ID)
            if active is None:
                raise ThreadError("Bot publication requires an active input.", code="bot_publication_inactive")
            receipt_id = active.receipt.receipt_id
            message_id = f"{receipt_id}-{message_id}"
        current = next(
            (item for item in await self._store.bot.inputs(pending_only=True) if item.receipt_id == receipt_id),
            None,
        )
        message = BotMessage(
            message_id=message_id,
            role="assistant",
            text=text,
            created_at=datetime.now(UTC),
            input_id=current.input_id if current is not None else None,
            connector_setups=connector_setups,
            display_ids=display_ids,
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

    async def run(self, owned: bool | None = None) -> None:
        if owned is not None:
            if owned:
                await self._run_owned()
            return
        try:
            async with AsyncFileLock(self._store.layout.root / ".bot-runtime.lock", timeout=0):
                await self._run_owned()
        except Timeout:
            get_logger(__name__).info("Bot processing belongs to another App using this data root")

    async def _run_owned(self) -> None:
        try:
            for item in await self._store.bot.inputs(pending_only=True):
                if item.state in {"queued", "dispatching", "admitted"}:
                    await self._interrupt(
                        item, "Bot was interrupted. Review the reported error and any effects before continuing."
                    )
            self._owned = True
            async with create_task_group() as group:
                group.start_soon(self._automatic_loop)
                group.start_soon(self._settlement_loop)
                while True:
                    try:
                        await self.introduce_workspace()
                        await self.tick()
                    except Exception:
                        get_logger(__name__).exception("Bot heartbeat processing failed")
                    await sleep(1)
        finally:
            self._owned = False

    def signal_inputs(self) -> None:
        self._inbox_changed.set()

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
                signal = self._inbox_changed
                with move_on_after((await self._policy()).connector_interval_seconds):
                    await signal.wait()
                self._inbox_changed = Event()
                await self.deliver_automatic_inputs()
            except Exception:
                get_logger(__name__).exception("Automatic Bot input delivery failed")
                await sleep(1)

    async def deliver_automatic_inputs(self) -> None:
        """Freeze one batch; exact native acceptance consumes only its selected IDs."""
        # An immediately notified report must not win the first activation over visible orientation.
        await self.introduce_workspace()
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
            text = (
                "Automatic inputs (Thread messages, external content and Host ticks, never human approval). Use tools for internal coordination; do not acknowledge reports. Human messages received during this Run still deserve a timely response:\n"
                + serialize_batch(batch)
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
        """Produce Heartbeats independently of direct human input and Run settlement."""
        await self.reconcile_policy(now=now)

    async def _admit(self, item: BotInput, prompt: RunInputValue) -> bool:
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
                await self._store.bot.save_input(item.model_copy(update={"state": "discarded", "error": str(exc)}))
            else:
                await self._save_interrupted(item, str(exc))
            if item.source == "user":
                raise
            return False
        except BaseException:
            with CancelScope(shield=True):
                await self._save_interrupted(
                    item,
                    "Bot admission was interrupted. Review the reported error and working Thread results before continuing.",
                )
            raise
        with CancelScope(shield=True):
            item = item.model_copy(update={"receipt_id": receipt.receipt_id})
            try:
                await self._store.bot.save_input(item.model_copy(update={"state": "admitted"}))
                state = await self._store.bot.state()
                await self._store.bot.save_state(
                    state.model_copy(update={"activated": True, "last_attempt_at": datetime.now(UTC)})
                )
            except BaseException:
                await self._save_interrupted(
                    item,
                    "Bot admission was interrupted. Review the reported error and working Thread results before continuing.",
                )
                raise
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
            try:
                await self._finish(item)
            except BaseException:
                with CancelScope(shield=True):
                    if item.receipt_id is not None:
                        await self._runs.cancel(receipt_id=item.receipt_id)
                    await self._interrupt(
                        item,
                        "Bot processing was interrupted. Review the reported error and working Thread results before continuing.",
                    )
                raise

    async def _finish(self, item: BotInput) -> None:
        assert item.receipt_id is not None
        operation = await self._runs.wait(item.receipt_id)
        # Include human steering accepted while this receipt was running, and serialize
        # settlement with input acceptance so every correlated input settles together.
        async with self._lock:
            related = await self._receipt_inputs(item)
            if (
                operation.status in {RootOperationStatus.completed, RootOperationStatus.suspended}
                and operation.outcome is not None
                and operation.outcome.continuation.status == "selected"
            ):
                output = operation.outcome.execution.output
                if isinstance(output, str) and output.strip():
                    final = tuple(row for row in await self._saved_prose(current_run_only=True) if row.final)
                    if final:
                        for row in final:
                            # Live publication and settlement share the same durable
                            # identity, so final text never creates a second notice.
                            await self.publish(
                                message_id=f"{item.receipt_id}-{_prose_message_id(row.item_id)}",
                                text=row.text,
                                receipt_id=item.receipt_id,
                                display_ids=(row.item_id,),
                            )
                    else:
                        # Hosts/models without native display items retain the ordinary fallback.
                        await self.publish(
                            message_id=f"reply-{item.receipt_id}",
                            text=output,
                            receipt_id=item.receipt_id,
                        )
                for value in related:
                    await self._store.bot.save_input(value.model_copy(update={"state": "completed"}))
            else:
                for value in related:
                    await self._save_interrupted(
                        value,
                        "Bot did not complete this input. Review the reported error and working Thread results before continuing.",
                    )
            if related:
                await self._summaries.publish(kind="thread", root_thread_id=BOT_THREAD_ID, thread_id=BOT_THREAD_ID)

    async def _receipt_inputs(self, item: BotInput) -> tuple[BotInput, ...]:
        return tuple(
            value
            for value in await self._store.bot.inputs(pending_only=True)
            if value.input_id == item.input_id or (item.receipt_id is not None and value.receipt_id == item.receipt_id)
        )

    async def _interrupt(self, item: BotInput, message: str) -> None:
        with CancelScope(shield=True):
            async with self._lock:
                for value in await self._receipt_inputs(item):
                    await self._save_interrupted(value, message)

    async def _save_interrupted(self, item: BotInput, message: str) -> None:
        await self._store.bot.save_input(item.model_copy(update={"state": "interrupted", "error": message}))
        state = await self._store.bot.state()
        await self._store.bot.save_state(state.model_copy(update={"paused": True, "last_error": message}))


def _prose_message_id(item_id: str) -> str:
    return f"prose-{sha256(item_id.encode()).hexdigest()}"
