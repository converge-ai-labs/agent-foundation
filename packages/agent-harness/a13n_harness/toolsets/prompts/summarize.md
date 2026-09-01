<summarize-guidelines>
<overview>
Use `summarize` for an explicit semantic handoff when continuity would improve by moving to a fresh context. Notes and tasks remain separately projected as current structured state.
</overview>
<communication>
Explain the transition naturally. Do not mention context windows or token limits to the user.
</communication>
<when-to-summarize>
- The conversation contains substantial completed work that no longer needs to remain verbatim.
- The user asks to switch to a materially different task.
- A major task phase is complete and another phase will follow.
- The user explicitly asks to summarize and continue.
</when-to-summarize>
<when-not-to-summarize>
- A restored-context or completed-handoff marker is already present.
- The current task is a direct continuation and all context remains relevant.
- Only a simple follow-up or minor adjustment remains.
</when-not-to-summarize>
<before-summarizing>
- Reconcile stale notes and task statuses first.
- Preserve the user's intent, completed work, key decisions, unresolved work, relevant past interactions, and immediate next step.
- Do not mechanically duplicate all notes or tasks. Include a noted fact only when the handoff narrative or next step depends on it.
- Do not write the complete handoff summary into a note.
</before-summarizing>
<files-to-inspect>
List only files likely to require immediate inspection after continuation. Paths are reminders; contents are not loaded.
</files-to-inspect>
</summarize-guidelines>
