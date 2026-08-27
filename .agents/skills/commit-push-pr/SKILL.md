---
name: commit-push-pr
description: Prepare a focused Git commit, push its branch, and create or update a GitHub pull request. Use when the user asks to commit changes, push work, open a PR, submit a change for review, or complete the combined commit-push-PR workflow.
---

# Commit, Push, and Open a Pull Request

Move the intended repository changes into a reviewable GitHub pull request without including unrelated work. Use Git for local version-control operations and GitHub CLI (`gh`) for GitHub repository and pull-request operations.

## Workflow

### 1. Verify prerequisites

Before changing branches or creating a commit:

1. Confirm Git is available.
2. Confirm GitHub CLI is available with `command -v gh`.
3. Determine the GitHub hostname from the remote, defaulting to `github.com` for standard GitHub repositories.
4. Verify authentication with `gh auth status --hostname <github-host>`.

If `gh` is not installed, stop and tell the user to install it from <https://cli.github.com/>. Do not install it without the user's approval. After installation, tell the user to authenticate with:

```bash
gh auth login --hostname github.com
```

If `gh` is installed but not authenticated for the target host, stop and ask the user to run the corresponding `gh auth login` command. Continue only after `gh auth status` succeeds.

Use `gh` rather than browser automation, raw GitHub API calls, `curl`, or another hosting CLI for GitHub operations. Git remains responsible for diffing, staging, committing, and pushing because `gh` does not replace those Git operations.

### 2. Inspect the repository

- Read `AGENTS.md` and follow repository-specific instructions, including any linked contribution or workflow documents that it marks as required.
- Read the repository's contribution guide when present, such as `CONTRIBUTING.md`, and identify its pull-request requirements.
- Run `git status --short --branch`, inspect the relevant diff, and check the configured remotes.
- Use `gh repo view --json nameWithOwner,url,defaultBranchRef` to resolve GitHub repository metadata and the base branch when available.
- Identify the intended change scope from the conversation and repository state. Do not stage unrelated user changes.
- Check untracked files before staging. Never commit credentials, local configuration, generated artifacts, or files that appear sensitive.

If the intended scope is materially ambiguous, ask the user before staging. Otherwise, proceed without unnecessary confirmation.

### 3. Prepare the branch

- Keep using the current branch when it is already a suitable feature or fix branch.
- If the current branch is the base or another protected branch, create a short, descriptive branch such as `feat/add-agent-runtime` or `docs/clarify-lifecycle`.
- Do not rewrite shared history or force-push unless the user explicitly requests it and the consequences are clear.

An empty remote repository is a special case: establish its initial base commit before attempting a pull request, and explain that a PR cannot exist until a base branch exists.

### 4. Validate the change

- Run `make check` on the final intended working tree before committing. This is the required and sufficient local validation for opening or updating a pull request.
- Do not require `make check-all` or another full local gate before opening the pull request. Required GitHub CI checks own merge validation.
- If `make check` changes files, review those changes and rerun it until it passes.
- If `make check` cannot run, record the exact reason in the pull request summary.
- Review `git diff --check` and the final diff before staging.

Do not bypass failing checks or Git hooks with flags such as `--no-verify`. Fix failures caused by the change when feasible; otherwise stop and report the blocker.

### 5. Create the commit

- Stage explicit paths with `git add <path>...`; avoid `git add .` when unrelated changes are present.
- Review `git diff --cached --stat` and `git diff --cached` before committing.
- Use a concise English commit subject that describes the completed change. Follow the repository's existing convention; if none exists, use Conventional Commits, for example:

```text
feat(runtime): add resumable execution state
```

- Do not create an empty commit. If nothing needs committing, continue only when there are already unpushed commits relevant to the requested pull request.

#### Commit attribution

- Never add a `Co-authored-by:` trailer or any other co-author attribution for an agent. The agent assisted the author; it is not a co-author.
- If assistant attribution is required, use exactly this trailer format:

```text
Assisted-by: NAME <email>
```

