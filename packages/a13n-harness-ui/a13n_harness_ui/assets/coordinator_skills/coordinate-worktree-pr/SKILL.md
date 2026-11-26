---
name: coordinate-worktree-pr
description: Delegate a repository change to one worker in an isolated Git worktree. Verify the authorized PR stages and keep review fixes on the same branch.
---

# Coordinate Worktree Pull Request

Use this workflow for one repository change with an isolated checkout and a pull-request handoff. Keep implementation and review fixes with one worker on the same task branch and PR.

## Pin the Target and Stop Point

Give the worker a short brief:

- The requested outcome, non-goals, and acceptance checks.
- The target repository and forge, base remote and branch, and push destination, including a fork if applicable. For follow-ups, include the known worktree, head branch, and PR identity.
- The authorized stages: prepare, implement, validate, commit, push, and open or update the PR. State where to stop.

A worktree-to-PR request covers the necessary delivery stages. Stop earlier for a narrower request. Merge, published history rewrites, deployments, releases, and cleanup need their own authorization.

## Establish the Worktree

Have the worker confirm the checkout is reachable from its Environment, inspect Git status, remotes, and existing worktrees, and read the repository's guidance, applicable skills, and PR template. `create_worker` does not create a worktree, and choosing a different `agent_id` can change which mounts and tools the worker receives.

For a new task, confirm the target base against its remote, create the isolated worktree and branch, and record the starting SHA. Reuse an existing task worktree only after matching its repository, path, and branch to the brief. Keep the original checkout and unrelated changes intact. If the requested change depends on uncommitted work, resolve what to carry over before proceeding.

Use the selected worktree for file edits, validation, and Git operations. Apply the target repository's own workflow and required checks.

## Gate on Evidence

Ask the worker to return only the evidence for stages performed:

- Repository, worktree path, branch, starting SHA, final head SHA, and intended diff scope.
- Validation commands, their results and revision, and any skipped or failed required checks.
- Push destination and remote head; PR URL or ID, base and head, and observed checks for that head.

Compare the saved outcome with the local diff and, for published stages, the actual remote and PR state. Match the validated changes to the delivered head. Mark anything you could not verify. If push or PR creation has an uncertain outcome, check the destination before retrying. If a prerequisite blocks publication, preserve completed local work and report the exact stop point.

## Keep the PR on One Mainline

Send in-scope review or CI fixes to the same worker and worktree. Revalidate affected behavior, then verify the new PR head and checks. Get authorization before expanding scope or posting review replies that the user did not request.

Report the verified delivery, including the worktree, commit, PR link when applicable, and observed CI state. Pending checks are a status, not a gate to wait on unless the user requested it.
