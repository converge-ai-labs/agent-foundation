---
name: commit-push-pr
description: Prepare a focused, detailed Git commit, push its branch, and create or update a GitHub pull request. Use whenever the user asks to commit changes, push work, open or update a PR, or submit the current work to GitHub.
---

# Commit, Push, and Open a Pull Request

Move the intended changes into a clear GitHub pull request with minimal ceremony. Focus on the commit, push, and PR handoff. Do not create an Issue, start a code review, or request reviewers unless the user explicitly asks.

Use Git for local version-control operations and GitHub CLI (`gh`) for GitHub operations.

## Workflow

### 1. Verify access

Before changing branches or committing:

1. Confirm Git and `gh` are installed.
2. Determine the GitHub hostname from the remote.
3. Verify authentication with `gh auth status --hostname <host>`.
4. Resolve the repository and default branch with:

```bash
gh repo view --json nameWithOwner,url,defaultBranchRef
```

If `gh` is missing or unauthenticated, stop and give the user the exact installation or `gh auth login` command required. Do not substitute browser automation, raw API calls, or another hosting CLI.

### 2. Inspect the intended change

- Read `AGENTS.md` and the contribution guide when present.
- Run `git status --short --branch`, inspect the diff, and check remotes.
- Check untracked files before staging.
- Exclude unrelated work, credentials, local configuration, caches, and accidental generated artifacts.
- Ask about scope only when the intended files are materially ambiguous; otherwise proceed directly.
- Check whether the current branch already has an open PR so the workflow updates rather than duplicates it.

Do not open a GitHub Issue as part of this workflow unless the user specifically requests one.

### 3. Prepare the branch

Keep the current branch when it is already suitable. If it is the default or another protected branch, create a short descriptive branch such as:

```text
feat/harness-capability-runtime
fix/session-cancellation
```

Do not push directly to the protected base branch. Do not rewrite shared history or force-push without explicit approval.

### 4. Run the local gate

Run only the repository's fast PR gate on the final intended working tree:

```bash
make check
```

For this workflow, `make check` is the required and sufficient local validation. Do not add `make check-all`, full test suites, documentation builds, image builds, or code-review steps unless the user explicitly requests them.

- If `make check` formats files, review the resulting changes and rerun it.
- If it fails because of the intended change, fix the failure when feasible and rerun it.
- If it cannot run, record the exact reason in the PR body.
- Run `git diff --check` before staging.
- Never bypass checks or hooks with `--no-verify`.

### 5. Create a detailed commit

Stage explicit intended paths rather than using `git add .` when unrelated files may exist. Review both `git diff --cached --stat` and the staged diff before committing.

Use an English Conventional Commit subject with a required scope:

```text
type(scope): imperative summary
```

Common types are `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`, and `perf`. Keep the type and scope lowercase, keep the summary concise, and omit the trailing period.

Add a detailed body with two to five concise bullet points describing the material completed behavior or boundary changes:

```text
feat(harness): expand capability runtime

- Add packaged instructions and multimedia-understanding fallbacks.
- Complete bounded EIP file traversal and search operations.
- Document observation profiles and runtime configuration.
```

Commit-message rules:

- Describe completed outcomes, not the editing process.
- Keep bullets specific and non-overlapping.
- Do not pad the body with routine mechanics such as staging or formatting.
- Do not create an empty commit.
- Do not add `Co-authored-by:` or other agent co-author trailers.
- Do not invent assistant attribution.

If the relevant commit already exists and only needs pushing, do not create another commit merely to satisfy the workflow.

### 6. Push safely

Push the current branch with upstream tracking:

```bash
git push -u origin HEAD
```

Do not use `--force`. Use `--force-with-lease` only after explicit user approval when history rewriting is necessary.

### 7. Create or update the PR

Check for an existing PR first:

```bash
gh pr view --json number,url,state,isDraft,title
```

Update an existing open PR instead of creating a duplicate. Otherwise, create one with explicit base and head branches when needed.

The PR title must use the same scoped Conventional Commit format:

```text
type(scope): imperative summary
```

Usually reuse the commit subject. For a multi-commit branch, write one title that accurately summarizes the complete PR. Do not use a plain prose title such as `Expand Harness capability runtime`; use `feat(harness): expand capability runtime`.

Before writing the body, read the repository's PR template, preferring the base-branch version. Preserve required headings and checklist items, remove placeholders, and mark only completed conditions. Never leave an incomplete reference such as `Closes #`. When no Issue exists, write `None` or remove the optional Issue section as the template permits.

If no template exists, use:

```markdown
## Summary

- <material change>
- <material change>

## Validation

- `make check` — passed.
```

Keep the PR body factual and concise. Use a temporary body file outside the repository. Create a ready PR unless the user asks for a draft.

Do not start a review subagent or request GitHub reviewers by default. Review routing is outside this workflow unless the user explicitly asks for it.

### 8. Report CI and result

Run `gh pr checks` once after creating or updating the PR. Report the current state without waiting for completion unless the user asks.

Return:

- branch name;
- commit hash, subject, and bullet summary;
- push result;
- PR URL and title;
- `make check` outcome;
- current CI status;
- any concrete blocker or remaining follow-up.
