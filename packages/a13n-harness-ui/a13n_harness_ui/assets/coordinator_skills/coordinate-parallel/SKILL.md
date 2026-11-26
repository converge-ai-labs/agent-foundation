---
name: coordinate-parallel
description: Decompose genuinely independent work across multiple managed worker Threads in parallel, enforce workspace isolation to prevent edit conflicts, and reconcile and integrate verified results.
---

# Coordinate Parallel Work

Use this workflow when a user request splits into multiple independently useful tracks (such as separate investigations, disjoint modules, independent bug fixes, or multi-angle reviews) that benefit from concurrent execution.

## Independence and Isolation Gate

Check independence **before** fanning out:

- **Read-only tasks** (exploration, audits, reviews) can share the same workspace safely.
- **Editing tasks in the same repository** must either own strictly disjoint files/directories or each run in its own isolated Git worktree and branch.
- **Tightly coupled work** that edits overlapping files, shared schemas, or interdependent interfaces must stay on one continuing worker mainline—do not force parallel splitting.

## Workflow

1. **Partition the work cleanly.** Define each track's concrete objective, explicit file/module/worktree boundary, non-goals, required validation, and expected return format so workers do not duplicate or step on each other's work.
2. **Launch bounded workers in parallel.** Call `create_worker` for each independent track (or reuse idle workers whose existing context matches). Keep concurrency proportionate to the task rather than spawning redundant workers.
3. **Track the roster and yield cleanly.** Record each track's objective, worker Thread ID, and isolation path (using notes or tasks when available). When all active tracks are in flight and nothing independent remains for the Coordinator, state what is running and end the turn—do not poll in a loop.
4. **Reconcile outcomes reactively.** As auto-follow-ups or worker reports arrive, inspect each worker's saved result via `get_thread`. An inactive worker is not proof of success: verify its deliverables, blockers, and validation evidence.
5. **Handle partial failures and integrate.** Do not discard completed tracks when one worker fails or blocks. Send targeted correction requests to the responsible worker with `send_thread_message` (or report concrete blockers to the user). When multiple tracks produce edits or findings that combine, perform or assign a final cross-track consistency check and integration validation before presenting the unified result.
