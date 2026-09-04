"""Command, selector, status, Skill, and Thread-picker overlays."""

from __future__ import annotations

import hashlib
from typing import Literal

from textual.app import ComposeResult
from textual.containers import Container
from textual.timer import Timer
from textual.widgets import Button, Input, ListItem, ListView, Static

from a13n_ui.surfaces import RootOperationStatus, SelectableResourceSummary, SkillReference
from a13n_ui.tui.commands import COMMANDS, command_available
from a13n_ui.tui.intents import (
    CloseOverlay,
    ExecuteCommand,
    ExitTerminal,
    InsertSkillReference,
    OpenFocus,
    OpenOverlay,
    SearchThreadPicker,
    SelectConfigurationResource,
    SetWorkbenchFilter,
    TerminalIntent,
)
from a13n_ui.tui.models import (
    ConfigurationResourceKind,
    ControlMode,
    OverlayState,
    TerminalState,
)
from a13n_ui.tui.widgets.messages import IntentRequested

type ResourceKind = Literal[
    "harness_plugin",
    "environment_run_extension",
    "mcp_server",
]


class OverlayPane(Container):
    """Render one bounded transient workflow over persistent Focus/Workbench state."""

    def __init__(self) -> None:
        super().__init__(id="overlay-pane")
        self._state: TerminalState | None = None
        self._overlay: OverlayState | None = None
        self._query = ""
        self._actions: dict[str, TerminalIntent] = {}
        self._thread_search_timer: Timer | None = None
        self._projecting = False

    def compose(self) -> ComposeResult:
        yield Static("", id="overlay-title")
        yield Input(placeholder="Filter", id="overlay-search")
        yield Button("Scope", id="overlay-scope")
        yield Static(id="overlay-body")
        yield ListView(id="overlay-list")
        yield Button("Exit and stop work", id="overlay-confirm-exit", variant="error")
        yield Button("Back", id="overlay-close", variant="primary")

    async def project(self, state: TerminalState) -> None:
        overlay = state.overlays[-1] if state.overlays else None
        if overlay is None:
            return
        if self._overlay != overlay:
            self._query = state.thread_picker_query if overlay.kind == "threads" else ""
            self._overlay = overlay
        self._state = state
        search = self.query_one("#overlay-search", Input)
        if not search.has_focus and search.value != self._query:
            self._projecting = True
            search.value = self._query
            self._projecting = False
        await self._rebuild()

    def show_render_failure(self) -> None:
        self.query_one("#overlay-title", Static).update("Surface unavailable")
        body = self.query_one("#overlay-body", Static)
        body.display = True
        body.update("This surface could not be rendered safely. No action was taken.")
        self.query_one("#overlay-search", Input).display = False
        self.query_one("#overlay-scope", Button).display = False
        self.query_one("#overlay-list", ListView).display = False
        self.query_one("#overlay-confirm-exit", Button).display = False
        close = self.query_one("#overlay-close", Button)
        close.label = "Back"
        close.focus()

    def focus_initial(self) -> None:
        search = self.query_one("#overlay-search", Input)
        if search.display:
            search.focus()
            return
        list_view = self.query_one("#overlay-list", ListView)
        if list_view.display:
            list_view.focus()
            return
        self.query_one("#overlay-close", Button).focus()

    async def _rebuild(self) -> None:
        state = self._state
        overlay = self._overlay
        if state is None or overlay is None:
            return
        title = self.query_one("#overlay-title", Static)
        body = self.query_one("#overlay-body", Static)
        search = self.query_one("#overlay-search", Input)
        scope = self.query_one("#overlay-scope", Button)
        list_view = self.query_one("#overlay-list", ListView)
        confirm_exit = self.query_one("#overlay-confirm-exit", Button)
        self._actions.clear()
        await list_view.clear()
        body.display = False
        body.update("")
        search.display = overlay.kind not in {"status", "help", "inspector", "exit"}
        scope.display = overlay.kind == "threads"
        scope.label = f"Project: {state.project_filter_id or 'All Projects'}"
        list_view.display = overlay.kind not in {"status", "help", "inspector", "exit"}
        confirm_exit.display = overlay.kind == "exit"
        self.query_one("#overlay-close", Button).label = "Keep running" if overlay.kind == "exit" else "Back"

        entries: list[tuple[str, str, TerminalIntent, bool]] = []
        query = self._query.casefold()
        if overlay.kind == "commands":
            title.update("Command palette")
            entries = [
                (
                    command.slash,
                    command.description,
                    ExecuteCommand(command.name, context_key=overlay.context_key),
                    command_available(command, state, context_key=overlay.context_key),
                )
                for command in COMMANDS
                if query in command.name.casefold() or query in command.description.casefold()
            ]
        elif overlay.kind == "threads":
            title.update("Open Thread")
            page = state.thread_picker
            entries = (
                []
                if page is None
                else [
                    (
                        row.thread.title or row.thread.thread_id,
                        f"{row.project_name} - {row.agent_name}",
                        OpenFocus(row.thread.thread_id),
                        True,
                    )
                    for row in page.rows
                ]
            )
        elif overlay.kind == "skills":
            title.update("Effective Skills")
            catalog = state.skill_catalog
            key = overlay.context_key or "new"
            entries = (
                []
                if catalog is None
                else [
                    (
                        f"${item.name}",
                        item.description,
                        InsertSkillReference(
                            key=key,
                            reference=SkillReference(
                                catalog_id=catalog.catalog_id,
                                item_id=item.item_id,
                                name=item.name,
                            ),
                        ),
                        True,
                    )
                    for item in catalog.items
                    if query in item.name.casefold() or query in item.description.casefold()
                ]
            )
        elif overlay.kind == "projects":
            title.update("Project filter")
            entries = [("All Projects", "Show every configured Project", SetWorkbenchFilter(None), True)]
            entries.extend(
                (
                    project.name,
                    " | ".join(project.roots),
                    SetWorkbenchFilter(project.project_id),
                    True,
                )
                for project in state.projects
                if query in project.name.casefold() or query in project.project_id.casefold()
            )
        elif overlay.kind == "configuration":
            title.update(_configuration_title(overlay.key))
            body.display = True
            body.update(_configuration_context(state, overlay.context_key))
            entries = _configuration_entries(state, overlay.key, overlay.context_key, query)
        elif overlay.kind == "status":
            title.update("Status")
            body.display = True
            body.update(_status_text(state, overlay.context_key))
        elif overlay.kind == "help":
            title.update("Terminal help")
            body.display = True
            body.update(_help_text())
        elif overlay.kind == "inspector":
            title.update("Thread inspector")
            body.display = True
            body.update(_inspector_text(state, overlay.context_key))
        elif overlay.kind == "exit":
            title.update("Exit Agent UI?")
            body.display = True
            body.update(
                f"Active root operations: {overlay.active_root_operations}\n"
                f"Active child executions: {overlay.active_child_executions}\n\n"
                "This work is process-local and will not continue after Agent UI shuts down. "
                "Exit requests cooperative cancellation and begins bounded App cleanup."
            )
        else:
            title.update(overlay.kind.title())
            body.display = True
            body.update("This surface has no additional content.")

        items: list[ListItem] = []
        for index, (label, description, action, enabled) in enumerate(entries):
            item_id = f"overlay-item-{index}-{hashlib.sha256(label.encode()).hexdigest()[:10]}"
            self._actions[item_id] = action
            items.append(
                ListItem(
                    Static(f"{label}\n  {description}"),
                    id=item_id,
                    disabled=not enabled,
                )
            )
        if items:
            await list_view.extend(items)
        elif list_view.display:
            await list_view.append(ListItem(Static("No matching items."), disabled=True))

    async def on_input_changed(self, event: Input.Changed) -> None:
        if self._projecting or event.input.id != "overlay-search":
            return
        self._query = event.value
        if self._overlay is not None and self._overlay.kind == "threads":
            if self._thread_search_timer is not None:
                self._thread_search_timer.stop()
            self._thread_search_timer = self.set_timer(
                0.2,
                lambda: self.post_message(IntentRequested(SearchThreadPicker(self._query))),
            )
        else:
            await self._rebuild()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id is None:
            return
        action = self._actions.get(item_id)
        if action is not None:
            self.post_message(IntentRequested(action))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "overlay-close":
            event.stop()
            self.post_message(IntentRequested(CloseOverlay()))
        elif event.button.id == "overlay-confirm-exit":
            event.stop()
            self.post_message(IntentRequested(ExitTerminal(confirmed=True)))
        elif event.button.id == "overlay-scope":
            event.stop()
            self.post_message(IntentRequested(OpenOverlay("projects")))


