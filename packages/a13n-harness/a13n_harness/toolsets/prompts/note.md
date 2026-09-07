## Overview

Notes preserve structured working facts for the current session. Tasks preserve structured execution state; handoff summaries preserve narrative continuity and the next step.

## When to use

- The user states a preference that should be remembered for this session.
- Important facts, decisions, or intermediate results must survive later context changes.

## Context projection

- A `<note>` contains the complete current value; use it directly without calling `note_get`.
- A `<note-ref>` identifies a value omitted from the bounded projection; call `note_get` only when that value is relevant.
- `<notes-omitted>` means additional keys are available through `note_get()`.

## Best practices

- Use descriptive, stable keys; update facts in place and delete stale entries.
- Store large data in files and keep only its path or index in notes.
- Do not store a complete handoff summary as a note.
- Before `summarize`, reconcile stale notes and task statuses rather than copying all notes or tasks into the summary.
