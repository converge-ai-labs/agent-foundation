"""Single-owner controller for App calls, subscriptions, and receipt correlation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from pathlib import Path
from typing import Protocol

from a13n_ui.errors import AgentUiError, LivePresentationError
from a13n_ui.live import LiveEvent, SummaryCursor, SummaryInvalidation, SummarySubscription
from a13n_ui.storage import ThreadConfigurationMutation
from a13n_ui.surfaces import (
    DecisionBatchView,
    DecisionResponseBatch,
    FailureView,
    LaunchProjectResolution,
    NewThreadDefaults,
    RootControlResult,
    RootOperationView,
    RootRunReceipt,
    SkillReference,
    ThreadConfigurationMutationInput,
    ThreadFocusSnapshot,
    ThreadMetadataMutation,
    ThreadMetadataPatch,
    ThreadSummary,
    TranscriptPage,
    WorkbenchPage,
)
from a13n_ui.tui.events import (
    ClosingStarted,
    CompletionAcknowledged,
    DraftChanged,
    DraftDefaultsChanged,
    FocusLoaded,
    FollowLatestChanged,
    LiveReceived,
    LiveUnavailable,
    OperationFailed,
    OverlayClosed,
    OverlayOpened,
    ReadingAnchorChanged,
    RootControlCompleted,
    RootOperationUpdated,
    RootReceiptAccepted,
    RouteChanged,
    StartupFailed,
    StartupReady,
    StartupStarted,
    TerminalEvent,
    TimelineSelectionChanged,
    TranscriptLoaded,
    WorkbenchLoaded,
    WorkbenchSelectionChanged,
    normalize_live_event,
)
from a13n_ui.tui.intents import (
    AcknowledgeWorkbenchCompletion,
    ArchiveThread,
    CancelFocusedOperation,
    CloseOverlay,
    EditDraft,
    ExitTerminal,
    LoadOlderTranscript,
    OpenExternalEditor,
    OpenFocus,
    OpenOverlay,
    OpenWorkbench,
    PatchThreadConfiguration,
    RetryStartup,
    SearchWorkbench,
    SelectTimelineBlock,
    SelectWorkbenchThread,
    SetFollowLatest,
    SetReadingAnchor,
    SetWorkbenchFilter,
    StartNewDraft,
    SubmitComposer,
    SubmitDecisions,
    TerminalIntent,
    ToggleTopLevelMode,
)
from a13n_ui.tui.models import (
    ControlMode,
    OverlayState,
    ReadingAnchor,
    TerminalLifecycle,
    TerminalMode,
    TerminalState,
)
from a13n_ui.tui.reducer import reduce_terminal
from a13n_ui.tui.scheduler import ProjectionScheduler, RenderCallback


class FocusWatchProtocol(Protocol):
    @property
    def snapshot(self) -> ThreadFocusSnapshot: ...

    @property
    def events(self) -> AsyncIterator[LiveEvent]: ...


class TerminalAppProtocol(Protocol):
    async def resolve_launch_project(
        self,
        directory: Path,
        *,
        project_id: str | None = None,
    ) -> LaunchProjectResolution: ...

    async def workbench(
        self,
        *,
        project_id: str | None,
        query: str | None = None,
        include_archived: bool = False,
        cursor: str | None = None,
        limit: int = 20,
    ) -> WorkbenchPage: ...

    def summary_events(
        self,
        *,
        after: SummaryCursor | None = None,
    ) -> AbstractAsyncContextManager[SummarySubscription]: ...

    def watch_thread(
        self,
        *,
        root_thread_id: str,
        child_limit: int = 20,
    ) -> AbstractAsyncContextManager[FocusWatchProtocol]: ...

    async def thread_decisions(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
    ) -> DecisionBatchView | None: ...

    async def get_thread_transcript(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> TranscriptPage: ...

    async def create_thread(
        self,
        *,
        defaults: NewThreadDefaults | None = None,
        title: str | None = None,
    ) -> ThreadSummary: ...

    async def submit_thread(
        self,
        *,
        thread_id: str,
        prompt: str,
        mutation: ThreadConfigurationMutation | None = None,
        skill_references: tuple[SkillReference, ...] = (),
    ) -> RootRunReceipt: ...

    async def respond_decisions(
        self,
        *,
        thread_id: str,
        response: DecisionResponseBatch,
        mutation: ThreadConfigurationMutation | None = None,
    ) -> RootRunReceipt: ...

    async def wait_root_operation(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> RootOperationView: ...

    async def steer_root_operation(
        self,
        *,
        receipt_id: str,
        message: str,
        skill_references: tuple[SkillReference, ...] = (),
    ) -> RootControlResult: ...

    async def cancel_root_operation(self, receipt_id: str) -> RootControlResult: ...

    async def patch_thread_configuration(
        self,
        *,
        thread_id: str,
        mutation: ThreadConfigurationMutationInput,
    ) -> ThreadSummary: ...

    async def update_thread_metadata(
        self,
        *,
        thread_id: str,
        mutation: ThreadMetadataMutation,
    ) -> ThreadSummary: ...


type AppContextFactory = Callable[[], AbstractAsyncContextManager[TerminalAppProtocol]]
type ExitCallback = Callable[[], Awaitable[None]]


class TerminalController:
    """Own every App interaction and reduce results into one terminal state."""

    def __init__(
        self,
        *,
        app_factory: AppContextFactory,
        render: RenderCallback,
        launch_directory: Path,
        launch_thread_id: str | None = None,
        launch_defaults: NewThreadDefaults | None = None,
        open_workbench: bool = False,
        exit_callback: ExitCallback | None = None,
    ) -> None:
        self._app_factory = app_factory
        self._launch_directory = launch_directory
        self._launch_thread_id = launch_thread_id
        self._launch_defaults = launch_defaults or NewThreadDefaults()
        self._open_workbench = open_workbench
        self._exit_callback = exit_callback
        self._state = TerminalState(draft_defaults=self._launch_defaults)
        self._scheduler = ProjectionScheduler(render)
        self._state_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._focus_lock = asyncio.Lock()
        self._app: TerminalAppProtocol | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._lifetime_task: asyncio.Task[None] | None = None
        self._startup_future: asyncio.Future[None] | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._summary_task: asyncio.Task[None] | None = None
        self._focus_task: asyncio.Task[None] | None = None
        self._request_version = 0
        self._closed = False

    @property
    def state(self) -> TerminalState:
        return self._state

    @property
    def background_task_count(self) -> int:
        count = sum(1 for task in self._tasks if not task.done())
        if self._lifetime_task is not None and not self._lifetime_task.done():
            count += 1
        return count

    async def start(self) -> None:
        while True:
            wait_for_previous: asyncio.Task[None] | None = None
            async with self._lifecycle_lock:
                if self._closed:
                    return
                task = self._lifetime_task
                started = self._startup_future
                if task is None or task.done():
                    started = asyncio.get_running_loop().create_future()
                    shutdown = asyncio.Event()
                    task = asyncio.create_task(
                        self._run_app_lifetime(started, shutdown),
                        name="terminal-app-lifetime",
                    )
                    self._lifetime_task = task
                    self._startup_future = started
                    self._shutdown_event = shutdown
                elif self._state.lifecycle is TerminalLifecycle.STARTING and started is not None and started.done():
                    wait_for_previous = task
            if wait_for_previous is not None:
                await asyncio.shield(wait_for_previous)
                continue
            assert started is not None
            await asyncio.shield(started)
            return

    async def _run_app_lifetime(
        self,
        started: asyncio.Future[None],
        shutdown: asyncio.Event,
    ) -> None:
        stack = AsyncExitStack()
        summary_gate = asyncio.Event()
        summary_ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        try:
            app = await stack.enter_async_context(self._app_factory())
            self._app = app
            self._summary_task = self._spawn(
                self._run_summary(app, summary_ready, summary_gate),
                name="terminal-summary",
            )
            await summary_ready
            launch = await app.resolve_launch_project(
                self._launch_directory,
                project_id=self._launch_defaults.project_id,
            )
            project_id = launch.project.project_id if launch.kind == "selected" else None
            page = await app.workbench(project_id=project_id)
            await self._dispatch(
                StartupReady(
                    launch=launch,
                    workbench=page,
                    explicit_thread_id=self._launch_thread_id,
                    open_workbench=self._open_workbench,
                ),
                immediate=True,
            )
            summary_gate.set()
            if self._launch_thread_id is not None:
                await self._replace_focus(self._launch_thread_id)
            if not started.done():
                started.set_result(None)
            await shutdown.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._dispatch(StartupFailed(_failure(exc)), immediate=True)
        finally:
            summary_gate.set()
            if not started.done():
                started.set_result(None)
            await self._close_app_lifetime(stack)

    async def retry_startup(self) -> None:
        if self._state.lifecycle is not TerminalLifecycle.FAILED:
            return
        await self._dispatch(StartupStarted(), immediate=True)
        await self.start()

    async def handle(self, intent: TerminalIntent) -> None:
        if isinstance(intent, RetryStartup):
            await self.retry_startup()
        elif isinstance(intent, ExitTerminal):
            await self._dispatch(ClosingStarted(), immediate=True)
            if self._exit_callback is not None:
                await self._exit_callback()
        elif isinstance(intent, ToggleTopLevelMode):
            if self._state.mode is TerminalMode.FOCUS:
                await self._dispatch(RouteChanged(mode="workbench"), immediate=True)
            elif self._state.previous_focused_thread_id is not None:
                await self._replace_focus(self._state.previous_focused_thread_id)
        elif isinstance(intent, OpenWorkbench):
            await self._dispatch(RouteChanged(mode="workbench"), immediate=True)
        elif isinstance(intent, OpenFocus):
            await self._replace_focus(intent.thread_id)
        elif isinstance(intent, StartNewDraft):
            if intent.defaults is not None:
                self._launch_defaults = intent.defaults
                await self._dispatch(DraftDefaultsChanged(intent.defaults), immediate=True)
            await self._stop_focus()
            await self._dispatch(RouteChanged(mode="focus", new_draft=True), immediate=True)
        elif isinstance(intent, SelectWorkbenchThread):
            await self._dispatch(WorkbenchSelectionChanged(intent.thread_id))
        elif isinstance(intent, SetWorkbenchFilter):
            await self._refresh_workbench(
                project_id=intent.project_id,
                replace_project=True,
                query=self._state.workbench.query,
            )
        elif isinstance(intent, SearchWorkbench):
            await self._refresh_workbench(
                project_id=self._state.project_filter_id,
                query=intent.query,
            )
        elif isinstance(intent, LoadOlderTranscript):
            await self._load_older(intent.thread_id)
        elif isinstance(intent, EditDraft):
            await self._dispatch(
                DraftChanged(
                    key=intent.key,
                    text=intent.text,
                    cursor=intent.cursor,
                    project_paths=intent.project_paths,
                    skill_references=intent.skill_references,
                )
            )
        elif isinstance(intent, SubmitComposer):
            await self._submit_composer(intent.key)
        elif isinstance(intent, CancelFocusedOperation):
            await self._cancel_focused()
        elif isinstance(intent, SubmitDecisions):
            await self._submit_decisions(intent)
        elif isinstance(intent, PatchThreadConfiguration):
            await self._patch_configuration(intent)
        elif isinstance(intent, ArchiveThread):
            await self._archive(intent)
        elif isinstance(intent, OpenOverlay):
            await self._dispatch(OverlayOpened(OverlayState(kind=intent.kind, key=intent.key)), immediate=True)
        elif isinstance(intent, CloseOverlay):
            await self._dispatch(OverlayClosed(), immediate=True)
        elif isinstance(intent, SelectTimelineBlock):
            await self._dispatch(TimelineSelectionChanged(intent.thread_id, intent.block_id))
        elif isinstance(intent, SetFollowLatest):
            await self._dispatch(FollowLatestChanged(intent.thread_id, intent.enabled))
        elif isinstance(intent, SetReadingAnchor):
            await self._dispatch(
                ReadingAnchorChanged(
                    intent.thread_id,
                    ReadingAnchor(intent.block_id, intent.line_offset),
                )
            )
        elif isinstance(intent, AcknowledgeWorkbenchCompletion):
            await self._dispatch(CompletionAcknowledged(intent.receipt_id))
        elif isinstance(intent, OpenExternalEditor):
            await self._dispatch(
                OperationFailed(
                    action="editor",
                    failure=FailureView(
                        code="editor_not_ready",
                        message="External editor integration is not available yet.",
                    ),
                    draft_key=intent.key,
                )
            )
        else:
            raise TypeError(f"Unsupported terminal intent: {type(intent).__name__}")

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            was_starting = self._state.lifecycle is TerminalLifecycle.STARTING
            self._closed = True
            async with self._state_lock:
                self._state = reduce_terminal(self._state, ClosingStarted()).state
            task = self._lifetime_task
            shutdown = self._shutdown_event
            if shutdown is not None:
                shutdown.set()
            if was_starting and task is not None:
                task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await self._scheduler.close()

    async def _close_app_lifetime(self, stack: AsyncExitStack) -> None:
        await self._stop_focus()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._app = None
        self._summary_task = None
        await stack.aclose()

    async def _run_summary(
        self,
        app: TerminalAppProtocol,
        ready: asyncio.Future[None],
        startup_gate: asyncio.Event,
    ) -> None:
        refresh_after_subscribe = False
        try:
            while not self._closed:
                try:
                    async with app.summary_events() as subscription:
                        if not ready.done():
                            ready.set_result(None)
                        await startup_gate.wait()
                        if refresh_after_subscribe:
                            await self._refresh_after_summary_gap()
                            refresh_after_subscribe = False
                        async for invalidation in subscription:
                            await self._handle_invalidation(invalidation)
                        return
                except LivePresentationError as exc:
                    if not ready.done():
                        ready.set_exception(exc)
                        return
                    refresh_after_subscribe = True
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
                return
            await self._dispatch(
                OperationFailed(action="summary", failure=_failure(exc)),
                immediate=True,
            )

    async def _refresh_after_summary_gap(self) -> None:
        await self._refresh_workbench()
        focused = self._state.focused_thread_id
        if focused is not None:
            await self._replace_focus(focused)

    async def _handle_invalidation(self, invalidation: SummaryInvalidation) -> None:
        await self._refresh_workbench()
        focused = self._state.focused_thread_id
        if (
            focused is not None
            and invalidation.root_thread_id in {None, focused}
            and invalidation.kind in {"thread", "root_operation", "child_execution", "configuration"}
        ):
            await self._replace_focus(focused)

    async def _refresh_workbench(
        self,
        *,
        project_id: str | None = None,
        replace_project: bool = False,
        query: str | None = None,
    ) -> None:
        app = self._app
        if app is None:
            return
        selected_project = project_id if replace_project else self._state.project_filter_id
        selected_query = self._state.workbench.query if query is None else query
        version = self._next_version()
        try:
            page = await app.workbench(project_id=selected_project, query=selected_query or None)
        except Exception as exc:
            await self._dispatch(OperationFailed(action="workbench", failure=_failure(exc)))
            return
        await self._dispatch(
            WorkbenchLoaded(
                request_version=version,
                page=page,
                query=selected_query,
            )
        )

    async def _replace_focus(self, thread_id: str) -> None:
        async with self._focus_lock:
            await self._stop_focus_locked()
            await self._dispatch(RouteChanged(mode="focus", thread_id=thread_id), immediate=True)
            task = self._spawn(self._run_focus(thread_id), name=f"terminal-focus-{thread_id}")
            self._focus_task = task

    async def _stop_focus(self) -> None:
        async with self._focus_lock:
            await self._stop_focus_locked()

    async def _stop_focus_locked(self) -> None:
        task = self._focus_task
        self._focus_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run_focus(self, thread_id: str) -> None:
        app = self._app
        if app is None:
            return
        while not self._closed and self._state.focused_thread_id == thread_id:
            version = self._next_version()
            try:
                async with app.watch_thread(root_thread_id=thread_id) as watch:
                    continuation = watch.snapshot.thread.continuation_id
                    decisions = await app.thread_decisions(
                        thread_id=thread_id,
                        expected_continuation_id=continuation,
                    )
                    await self._dispatch(
                        FocusLoaded(
                            request_version=version,
                            snapshot=watch.snapshot,
                            decisions=decisions,
                        ),
                        immediate=True,
                    )
                    transcript = await app.get_thread_transcript(
                        thread_id=thread_id,
                        expected_continuation_id=continuation,
                    )
                    await self._dispatch(
                        TranscriptLoaded(
                            request_version=version,
                            thread_id=thread_id,
                            page=transcript,
                        ),
                        immediate=True,
                    )
                    async for raw in watch.events:
                        await self._dispatch(LiveReceived(normalize_live_event(raw)))
                return
            except asyncio.CancelledError:
                raise
            except LivePresentationError as exc:
                await self._dispatch(
                    LiveUnavailable(thread_id=thread_id, failure=_failure(exc)),
                    immediate=True,
                )
                await asyncio.sleep(0.1)
            except AgentUiError as exc:
                await self._dispatch(
                    OperationFailed(
                        action="focus",
                        failure=_failure(exc),
                        thread_id=thread_id,
                    ),
                    immediate=True,
                )
                return
            except Exception as exc:
                await self._dispatch(
                    OperationFailed(
                        action="focus",
                        failure=_failure(exc),
                        thread_id=thread_id,
                    ),
                    immediate=True,
                )
                return

    async def _load_older(self, thread_id: str) -> None:
        app = self._app
        view = self._state.thread_view(thread_id)
        if app is None or view is None or view.older_cursor is None:
            return
        version = view.projection_version
        try:
            page = await app.get_thread_transcript(
                thread_id=thread_id,
                expected_continuation_id=view.transcript_continuation_id,
                cursor=view.older_cursor,
            )
        except Exception as exc:
            await self._dispatch(OperationFailed(action="history", failure=_failure(exc), thread_id=thread_id))
            return
        await self._dispatch(
            TranscriptLoaded(
                request_version=version,
                thread_id=thread_id,
                page=page,
                prepend=True,
            )
        )

    async def _submit_composer(self, key: str) -> None:
        app = self._app
        draft = self._state.draft(key)
        if app is None or draft is None or not draft.text.strip():
            return
        text = draft.text
        thread_id = self._state.focused_thread_id
        view = None if thread_id is None else self._state.thread_view(thread_id)
        mode = (
            ControlMode.DRAFT if thread_id is None else (ControlMode.UNAVAILABLE if view is None else view.control_mode)
        )
        try:
            if mode is ControlMode.DRAFT:
                created = await app.create_thread(defaults=self._state.draft_defaults)
                thread_id = created.thread_id
                await self._replace_focus(thread_id)
                receipt = await app.submit_thread(
                    thread_id=thread_id,
                    prompt=text,
                    skill_references=draft.skill_references,
                )
                await self._dispatch(
                    RootReceiptAccepted(
                        receipt=receipt,
                        draft_key=key,
                        submitted_text=text,
                    ),
                    immediate=True,
                )
                self._spawn(self._wait_receipt(receipt), name=f"terminal-receipt-{receipt.receipt_id}")
            elif mode is ControlMode.IDLE and thread_id is not None:
                receipt = await app.submit_thread(
                    thread_id=thread_id,
                    prompt=text,
                    skill_references=draft.skill_references,
                )
                await self._dispatch(
                    RootReceiptAccepted(
                        receipt=receipt,
                        draft_key=key,
                        submitted_text=text,
                    ),
                    immediate=True,
                )
                self._spawn(self._wait_receipt(receipt), name=f"terminal-receipt-{receipt.receipt_id}")
            elif mode is ControlMode.RUNNING and view is not None and view.root_operation is not None:
                result = await app.steer_root_operation(
                    receipt_id=view.root_operation.receipt.receipt_id,
                    message=text,
                    skill_references=draft.skill_references,
                )
                await self._dispatch(
                    RootControlCompleted(result=result, action="steer", draft_key=key),
                    immediate=True,
                )
                if not result.accepted:
                    raise AgentUiError(
                        "The active Run completed before steering was accepted.",
                        code="steering_not_accepted",
                    )
        except Exception as exc:
            await self._dispatch(
                OperationFailed(
                    action="submit",
                    failure=_failure(exc),
                    draft_key=key,
                    draft_text=text,
                    thread_id=thread_id,
                ),
                immediate=True,
            )

    async def _wait_receipt(self, receipt: RootRunReceipt) -> None:
        app = self._app
        if app is None:
            return
        try:
            operation = await app.wait_root_operation(receipt.receipt_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._dispatch(
                OperationFailed(
                    action="wait",
                    failure=_failure(exc),
                    thread_id=receipt.thread_id,
                )
            )
            return
        await self._dispatch(RootOperationUpdated(operation), immediate=True)
        if self._state.focused_thread_id == receipt.thread_id:
            await self._replace_focus(receipt.thread_id)

    async def _cancel_focused(self) -> None:
        app = self._app
        thread_id = self._state.focused_thread_id
        view = None if thread_id is None else self._state.thread_view(thread_id)
        if app is None or view is None or view.root_operation is None:
            return
        receipt_id = view.root_operation.receipt.receipt_id
        try:
            result = await app.cancel_root_operation(receipt_id)
        except Exception as exc:
            await self._dispatch(OperationFailed(action="cancel", failure=_failure(exc), thread_id=thread_id))
            return
        await self._dispatch(RootControlCompleted(result=result, action="cancel"), immediate=True)

    async def _submit_decisions(self, intent: SubmitDecisions) -> None:
        app = self._app
        if app is None:
            return
        try:
            receipt = await app.respond_decisions(thread_id=intent.thread_id, response=intent.response)
        except Exception as exc:
            await self._dispatch(
                OperationFailed(action="decision", failure=_failure(exc), thread_id=intent.thread_id),
                immediate=True,
            )
            return
        await self._dispatch(
            RootReceiptAccepted(
                receipt=receipt,
                draft_key=intent.thread_id,
                submitted_text="Decision response",
            ),
            immediate=True,
        )
        self._spawn(self._wait_receipt(receipt), name=f"terminal-receipt-{receipt.receipt_id}")

    async def _patch_configuration(self, intent: PatchThreadConfiguration) -> None:
        app = self._app
        if app is None:
            return
        try:
            await app.patch_thread_configuration(thread_id=intent.thread_id, mutation=intent.mutation)
        except Exception as exc:
            await self._dispatch(
                OperationFailed(action="configuration", failure=_failure(exc), thread_id=intent.thread_id),
                immediate=True,
            )
            return
        await self._replace_focus(intent.thread_id)

    async def _archive(self, intent: ArchiveThread) -> None:
        app = self._app
        if app is None:
            return
        try:
            await app.update_thread_metadata(
                thread_id=intent.thread_id,
                mutation=ThreadMetadataMutation(
                    expected_version=intent.expected_version,
                    patch=ThreadMetadataPatch(archived=intent.archived),
                ),
            )
        except Exception as exc:
            await self._dispatch(OperationFailed(action="archive", failure=_failure(exc)))
            return
        await self._refresh_workbench()

    async def _dispatch(self, event: TerminalEvent, *, immediate: bool = False) -> None:
        async with self._state_lock:
            reduction = reduce_terminal(self._state, event)
            self._state = reduction.state
        await self._scheduler.submit(self._state, reduction.hints, immediate=immediate)

    def _spawn(self, awaitable: Coroutine[object, object, None], *, name: str) -> asyncio.Task[None]:
        task = asyncio.create_task(awaitable, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _next_version(self) -> int:
        self._request_version += 1
        return self._request_version


def _failure(exc: BaseException) -> FailureView:
    if isinstance(exc, AgentUiError):
        return FailureView(code=exc.code, message=str(exc) or exc.code)
    if isinstance(exc, asyncio.CancelledError):
        return FailureView(code="operation_cancelled", message="The terminal operation was cancelled.")
    return FailureView(code="terminal_operation_failed", message=str(exc) or type(exc).__name__)


__all__ = ["AppContextFactory", "TerminalAppProtocol", "TerminalController"]