def _inspector_text(state: TerminalState, thread_id: str | None) -> str:
    selected_id = thread_id or state.focused_thread_id
    view = None if selected_id is None else state.thread_view(selected_id)
    if view is None or view.detail is None:
        return "Focused Thread details are unavailable."

    configuration = view.detail.thread.configuration
    lines = [
        "CONTEXT",
        f"Thread  {view.thread_id}",
        f"Project {configuration.project_id}",
        f"Agent   {configuration.agent_source.id}",
        f"Env     {configuration.environment_profile_id}",
        f"Config  v{configuration.version}",
        "",
        "SELECTION",
    ]
    selected = next((item for item in view.timeline if item.block_id == view.selected_block_id), None)
    if selected is None:
        lines.append("Focus a timeline block for details")
    else:
        lines.extend((f"Kind    {selected.kind.value}", f"Status  {selected.status.value}"))
        if selected.summary:
            lines.append(selected.summary)

    lines.extend(("", "TASKS"))
    if not view.tasks.available:
        lines.append("Task state unavailable")
    elif not view.tasks.tasks:
        lines.append("No tasks")
    else:
        marker = {"pending": "[ ]", "in_progress": "[*]", "completed": "[x]"}
        lines.extend(f"{marker[item.status]} {item.subject}" for item in view.tasks.tasks[:20])
        if view.tasks.omitted:
            lines.append(f"... {view.tasks.omitted} omitted")

    lines.extend(("", "CHILDREN"))
    children = () if view.snapshot is None else view.snapshot.children.executions
    if not children:
        lines.append("No child executions")
    else:
        for child in children[:20]:
            status = (
                "unavailable"
                if child.persisted_status == "running" and child.local_status == "unavailable"
                else child.persisted_status
            )
            lines.append(f"{child.subagent_name}  {status}")
        assert view.snapshot is not None
        omitted = view.snapshot.children.total - len(children)
        if omitted > 0:
            lines.append(f"... {omitted} omitted")
    return "\n".join(lines)


