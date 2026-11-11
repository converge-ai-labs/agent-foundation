---
name: worktree-pr
description: Start a task in a Git worktree under .worktrees/ from freshly fetched origin/main, implement and validate it, then use commit-push-pr to open or update a PR. Use when the user requests worktree-based development or an isolated task-to-PR workflow; continue follow-ups in the same worktree.
---

# Worktree to Pull Request

Own workspace preparation and continuity; [commit-push-pr](../commit-push-pr/SKILL.md) owns the Git/GitHub handoff. Follow `AGENTS.md` and [CONTRIBUTING.md](../../../CONTRIBUTING.md). Keep implementation with the current agent unless parallel work is requested.

A request to complete a task in a worktree and open a PR covers preparation, implementation, validation, commit, push, and PR creation. A workspace-only request stops after preparation. Preserve authorization across follow-ups; opening a PR does not authorize merging it.

## Select and Create the Worktree

Inspect `git status --short --branch`, `git remote -v`, and `git worktree list --porcelain`. Identify the primary checkout and any existing worktree for this task. Keep its path and branch in the task context so follow-ups reuse it rather than starting over.

For a new task:

1. Confirm `origin` is the intended repository and fetch it with `git fetch origin`. Use the updated `origin/main`, not local `main` or the current branch. If fetching fails, report the blocker rather than silently using a stale base.

2. Choose a short descriptive branch and `<primary>/.worktrees/<task-slug>` path within the Environment's accessible files and shell scope. Keep all task worktrees under the primary checkout, even when starting from another worktree. If the branch or path already exists, inspect it and reuse it only when it belongs to this task; otherwise choose a distinct name.

3. Ensure `/.worktrees/` is ignored in the primary checkout. The repository's `.gitignore` owns this rule. For an older checkout without it, add the same rule to the common Git `info/exclude` without changing unrelated entries.

4. Create the worktree and record its starting commit:

   ```bash
   git worktree add --no-track -b <branch> <primary>/.worktrees/<task-slug> origin/main
   git -C <primary>/.worktrees/<task-slug> rev-parse HEAD
   ```

Unrelated changes in the original checkout can stay in place. Do not stash, reset, or copy them into the new worktree. If this task depends on existing uncommitted work or commits outside `origin/main`, agree on how to carry them over before proceeding.

## Work in the Selected Worktree

Use the selected worktree explicitly for file paths, command working directories, validation, and Git/GitHub operations. A shell `cd` does not change later tool calls. Read the checkout's applicable guidance before editing.

Implement the requested task and validate according to [Local Validation](../../../CONTRIBUTING.md#local-validation). Prepare only the dependencies needed for the work; do not automatically copy private `.env` files or link another checkout's `.venv` or `node_modules`. Service work follows [its development guide](../../../dev/service/README.md), including `make dev-status` for checkout-specific URLs and ports.

## Frontend Screenshot Evidence

For frontend changes, include screenshots of the affected rendered UI in the PR body. Capture representative states and relevant viewport sizes from the task worktree after the final UI changes; inspect the images for correctness and sensitive data. Keep capture files outside the repository and reuse valid screenshots when their visible inputs have not changed.

Upload screenshots as GitHub-native attachments through `gh`, not browser automation or external image hosting. A browser login is not required. After the repository and CLI authentication checks owned by commit-push-pr, the following PNG upload works with `gh api`:

```bash
repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
repository_id=$(gh api "repos/$repo" --jq .id)
screenshot=/absolute/path/to/screenshot.png
upload_result=/absolute/path/to/screenshot-upload.json

gh api --method POST \
  -H 'Content-Type: application/octet-stream' \
  -H 'Accept: application/vnd.github+json' \
  "https://uploads.github.com/user-attachments/assets?name=screenshot.png&content_type=image/png&repository_id=$repository_id" \
  --input "$screenshot" > "$upload_result"
```

Read the returned JSON `url` and retain it. Add `![Descriptive screenshot caption](<returned-url>)` beside the behavior it demonstrates in the PR body, then create or update the PR through `gh` using the temporary body file. Preserve existing PR content and verify the saved body references the returned attachment URLs. For multiple screenshots, upload each once and give each a distinct caption; the endpoint filename and content type must match the submitted file, with query values URL-encoded when necessary. This endpoint is for GitHub.com; do not assume an Enterprise host supports it.

If an upload fails, report the actual CLI/API error and resolve the authentication or repository permission problem. Do not infer failure from an unsigned browser session, silently substitute another host, commit screenshots as source assets, or block unrelated authorized work. Reuse successful upload URLs rather than repeating an upload when only the PR edit failed. Keep all GitHub operations in `gh`; browser tools are for validating the application UI.

## Hand Off to commit-push-pr

Read and follow [commit-push-pr](../commit-push-pr/SKILL.md) in the same worktree. Carry forward the selected path, branch, starting commit, intended repository, PR base `main`, change scope, validation results, and authorized stages. Reuse valid checks rather than repeating them for handoff.

That skill owns staging, commit messages, push, PR content, and CI reporting. Do not duplicate those rules here or create another agent just to execute them.

Report the worktree path alongside the commit and PR result. Leave the worktree and branch available for follow-up changes; PR creation does not require cleanup or synchronization of the primary checkout.

## After an Authorized Merge

When the user explicitly authorizes merging this PR, use `gh` and the repository's merge policy without bypassing required checks. Confirm that the PR is actually merged before synchronizing; queued or auto-merge-enabled is not merged.

As part of that authorized merge workflow, help bring the primary checkout up to date:

1. Fetch `origin` and inspect the primary checkout's status and branch again. A clean checkout has no staged, unstaged, or untracked changes; ignored worktrees and local caches do not make it dirty.
2. If it is clean and on `main`, fast-forward it with `git -C <primary> merge --ff-only origin/main`.
3. If it is on another branch, confirm switching to `main` unless already authorized; do not merge `origin/main` into an unrelated task branch. If it is dirty or local `main` cannot fast-forward, preserve it and report what blocks synchronization rather than stashing, resetting, or rebasing it.

Report the merge and synchronization outcomes separately. Keep worktrees and branches unless their removal is requested.
