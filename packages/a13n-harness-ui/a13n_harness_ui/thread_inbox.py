"""One App-owned dispatcher for durable non-human Thread inputs."""

from typing import TYPE_CHECKING

from a13n_harness.content import ContentItem, ContentMetadata
from a13n_harness.envelopes import Envelope, ThreadCondition, disposition, input_source, render_envelope
from a13n_logging import get_logger
from anyio import CancelScope, sleep
from anyio.abc import TaskGroup
from anyio.lowlevel import checkpoint

from a13n_harness_ui.bot_models import BOT_THREAD_ID
from a13n_harness_ui.composition.models import ResolvedRunComposition
from a13n_harness_ui.errors import RunCoordinationError, ThreadError
from a13n_harness_ui.storage.ownership import OwnershipLocks

if TYPE_CHECKING:
    from a13n_harness_ui.app import HarnessUiApp


def root_condition(thread, operation, *, deferred: bool, cancelling: bool = False) -> ThreadCondition:
    """An inactive receipt is not authority to automatically continue a saved attempt."""
    if thread.archived or thread.memory_scope is not None:
        return "archived"
    if deferred:
        return "waiting"
    if operation is not None:
        if cancelling:
            return "paused"
        return "active" if operation.status == "running" else "preparing"
    last = thread.last_execution
    if last is None:
        return "idle" if thread.continuation is None else "paused"
    return "idle" if last.status == "completed" else "paused"