def _configuration_title(key: str | None) -> str:
    if key == "agent":
        return "Select Agent"
    if key == "environment":
        return "Select Environment"
    if key == "extensions":
        return "Select extensions"
    return "Thread configuration"


def _configuration_context(state: TerminalState, context_key: str | None) -> str:
    view = None
    row = None
    if context_key is not None and context_key != "new":
        view = state.thread_view(context_key)
    configuration = None if view is None or view.detail is None else view.detail.thread.configuration
    if configuration is None and context_key is not None and context_key != "new":
        row = next(
            (item for item in state.workbench.rows if item.thread.thread_id == context_key),
            None,
        )
        configuration = None if row is None else row.thread.configuration
    project_id = state.draft_defaults.project_id if configuration is None else configuration.project_id
    agent_id = state.draft_defaults.agent_id if configuration is None else configuration.agent_source.id
    model_id = "unavailable"
    if state.selectors is not None:
        agent = next((item for item in state.selectors.agents if item.agent_id == agent_id), None)
        if agent is not None:
            model_id = agent.model_id
    lines = [
        f"Project: {project_id or 'unmatched'} (read-only)",
        f"Resolved Model: {model_id} (read-only)",
    ]
    active_view = view is not None and view.control_mode in {
        ControlMode.PREPARING,
        ControlMode.RUNNING,
        ControlMode.AWAITING_DECISION,
        ControlMode.CANCELLING,
    }
    active_row = (
        row is not None
        and row.latest_operation is not None
        and row.latest_operation.status
        not in {
            RootOperationStatus.completed,
            RootOperationStatus.failed,
            RootOperationStatus.cancelled,
        }
    )
    if active_view or active_row:
        lines.append("Changes apply to the next Run; the active Run keeps its captured configuration.")
    conflict = state.configuration_conflict
    if conflict is not None and conflict.thread_id == context_key:
        lines.append(conflict.message)
    return "\n".join(lines)