- Use the assistant identity supplied by the user, repository policy, or execution host. Never invent a name or email address. If attribution is required but the identity is unavailable, ask the user before committing.
- Before pushing, inspect the final commit message and remove any `Co-authored-by:` trailer. Do not push a commit that violates this policy.

### 6. Push safely

Push the current branch with upstream tracking:

```bash
git push -u origin HEAD
```

- Never push directly to a protected base branch as part of the normal PR workflow.
- Do not use `--force`; use `--force-with-lease` only after explicit user approval when history rewriting is necessary.

### 7. Create or update the pull request with `gh`

- Run `gh pr view --json number,url,state,isDraft,reviewRequests` to check whether the current branch already has a pull request. A not-found result means a new PR may be created; it is not an execution failure.
- If an open pull request exists, do not create a duplicate. Push the commit, verify that its title and body still satisfy the repository requirements, and update them only when needed without discarding meaningful existing content.
- Otherwise, create the pull request with `gh pr create`, setting the base and head branches explicitly when they are not unambiguous.
- Before drafting the body, discover the repository's pull-request template in the standard root, `docs/`, and `.github/` locations, including multiple-template directories. Prefer the template and contribution requirements from the base branch so the pull request cannot relax its own review contract. Fall back to the working-tree versions for an initial repository or when the base has none, and report that fallback.
- Use an English title and follow the selected repository template instead of replacing it with a generic body. Preserve its required headings and checklist items, replace or remove placeholders, and mark a checkbox complete only when the condition is actually satisfied. Link the relevant issue when one exists; never leave an incomplete reference such as `Closes #` in the body.
- If the repository has no pull-request template, use this fallback:

```markdown
## Summary

- <material change>
- <material change>

## Testing

- `<command and outcome>`
```

Keep the body factual and satisfy any additional requirements from the contribution guide. Report exact validation commands and outcomes, and mention skipped or failing checks and their reasons instead of implying they passed. Use a temporary body file when needed and do not leave it in the repository.

#### Route reviewers from `MAINTAINERS.md`

- Treat the base branch's `MAINTAINERS.md` as the source of truth for semantic review ownership. Prefer the base version so a pull request cannot redirect its own review by changing the maintainer table.
- If the base branch does not yet contain `MAINTAINERS.md`, use the working-tree version and state that fallback in the result. This fallback is expected only during repository initialization.
- Review the pull request's changed paths, diff, intent, and affected architectural boundaries. Match them against the semantic scopes and path hints in `MAINTAINERS.md`; path hints are evidence, not the sole routing rule.
- A change may match multiple areas. Collect every distinct maintainer from all matching areas, and use the `Default` area only when no specific area matches.
- Do not infer reviewers from organization membership, repository roles, collaborator lists, or commit history.
- Inspect the pull request author and existing requests:

```bash
gh pr view --json url,isDraft,author,reviewRequests
```

- Remove the pull request author and already-requested reviewers from the candidate set. Never request the author to review their own pull request, and never remove an existing review request merely because the current routing result differs.
- For a ready pull request, request each remaining maintainer with GitHub's reviewer mechanism rather than adding an `@mention` to the pull request body:

```bash
gh pr edit --add-reviewer <login-or-org/team>
```

- If the pull request is a draft, do not request reviewers yet. Report the matched maintainers and request them when the pull request becomes ready for review.
- If no eligible independent reviewer remains, report that clearly. Do not invent a reviewer or silently select someone from outside `MAINTAINERS.md`.

#### Check CI merge readiness

- After creating or updating the pull request, run `gh pr checks` once and report the current required-check status.
- Do not block pull-request creation while checks are pending, and do not wait for completion unless the user asks.
- A pull request must not merge until its required GitHub CI checks pass. If checks are pending, advise the user to monitor CI before merging; if checks fail, report the failures and do not describe the pull request as merge-ready.

### 8. Report the result

Return:

- branch name
- commit hash and subject
- push result
- pull request URL
- requested reviewers or the reason none were requested
- `make check` outcome
- current GitHub CI status, including a recommendation to monitor pending checks before merge
- any remaining risks or follow-up work
