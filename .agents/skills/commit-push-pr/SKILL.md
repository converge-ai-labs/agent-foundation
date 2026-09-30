---
name: commit-push-pr
description: Prepare focused commits, push branches, and create or update GitHub pull requests when requested. Perform only the stages authorized by the user; a commit-only request does not include pushing or opening a PR.
---

# Commit, Push, and Pull Request

Complete the requested Git/GitHub handoff under `AGENTS.md` and `CONTRIBUTING.md`. Preserve existing authorization across follow-ups. Do not add Issue creation, reviewer requests, PR merges, releases, or deployments unless authorized. Do not start a separate code review or review subagent unless the user requests it.

## Inspect and Prepare

- Inspect `git status --short --branch`, the diff, untracked files, and relevant remotes. Separate intended changes from unrelated work, secrets, local configuration, caches, and generated artifacts.
- Resolve the requested stages from context: commit-only ends after committing; push-only may use an existing commit; opening a PR normally includes the necessary branch, commit, and push. Ask only when the intended content or destination is materially ambiguous.
- Keep a suitable branch. For detached HEAD, a default branch, or another protected branch, follow the user's naming convention when provided; otherwise create a short descriptive branch such as `feat/harness-capability-runtime` or `fix/session-cancellation`. Do not push directly to a protected base.
- GitHub operations use `gh`; local Git work does not require it. On first use for a target, determine the host/repository from the remote and check `gh auth status --hostname <host>` and `gh repo view --json nameWithOwner,url,defaultBranchRef`. Reuse that verification unless the target or authentication context changes, or an operation indicates it is no longer valid.
- If `gh` is unavailable or unauthenticated, finish independent authorized local preparation and report the missing prerequisite and applicable login command. Do not silently substitute browser automation, raw APIs, or another hosting CLI.

Unresolved material design questions follow the contribution workflow; this skill does not settle them or authorize an Issue post.

## Validate the Intended Change

Follow [CONTRIBUTING.md](../../../CONTRIBUTING.md#local-validation) for validation. Reuse successful results whose relevant inputs remain unchanged and rerun affected checks after fixes. Committing or updating a PR does not itself invalidate prior results. Report failures and unavailable checks accurately; never bypass hooks.

Before staging, run `git diff --check`. Stage explicit intended paths and review both the staged diff and `git diff --cached --stat`.

## Commit

Use an English Conventional Commit subject with lowercase type and required scope:

```text
type(scope): imperative summary
```

Keep the subject concise without a trailing period. Describe material completed behavior or boundary changes in the body when needed; do not pad it with routine staging/formatting steps or require a fixed bullet count.

Do not create empty or duplicate commits, add agent co-author trailers, or invent attribution. If hooks rewrite files, inspect and stage only intended changes before committing again. Rewrite existing history only when that operation is explicitly authorized.

## Push

Confirm the destination remote and branch, then push with upstream tracking where needed; for a confirmed `origin` destination:

```bash
git push -u origin HEAD
```

Do not force-push by default. Use `--force-with-lease` only when the concrete history rewrite is explicitly authorized. After an uncertain push result, inspect remote state before retrying.

When local checkout synchronization is part of the user's workflow, inspect that checkout before changing it. Synchronize only a clean checkout with a verified fast-forward; explain the impact and obtain missing authorization if local work, reset, rebase, or merge would be involved. Do not delete branches or worktrees merely to tidy up.

## Create or Update the PR

Check for an existing open PR for the confirmed head/base so updates do not create duplicates:

```bash
gh pr view --json number,url,state,isDraft,title
```

If no open PR exists, create one with the intended head/base. Preserve an existing PR's draft state; create a ready PR unless the user requests a draft. Drafts skip code CI; marking a draft ready starts the applicable checks. Do not toggle readiness just to refresh automatic labels.

Use the scoped Conventional Commit format for the PR title, describing the resulting change rather than editing or review activity. Write it for the component's users or integrators, not just someone familiar with the implementation:

- Name the concrete capability added, failure corrected, or behavior/default changed. Prefer the observable outcome over internal mechanisms, file names, or work-log language; internal-only changes should still identify their concrete result without inventing user impact.
- Make the summary understandable without opening the PR. Include the affected surface when the scope alone carries essential context: generated entries remove `type(scope)!:` and retain only the summary.
- Avoid vague summaries such as "refine controls", "align behavior", or "simplify handling" unless they also say what specifically changes. Keep useful technical terms, but do not let them substitute for the outcome.
- For cross-component changes, qualify differing defaults or behavior rather than implying that all components change identically. Lead with the main outcome instead of squeezing every implementation detail into the title; keep secondary details and migration steps in the body.
- Before creating or updating the PR, read the summary without its Conventional Commit prefix as a standalone changelog bullet. Can the target reader tell what they gain, what stops failing, or what changes? Check it against the final diff and update the title when the scope changes. The generator reads merged Git subjects, so editing a PR title after merging does not repair historical entries.

Examples (use only when supported by the diff):

| Too vague or implementation-led | Outcome-led summary                                      |
| ------------------------------- | -------------------------------------------------------- |
| refine media previews           | preview linked images, audio, and video in conversations |
| simplify proxy handling         | allow proxied Web requests without local destination DNS |
| enable self-healing by default  | repair supported model history rejections by default     |

The [PR Labels workflow](../../../CONTRIBUTING.md#pr-labels) automatically classifies PRs on opening and readiness; ordinary PRs need no manual labels or notes file. Mark incompatible changes with `!` and explain impact and migration in the body. Existing type labels are preserved, so when an update changes the PR's classification, inspect its labels and adjust only the relevant ones with `gh pr edit`; preserve unrelated labels. Do not classify user-visible changes as `chore`. Label corrections are part of the authorized PR handoff, not permission to change repository settings or publish a release.

Read the repository PR template, preferring the base-branch version. Preserve required headings and checklist items, remove placeholders such as `Closes #`, link a relevant Issue when one exists, and mark only verified conditions. Human-review items require evidence from the human author.

Follow [Writing Issues and Pull Requests](../../../CONTRIBUTING.md#writing-issues-and-pull-requests) for the opening explanation and choice of visuals. Keep the body and diagrams aligned with the final diff when the PR scope changes. Explain material compatibility implications and exact validation outcomes. If no template exists, a short summary and validation section suffice. Pass multiline content with `--body-file` using a temporary file outside the repository. Do not claim a missing check passed or omit a known blocker.

Inspect `gh pr checks` once after creating or updating the PR. Report CI as pending, passing, or failed; wait or monitor only when requested. Before retrying an uncertain PR creation, query existing PRs again.

## Handoff

Report only the completed/requested stages: branch and commit, push result, PR link/title, validation and current CI state, plus concrete blockers. Apply reviewer routing from `MAINTAINERS.md` when requesting reviewers is authorized.