class ThreadInbox:
    def __init__(self, app: "HarnessUiApp") -> None:
        self.app = app
        self.store = app._store.thread_inbox
        self.owners = OwnershipLocks(app._store.layout.root / "inbox-locks")
        self._group: TaskGroup | None = None
        self._scheduled: set[str] = set()
        self._dirty: set[str] = set()
        self._offered: dict[str, set[str]] = {}
        self._accepting = True

    def start(self, group: TaskGroup) -> None:
        self._group = group

    async def deliver(self, thread_id: str, envelope: Envelope) -> dict[str, object]:
        thread = await self.app._store.threads.get(thread_id)
        if thread is None and thread_id != BOT_THREAD_ID:
            raise ThreadError("The target Thread is unavailable.", code="thread_missing")
        if thread is not None and (thread.archived or thread.memory_scope is not None):
            raise ThreadError("The target Thread cannot receive input.", code="thread_archived")
        identity = await self.store.put(thread_id, envelope)
        self.schedule(thread_id)
        return {"accepted": True, "input_id": identity}

    def schedule(self, thread_id: str) -> None:
        if not self._accepting or self._group is None:
            return
        if thread_id in self._scheduled:
            self._dirty.add(thread_id)
            return
        self._scheduled.add(thread_id)
        self._group.start_soon(self._dispatch, thread_id)

    def settled(self, thread_id: str) -> None:
        self._offered.pop(thread_id, None)
        self.schedule(thread_id)

    def busy_thread_ids(self) -> frozenset[str]:
        return frozenset(self._scheduled)

    async def condition(self, thread_id: str) -> ThreadCondition:
        app = self.app
        thread = await app._store.threads.get(thread_id)
        if thread is not None and (thread.archived or thread.memory_scope is not None):
            return "archived"
        if app._state.value != "ready" or not app._root_runs._accepting:
            return "preparing"
        if thread_id == BOT_THREAD_ID:
            bot = app._bot
            if bot is None or not await bot._can_deliver():
                return "preparing"
            if (await app._store.bot.state()).paused:
                return "paused"
            if thread is None:
                thread = await app._threads.bot_thread()
        if thread is None:
            return "archived"
        if thread.parent_thread_id is not None:
            return await app._subagent_operator.input_condition(thread_id)
        detail = await app._projections.detail(thread_id)
        operation = await app._root_runs.active(thread_id)
        internal = None if operation is None else app._root_runs._operations.get(operation.receipt.receipt_id)
        condition = root_condition(
            thread,
            operation,
            deferred=bool(detail.deferred_requests),
            cancelling=internal is not None and internal.cancel_requested,
        )
        if thread_id == BOT_THREAD_ID and operation is None and not detail.deferred_requests:
            last = thread.last_execution
            if (
                last is not None
                and last.status == "failed"
                and last.error_code not in {"execution_status_save_failed", "continuation_selection_failed"}
            ):
                condition = "idle"
            if any(
                item.state == "admitted" or (item.source == "user" and item.state in {"queued", "dispatching"})
                for item in await app._store.bot.inputs(pending_only=True)
            ):
                return "preparing"
        if thread.configuration.agent_source.kind == "live":
            source = await app._configurations.current()
            if source is None or source.document.live is None or not source.document.live.enabled:
                return "preparing"
            if operation is not None:
                reference = await app._root_runs.composition_reference(operation.receipt.receipt_id)
                if reference is None:
                    return "preparing"
                composition = await app._store.objects.read_model(reference, ResolvedRunComposition)
                if composition.root.model.model_id != source.document.live.model:
                    return "preparing"
        return condition

    async def _dispatch(self, thread_id: str) -> None:
        ids: set[str] = set()
        try:
            await checkpoint()
            if thread_id == BOT_THREAD_ID and self.app._bot is not None:
                await self.app._bot.introduce_workspace()
            condition = await self.condition(thread_id)
            if condition == "archived":
                await self.store.discard(thread_id)
                return
            if condition not in {"active", "idle"} or not await self.owners.acquire(thread_id):
                return
            condition = await self.condition(thread_id)
            rows = await self.store.pending(thread_id, exclude=tuple(self._offered.get(thread_id, ())))
            rows = tuple(row for row in rows if disposition(condition, row.envelope.delivery) in {"start", "steer"})
            rows = tuple(
                row
                for row in rows
                if row.envelope.sender.name != "browser.handback"
                or (self.app._browsers is not None and self.app._browsers.handback.available)
            )
            if thread_id == BOT_THREAD_ID:
                rows = await self.store.bot_batch(rows)
            if not rows:
                return
            content = tuple(part for row in rows for part in render_envelope(row.envelope))
            # Events are model context, never authored user input.
            content = tuple(
                ContentItem(part.value, part.metadata.model_copy(update={"display": False}))
                if (origin := input_source(part.metadata)) is not None and origin.kind == "event"
                else part
                for part in content
            )
            if thread_id == BOT_THREAD_ID and (error := (await self.app._store.bot.state()).last_error):
                content = (
                    ContentItem(
                        "A previous Bot input failed. Inspect the saved Bot Thread execution failure and working Thread outcomes before "
                        "continuing. This is a fresh opportunity, not a replay of that input or permission to repeat "
                        "uncertain tool effects.\n" + error,
                        ContentMetadata(display=False),
                    ),
                    *content,
                )
            app = self.app
            thread = await app._store.threads.get(thread_id)
            if thread is None:
                return

            def before_accept() -> None:
                if app._state.value != "ready":
                    raise RunCoordinationError("The App is stopping.", code="app_stopping")
                if any(row.envelope.sender.name == "browser.handback" for row in rows):
                    browser = app._browsers
                    if browser is None or not browser.handback.available or thread_id in browser.handback._suppressed:
                        raise RunCoordinationError("Browser handback was withdrawn.", code="inbox_held")

            async def authorize() -> None:
                before_accept()
                if await self.condition(thread_id) != "idle":
                    raise RunCoordinationError("Automatic input is held.", code="inbox_held")
                pending_ids = {row.envelope.id for row in await self.store.pending(thread_id)}
                if not ids.issubset(pending_ids):
                    raise RunCoordinationError("Automatic input was withdrawn.", code="inbox_held")

            ids = {row.envelope.id for row in rows}
            self._offered.setdefault(thread_id, set()).update(ids)
            accepted = False
            if thread.parent_thread_id is not None:
                accepted = await app._subagent_operator.deliver_input(thread_id, content, before_accept=before_accept)
            elif condition == "active":
                active = await app._root_runs.active(thread_id)
                if active is None:
                    return
                result = await app._root_runs.steer(
                    receipt_id=active.receipt.receipt_id, message=content, immediate=False, before_accept=before_accept
                )
                accepted = result.accepted
            elif thread_id == BOT_THREAD_ID:
                assert app._bot is not None
                accepted = await app._bot.admit_inputs(
                    content, tuple(row.envelope.id for row in rows), authorize=authorize
                )
            else:
                await app._root_runs.submit_prompt(
                    thread_id=thread_id, prompt=content, reject_deferred=True, authorize=authorize
                )
                accepted = True
            if accepted:
                self._dirty.add(thread_id)
            else:
                self._offered.get(thread_id, set()).difference_update(ids)
        except Exception:
            self._offered.get(thread_id, set()).difference_update(ids)
            get_logger(__name__).warning("Thread input delivery held: %s", thread_id, exc_info=True)
        finally:
            with CancelScope(shield=True):
                if not self._offered.get(thread_id):
                    await self.owners.release(thread_id)
                self._scheduled.discard(thread_id)
                if thread_id in self._dirty:
                    self._dirty.discard(thread_id)
                    self.schedule(thread_id)

    async def scan(self) -> None:
        after = ""
        while threads := await self.store.threads(after=after):
            for thread_id in threads:
                self.schedule(thread_id)
            after = threads[-1]

    async def maintain(self) -> None:
        while self._accepting:
            try:
                await self.scan()
            except Exception:
                get_logger(__name__).warning("Thread inbox scan failed", exc_info=True)
            await sleep(1)

    def stop(self) -> None:
        self._accepting = False

    async def close(self) -> None:
        self.stop()
        await self.owners.close()
