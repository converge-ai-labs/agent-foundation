"""Persistent multi-Thread Workbench screen."""

from __future__ import annotations

import hashlib

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, Input, ListItem, ListView, Static, TextArea

from a13n_ui.surfaces import NewThreadDefaults, RootOperationStatus, WorkbenchThreadView
from a13n_ui.tui.commands import match_slash_command
from a13n_ui.tui.intents import (
    AcknowledgeWorkbenchCompletion,
    ArchiveThread,
    CancelThreadOperation,
    CloseCompletions,
    EditDraft,
    ExecuteCommand,
    LoadMoreWorkbench,
    OpenFocus,
    OpenOverlay,
    SearchWorkbench,
    SelectWorkbenchThread,
    StartNewDraft,
    SubmitThreadDraft,
)
from a13n_ui.tui.models import DraftState, TerminalState
from a13n_ui.tui.widgets.composer import PromptTextArea, _location_for_offset, completion_request
from a13n_ui.tui.widgets.messages import IntentRequested

_TERMINAL_STATUSES = {
    RootOperationStatus.completed,
    RootOperationStatus.failed,
    RootOperationStatus.cancelled,
}


class WorkbenchScreen(Container):
    """Render attention-ranked root Threads with one bounded preview."""

    def __init__(self) -> None:
        super().__init__(id="workbench-screen")
        self._state: TerminalState | None = None
        self._rows: tuple[WorkbenchThreadView, ...] = ()
        self._row_ids: dict[str, str] = {}
        self._search_timer: Timer | None = None
        self._projecting = False
        self._editor_thread_id: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="workbench-header"):
            yield Input(placeholder="Search Threads", id="workbench-search")
            yield Button("Project", id="workbench-project")
            yield Button("Preview", id="workbench-view-toggle")
            yield Button("New", id="workbench-new", variant="primary")
        yield Static(id="workbench-render-error", markup=False)
        with Horizontal(id="workbench-main"):
            with VerticalScroll(id="workbench-list-pane"):
                yield ListView(id="workbench-list")
                yield Button("Load more", id="workbench-more")
            with VerticalScroll(id="workbench-preview"):
                yield Static(id="workbench-preview-title", markup=False)
                yield Static(id="workbench-preview-body", markup=False)
                with Horizontal(id="workbench-preview-actions"):
                    yield Button("Open", id="workbench-open", variant="primary")
                    yield Button("Cancel", id="workbench-cancel", variant="error")
                    yield Button("Archive", id="workbench-archive")
                yield Static(id="workbench-composer-label", markup=False)
                yield PromptTextArea(
                    placeholder="Prompt or steer selected Thread",
                    show_line_numbers=False,
                    id="workbench-editor",
                )
                yield Button("Send", id="workbench-submit", variant="primary")
        yield Static(id="workbench-footer", markup=False)

    async def project(self, state: TerminalState) -> None:
        self._state = state
        self.query_one("#workbench-render-error", Static).display = False
        self.query_one("#workbench-main").display = True
        page = state.workbench.page
        search = self.query_one("#workbench-search", Input)
        if not search.has_focus and search.value != state.workbench.query:
            self._projecting = True
            search.value = state.workbench.query
            self._projecting = False
        project_name = state.project_filter_id or "All Projects"
        project_button = self.query_one("#workbench-project", Button)
        project_button.label = project_name
        project_button.tooltip = "Filter by Project or show All Projects"
        new_project_id = state.launch_project_id or state.draft_defaults.project_id
        self.query_one("#workbench-new", Button).disabled = new_project_id is None

        rows = () if page is None else page.rows
        selected_id = state.workbench.selected_thread_id
        if rows != self._rows:
            await self._rebuild_rows(rows, selected_id)
        self.query_one("#workbench-more", Button).display = page is not None and page.next_cursor is not None
        self.query_one("#workbench-view-toggle", Button).disabled = selected_id is None
        if not rows:
            self.show_list(focus=False)
        total = 0 if page is None else page.total
        action = "open" if self.app.has_class("width-wide") else "preview"
        self.query_one("#workbench-footer", Static).update(
            f"{len(rows)} of {total} Thread(s) - Enter {action} - Ctrl+N new - Ctrl+O return to Focus"
        )
        self._project_preview(state, next((row for row in rows if row.thread.thread_id == selected_id), None))

    async def _rebuild_rows(
        self,
        rows: tuple[WorkbenchThreadView, ...],
        selected_id: str | None,
    ) -> None:
        list_view = self.query_one("#workbench-list", ListView)
        await list_view.clear()
        self._rows = rows
        self._row_ids.clear()
        items: list[ListItem] = []
        selected_index = 0
        for index, row in enumerate(rows):
            item_id = f"workbench-row-{hashlib.sha256(row.thread.thread_id.encode()).hexdigest()[:12]}"
            self._row_ids[item_id] = row.thread.thread_id
            items.append(ListItem(Static(_row_label(row), markup=False), id=item_id))
            if row.thread.thread_id == selected_id:
                selected_index = index
        if items:
            await list_view.extend(items)
            list_view.index = selected_index
        else:
            await list_view.append(ListItem(Static("No Threads match this view.", markup=False), disabled=True))

    def _project_preview(self, state: TerminalState, row: WorkbenchThreadView | None) -> None:
        title = self.query_one("#workbench-preview-title", Static)
        body = self.query_one("#workbench-preview-body", Static)
        editor = self.query_one("#workbench-editor", PromptTextArea)
        submit = self.query_one("#workbench-submit", Button)
        label = self.query_one("#workbench-composer-label", Static)
        open_button = self.query_one("#workbench-open", Button)
        cancel = self.query_one("#workbench-cancel", Button)
        archive = self.query_one("#workbench-archive", Button)
        if row is None:
            self._editor_thread_id = None
            with editor.prevent(TextArea.Changed, TextArea.SelectionChanged):
                editor.load_text("")
            title.update("No Thread selected")
            body.update("Choose a Thread to inspect its latest bounded activity.")
            editor.disabled = True
            submit.disabled = True
            open_button.disabled = True
            cancel.display = False
            archive.display = False
            return

        thread = row.thread
        title.update(thread.title or thread.thread_id)
        activity = row.latest_activity
        activity_text = "No retained activity" if activity is None else activity.text
        pending = (
            "None" if row.pending_decision is None else f"{row.pending_decision.kind} ({row.pending_decision.count})"
        )
        body.update(
            "\n".join(
                (
                    f"Project      {row.project_name}",
                    f"Agent        {row.agent_name}",
                    f"Environment  {row.environment_name}",
                    f"Activity     {activity_text}",
                    f"Decision     {pending}",
                    f"Children     {row.children.running} running, {row.children.failed} failed, "
                    f"{row.children.unavailable} unavailable",
                )
            )
        )
        open_button.disabled = False
        open_button.label = "Answer" if "respond" in row.available_actions else "Open"
        cancel.display = (
            "cancel" in thread.root_activity.available_actions and thread.root_activity.receipt_id is not None
        )
        archive.display = "archive" in row.available_actions
        draft = state.draft(thread.thread_id) or DraftState(key=thread.thread_id)
        if self._editor_thread_id != thread.thread_id or editor.text != draft.text:
            with editor.prevent(TextArea.Changed, TextArea.SelectionChanged):
                editor.load_text(draft.text)
                editor.cursor_location = _location_for_offset(draft.text, draft.cursor)
        self._editor_thread_id = thread.thread_id
        operation_busy = thread.root_activity.receipt_id is not None
        editor.disabled = "respond" in row.available_actions or (
            operation_busy and "steer" not in row.available_actions
        )
        if "steer" in row.available_actions:
            label.update("Steer active Run")
            submit.label = "Steer"
        elif "respond" in row.available_actions:
            label.update("Open Focus to answer the pending decision")
            submit.label = "Send"
        elif operation_busy:
            label.update("Current operation cannot accept input")
            submit.label = "Send"
        else:
            label.update("Send a new prompt")
            submit.label = "Send"
        submit.disabled = editor.disabled or not draft.text.strip()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None or event.item.id is None:
            return
        thread_id = self._row_ids.get(event.item.id)
        if thread_id is None:
            return
        self.post_message(IntentRequested(SelectWorkbenchThread(thread_id)))
        state = self._state
        if state is None:
            return
        row = next((item for item in state.workbench.rows if item.thread.thread_id == thread_id), None)
        operation = None if row is None else row.latest_operation
        if operation is not None and operation.status in _TERMINAL_STATUSES:
            self.post_message(IntentRequested(AcknowledgeWorkbenchCompletion(operation.receipt.receipt_id)))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id is None:
            return
        thread_id = self._row_ids.get(event.item.id)
        if thread_id is None:
            return
        if self.app.has_class("width-wide"):
            self.post_message(IntentRequested(OpenFocus(thread_id)))
        else:
            self.show_preview()

    def show_render_failure(self) -> None:
        error = self.query_one("#workbench-render-error", Static)
        error.display = True
        error.update("The Workbench surface could not be rendered safely. App-owned work continues.")
        self.query_one("#workbench-main").display = False

    @property
    def showing_preview(self) -> bool:
        return self.has_class("show-preview")

    def focus_initial(self) -> None:
        target = "#workbench-open" if self.showing_preview else "#workbench-list"
        self.query_one(target).focus()

    def show_preview(self) -> None:
        self.add_class("show-preview")
        self.query_one("#workbench-view-toggle", Button).label = "Threads"
        self.query_one("#workbench-open", Button).focus()

    def show_list(self, *, focus: bool = True) -> None:
        self.remove_class("show-preview")
        self.query_one("#workbench-view-toggle", Button).label = "Preview"
        if focus:
            self.query_one("#workbench-list", ListView).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._projecting or event.input.id != "workbench-search":
            return
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(
            0.2,
            lambda: self.post_message(IntentRequested(SearchWorkbench(event.value))),
        )

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self._projecting or event.text_area.id != "workbench-editor":
            return
        state = self._state
        thread_id = None if state is None else state.workbench.selected_thread_id
        if state is None or thread_id is None:
            return
        draft = state.draft(thread_id) or DraftState(key=thread_id)
        text = event.text_area.text
        row, column = event.text_area.cursor_location
        lines = text.split("\n")
        cursor = sum(len(line) + 1 for line in lines[:row]) + min(column, len(lines[row]))
        self.post_message(
            IntentRequested(
                EditDraft(
                    key=thread_id,
                    text=text,
                    cursor=cursor,
                    project_paths=draft.project_paths,
                    skill_references=draft.skill_references,
                )
            )
        )
        completion = completion_request(text, cursor=cursor, key=thread_id)
        self.post_message(IntentRequested(completion or CloseCompletions()))

    def on_prompt_text_area_submit(self, event: PromptTextArea.Submit) -> None:
        event.stop()
        self._submit()

    def _submit(self) -> None:
        state = self._state
        thread_id = None if state is None else state.workbench.selected_thread_id
        if thread_id is not None:
            draft = None if state is None else state.draft(thread_id)
            command = None if draft is None else match_slash_command(draft.text)
            if command is not None:
                self.post_message(IntentRequested(ExecuteCommand(command.name, draft_key=thread_id)))
            else:
                self.post_message(IntentRequested(SubmitThreadDraft(thread_id)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        state = self._state
        selected_id = None if state is None else state.workbench.selected_thread_id
        if event.button.id == "workbench-project":
            event.stop()
            self.post_message(IntentRequested(OpenOverlay("projects")))
        elif event.button.id == "workbench-view-toggle":
            event.stop()
            if self.showing_preview:
                self.show_list()
            elif selected_id is not None:
                self.show_preview()
        elif event.button.id == "workbench-new" and state is not None:
            project_id = state.launch_project_id or state.draft_defaults.project_id
            if project_id is None:
                return
            event.stop()
            defaults = NewThreadDefaults.model_validate({**state.draft_defaults.model_dump(), "project_id": project_id})
            self.post_message(IntentRequested(StartNewDraft(defaults)))
        elif event.button.id == "workbench-more":
            event.stop()
            self.post_message(IntentRequested(LoadMoreWorkbench()))
        elif event.button.id in {"workbench-open", "workbench-submit"} and selected_id is not None:
            event.stop()
            if event.button.id == "workbench-open":
                self.post_message(IntentRequested(OpenFocus(selected_id)))
            else:
                self._submit()
        elif event.button.id == "workbench-cancel" and state is not None and selected_id is not None:
            event.stop()
            row = next((item for item in state.workbench.rows if item.thread.thread_id == selected_id), None)
            if row is not None and row.thread.root_activity.receipt_id is not None:
                self.post_message(
                    IntentRequested(
                        CancelThreadOperation(
                            thread_id=selected_id,
                            receipt_id=row.thread.root_activity.receipt_id,
                        )
                    )
                )
        elif event.button.id == "workbench-archive" and state is not None and selected_id is not None:
            event.stop()
            row = next((item for item in state.workbench.rows if item.thread.thread_id == selected_id), None)
            if row is not None:
                self.post_message(
                    IntentRequested(
                        ArchiveThread(
                            thread_id=selected_id,
                            expected_version=row.thread.metadata_version,
                        )
                    )
                )


def _row_label(row: WorkbenchThreadView) -> Text:
    title = row.thread.title or row.thread.thread_id
    attention = "!" if row.pending_decision is not None or row.children.failed or row.children.lost else " "
    if row.thread.root_activity.receipt_id is not None:
        status = row.thread.root_activity.state.value
    elif row.latest_operation is not None:
        status = row.latest_operation.status.value
    elif row.pending_decision is not None:
        status = "awaiting decision"
    elif row.children.running:
        status = "child active" if row.children.active else "child unavailable"
    else:
        status = "idle"
    activity = "" if row.latest_activity is None else f" - {row.latest_activity.text}"
    return Text(f"{attention} {title}\n  {status}{activity}")


__all__ = ["WorkbenchScreen"]
