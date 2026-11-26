---
name: coordinate-worktree-pr
description: Guide a managed worker Thread to implement, validate, and deliver a pull request from an isolated Git worktree while following the target repository's own guidelines, skills, and authorization boundaries.
---

# Coordinate Worktree Pull Request

Use this workflow to delegate a self-contained change to a managed worker Thread that develops in an isolated Git worktree and delivers a pull request according to the target repository's rules.

## Scope and Authorization

- Managed workers share the Coordinator's Project roots; creating a worker does **not** automatically create a Git worktree.
- State the exact authorized stages in the worker brief (for example: prepare worktree, implement, validate, commit, push, and open or update the PR).
- Never authorize merging a PR, rewriting published history, deploying, or releasing unless the user explicitly authorized that stage.

## Workflow

1. **Inspect repository expectations and select one worker mainline.** Reuse the existing worker for an ongoing task or create one worker with `create_worker`. Keep tightly coupled implementation and PR follow-ups on that same worker and worktree rather than spawning separate workers for commit or PR steps.
2. **Write a concrete, self-contained brief.** Tell the worker to:
   - Read and follow the target repository's own guidance (`AGENTS.md`, `CONTRIBUTING.md`, PR templates, and any repository-local worktree or PR skills when present) instead of assuming generic defaults.
   - Fetch the intended remote and create (or reuse) a dedicated Git worktree and task branch from the fresh target base branch without disturbing unrelated work in the primary checkout.
   - Implement the requested scope, update affected specs/docs/tests together, and run the repository's relevant local validation gates.
   - Respect the authorized handoff stages: stage only intended files, write commits and PR title/body per repository conventions, push the branch, open or update the PR, and report the worktree path, branch, commit SHA, validation evidence, PR URL, and initial CI status.
3. **Verify before reporting.** When the worker finishes or reports back, inspect its saved outcome via `get_thread` (and check key diffs, validation commands, or PR state directly when warranted). Do not report completion from a creation receipt or an unverified summary.
4. **Route follow-ups back to the same worker.** Send review comments, CI failures, or scope adjustments to the responsible worker via `send_thread_message` so all revisions stay in the same worktree and PR.
5. **Report clearly to the user.** Summarize the delivered outcome, PR link, worktree path and branch, verified validation results, CI status, and any remaining blockers or decisions.
