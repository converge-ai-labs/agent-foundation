<task-manager>
Use tasks only when a visible checklist improves execution, coordination, or continuity. Set a task to `in_progress` when work begins and to `completed` immediately after it finishes. Use dependencies only for real execution blockers, ownership boundaries, or parallel coordination.
</task-manager>

<overview>
Notes preserve structured working facts for the current session. Handoff summaries preserve narrative continuity and the next step.
</overview>

<!-- reference -->

<when-to-use>
- The user states a preference that should be remembered for this session.
- Important facts, decisions, or intermediate results must survive later context changes.
</when-to-use>

<task-context>
- At the start of a Run, the task list shows unfinished work. Later notices identify changes.
- Use `task_list()` or `task_get(task_id)` to inspect current tasks.
</task-context>

<note-context>
- At the start of a Run, the notes directory lists keys. Later notices identify changes.
- Use `note_get()` for all keys and `note_get(key)` for a complete note.
</note-context>

<note>
- Use descriptive, stable keys; update facts in place and delete stale entries.
- Store large data in files and keep only its path or index in notes.
- Do not store a complete handoff summary as a note.
</note>
