"""Bounded task presentation from selected snapshots and native change facts."""

from __future__ import annotations

from a13n_harness.events import TaskChangedPayload
from pydantic import ValidationError

from a13n_harness_ui.surfaces import TaskPage, TaskView


class TaskPanel:
    def __init__(self) -> None:
        self.tasks: dict[str, TaskView] = {}
        self.expanded = False
        self.available = True
        self.omitted = 0
        self.version = 0

    def restore(self, page: TaskPage) -> None:
        self.tasks = {task.task_id: task for task in page.tasks}
        self.available = page.available
        self.omitted = page.omitted
        self.version = page.version or 0

    def ingest(self, payload: object) -> None:
        try:
            change = TaskChangedPayload.model_validate(payload)
        except ValidationError:
            return
        if change.task_state_version < self.version:
            return
        task = change.task
        previous = self.tasks.get(task.id)
        if previous is not None and previous.version >= task.version:
            return
        if previous is None and len(self.tasks) >= 256:
            self.omitted += 1
            return
        self.version = change.task_state_version
        self.tasks[task.id] = TaskView(task_id=task.id, **task.model_dump(exclude={"id"}))

    def lines(self) -> list[str]:
        if not self.available:
            return ["Tasks unavailable in the selected continuation (external provider)."]
        tasks = tuple(self.tasks.values())
        if not tasks and not self.omitted:
            return []
        completed = sum(task.status == "completed" for task in tasks)
        active = sum(task.status == "in_progress" for task in tasks)
        lines = [f"Tasks {completed}/{len(tasks) + self.omitted} complete · {active} active"]
        if not self.expanded:
            return lines
        ordered = sorted(tasks, key=lambda task: {"in_progress": 0, "pending": 1, "completed": 2}[task.status])
        visible = ordered[:5]
        for task in visible:
            blocked = any(
                dependency not in self.tasks or self.tasks[dependency].status != "completed"
                for dependency in task.blocked_by
            )
            state = (
                "active"
                if task.status == "in_progress"
                else "done"
                if task.status == "completed"
                else "blocked"
                if blocked
                else "pending"
            )
            subject = task.active_form if task.status == "in_progress" and task.active_form else task.subject
            lines.append(f"[{state}] {subject}")
        hidden = len(tasks) + self.omitted - len(visible)
        if hidden:
            lines.append(f"{hidden} more tasks")
        return lines