def _configuration_entries(
    state: TerminalState,
    key: str | None,
    context_key: str | None,
    query: str,
) -> list[tuple[str, str, TerminalIntent, bool]]:
    selectors = state.selectors
    if selectors is None:
        return []
    configuration = None
    if context_key is not None and context_key != "new":
        view = state.thread_view(context_key)
        if view is not None and view.detail is not None:
            configuration = view.detail.thread.configuration
        else:
            row = next(
                (item for item in state.workbench.rows if item.thread.thread_id == context_key),
                None,
            )
            configuration = None if row is None else row.thread.configuration
    entries: list[tuple[str, str, TerminalIntent, bool]] = []
    if key == "agent":
        selected = state.draft_defaults.agent_id if configuration is None else configuration.agent_source.id
        for item in selectors.agents:
            if query in item.name.casefold() or query in item.agent_id.casefold():
                marker = _configuration_marker(
                    state,
                    "agent",
                    item.agent_id,
                    selected=item.agent_id == selected,
                )
                entries.append(
                    (
                        f"{marker} {item.name}",
                        f"{item.model_id} - {item.source_path}",
                        SelectConfigurationResource("agent", item.agent_id),
                        True,
                    )
                )
    elif key == "environment":
        selected = (
            state.draft_defaults.environment_profile_id
            if configuration is None
            else configuration.environment_profile_id
        )
        for item in selectors.environments:
            if query in item.name.casefold() or query in item.profile_id.casefold():
                marker = _configuration_marker(
                    state,
                    "environment",
                    item.profile_id,
                    selected=item.profile_id == selected,
                )
                entries.append(
                    (
                        f"{marker} {item.name}",
                        f"{item.mode} - {item.description}",
                        SelectConfigurationResource("environment", item.profile_id),
                        True,
                    )
                )
    else:
        groups: tuple[
            tuple[str, tuple[SelectableResourceSummary, ...], ResourceKind, tuple[str, ...]],
            ...,
        ] = (
            (
                "Plugin",
                selectors.harness_plugins,
                "harness_plugin",
                state.draft_defaults.harness_plugin_ids or ()
                if configuration is None
                else configuration.harness_plugin_ids,
            ),
            (
                "Run Extension",
                selectors.environment_run_extensions,
                "environment_run_extension",
                state.draft_defaults.environment_run_extension_ids or ()
                if configuration is None
                else configuration.environment_run_extension_ids,
            ),
            (
                "MCP",
                selectors.mcp_servers,
                "mcp_server",
                state.draft_defaults.mcp_server_ids or () if configuration is None else configuration.mcp_server_ids,
            ),
        )
        for label, items, kind, selected in groups:
            for item in items:
                if query not in item.name.casefold() and query not in item.resource_id.casefold():
                    continue
                marker = _configuration_marker(
                    state,
                    kind,
                    item.resource_id,
                    selected=item.resource_id in selected,
                )
                entries.append(
                    (
                        f"{marker} {label}: {item.name}",
                        item.source_path,
                        SelectConfigurationResource(kind, item.resource_id),
                        True,
                    )
                )
    return entries


