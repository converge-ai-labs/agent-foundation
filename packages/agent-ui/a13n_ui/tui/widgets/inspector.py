"""Compact focused Thread inspector."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from a13n_ui.tui.models import ThreadViewState


class FocusInspector(VerticalScroll):
    """Render bounded task, child, and configuration context."""

    def __init__(self) -> None:
        super().__init__(id="focus-inspector")

    def compose(self) -> ComposeResult:
        yield Static("CONTEXT", classes="inspector-heading")
        yield Static(id="inspector-context")
        yield Static("SELECTION", classes="inspector-heading")
        yield Static(id="inspector-selection")
        yield Static("TASKS", classes="inspector-heading")
        yield Static(id="inspector-tasks")
        yield Static("CHILDREN", classes="inspector-heading")
        yield Static(id="inspector-children")

    def project(self, view: ThreadViewState) -> None:
        detail = view.detail
        if detail is None:
            context = Text("Focused Thread details are unavailable.", style="yellow")
        else:
            configuration = detail.thread.configuration
            context = Text(
                "\n".join(
                    (
                        f"Thread  {detail.thread.thread_id}",
                        f"Project {configuration.project_id}",
                        f"Agent   {configuration.agent_source.id}",
                        f"Env     {configuration.environment_profile_id}",
                        f"Config  v{configuration.version}",
                    )
                )
            )
        self.query_one("#inspector-context", Static).update(context)

        selected = next(
            (block for block in view.timeline if block.block_id == view.selected_block_id),
            None,
        )
        if selected is None:
            selection_text = Text("Focus a timeline block for details", style="dim")
        else:
            values = [
                f"Kind    {selected.kind.value}",
                f"Status  {selected.status.value}",
            ]
            if selected.summary:
                values.append(selected.summary)
            if selected.tool_call_id:
                values.append(f"Tool    {selected.tool_call_id}")
            if selected.task_id:
                values.append(f"Task    {selected.task_id}")
            if selected.execution_id:
                values.append(f"Child   {selected.execution_id}")
            if selected.available_actions:
                values.append(f"Actions {', '.join(selected.available_actions)}")
            selection_text = Text("\n".join(values))
        self.query_one("#inspector-selection", Static).update(selection_text)

        if not view.tasks.available:
            task_text = Text("Task state unavailable", style="yellow")
        elif not view.tasks.tasks:
            task_text = Text("No tasks", style="dim")
        else:
            lines = []
            marker = {"pending": "[ ]", "in_progress": "[*]", "completed": "[x]"}
            for task in view.tasks.tasks[:20]:
                lines.append(f"{marker[task.status]} {task.subject}")
            if view.tasks.omitted:
                lines.append(f"... {view.tasks.omitted} omitted")
            task_text = Text("\n".join(lines))
        self.query_one("#inspector-tasks", Static).update(task_text)

        children = () if view.snapshot is None else view.snapshot.children.executions
        if not children:
            child_text = Text("No child executions", style="dim")
        else:
            lines = []
            for child in children[:20]:
                status = (
                    "unavailable"
                    if child.persisted_status == "running" and child.local_status == "unavailable"
                    else child.persisted_status
                )
                lines.append(f"{child.subagent_name}  {status}")
            omitted = 0 if view.snapshot is None else view.snapshot.children.total - len(children)
            if omitted > 0:
                lines.append(f"... {omitted} omitted")
            child_text = Text("\n".join(lines))
        self.query_one("#inspector-children", Static).update(child_text)


__all__ = ["FocusInspector"]