def _configuration_marker(
    state: TerminalState,
    kind: ConfigurationResourceKind,
    resource_id: str,
    *,
    selected: bool,
) -> str:
    marker = "[x]" if selected else "[ ]"
    conflict = state.configuration_conflict
    if conflict is None or conflict.kind != kind or conflict.resource_id != resource_id:
        return marker
    target = "selected" if conflict.intended_selected else "not selected"
    return f"{marker} intended: {target}; select to retry"


def _status_text(state: TerminalState, context_key: str | None) -> str:
    view = None
    if context_key is not None and context_key != "new":
        view = state.thread_view(context_key)
    if view is None or view.detail is None:
        row = next(
            (item for item in state.workbench.rows if context_key is not None and item.thread.thread_id == context_key),
            None,
        )
        if row is not None:
            configuration = row.thread.configuration
            operation = row.latest_operation
            model_id = "unavailable"
            if state.selectors is not None:
                agent = next(
                    (item for item in state.selectors.agents if item.agent_id == configuration.agent_source.id),
                    None,
                )
                if agent is not None:
                    model_id = agent.model_id
            return "\n".join(
                (
                    f"App: {state.lifecycle.value}",
                    f"Mode: {state.mode.value}",
                    f"Thread: {row.thread.thread_id}",
                    f"Project: {configuration.project_id} (read-only)",
                    f"Agent: {configuration.agent_source.id}",
                    f"Model: {model_id} (resolved, read-only)",
                    f"Environment: {configuration.environment_profile_id}",
                    f"Configuration: v{configuration.version}",
                    f"Operation: {'inactive' if operation is None else operation.status.value}",
                )
            )
        return (
            f"App: {state.lifecycle.value}\n"
            f"Mode: {state.mode.value}\n"
            f"Launch Project: {state.launch_project_id or 'unmatched'}\n"
            "Focused Thread: new draft\n"
            "Live detail: no focused Thread"
        )
    configuration = view.detail.thread.configuration
    model_id = "unavailable"
    if state.selectors is not None:
        agent = next(
            (item for item in state.selectors.agents if item.agent_id == configuration.agent_source.id),
            None,
        )
        if agent is not None:
            model_id = agent.model_id
    operation = view.root_operation
    return "\n".join(
        (
            f"App: {state.lifecycle.value}",
            f"Mode: {state.mode.value}",
            f"Thread: {view.thread_id}",
            f"Continuation: {view.detail.continuation_id or 'none'}",
            f"Live snapshot: {'loaded' if view.epoch is not None else 'unavailable'}",
            f"Project: {configuration.project_id} (read-only)",
            f"Agent: {configuration.agent_source.id}",
            f"Model: {model_id} (resolved, read-only)",
            f"Environment: {configuration.environment_profile_id}",
            f"Configuration: v{configuration.version}",
            f"Operation: {'inactive' if operation is None else operation.status.value}",
            f"Reasoning display: {'shown' if state.show_reasoning else 'collapsed'}",
            f"Tool details: {'expanded' if state.show_tool_details else 'compact'}",
        )
    )


def _help_text() -> str:
    commands = "\n".join(f"/{item.name:<12} {item.description}" for item in COMMANDS)
    return (
        "Keyboard\n"
        "  Ctrl+P command palette\n"
        "  Ctrl+O Focus / Workbench\n"
        "  Ctrl+N new Thread\n"
        "  Ctrl+C cancel active root operation or exit\n"
        "  Enter submit / activate\n"
        "  Alt+Enter or Shift+Enter newline\n"
        "  Escape close overlay without approving, denying, or cancelling\n\n"
        f"Commands\n{commands}"
    )


__all__ = ["OverlayPane"]
