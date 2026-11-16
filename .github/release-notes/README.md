# Release Notes

Write the usual PR title and push the component's release tag as usual. PR labels are added automatically on opening and readiness; the release workflow generates a component-scoped changelog without an AI service or a required notes file. See [PR labels](../../CONTRIBUTING.md#pr-labels) for the automatic mapping and manual overrides.

## Automatic Changelog

`scripts/release_notes.py` owns the component path mapping. It includes each component's source, shipped assets, documentation, specifications, and release workflow. The Harness channel includes Stream Protocol and Environment execution. Environment target management belongs to Service; there is no separate Environment release channel. Harness UI and Service include their browser applications and shared UI package; a13n-envd includes its Python client, protocol, installers, and sandbox image. Language SDKs remain independent, and the Rust SDK excludes the Service CLI. Shared root Python/frontend lockfiles and unrelated component changes do not select an entry. This is a source-change summary, not a transitive dependency-impact analysis.

The generator reads first-parent history between the comparison tag and the release tag. A merged PR appears once, preserving its full GitHub title at generation time, including any Conventional Commit prefix; direct commits appear with commit links. Selection is based on changed files, not the title's scope. Entries retain the existing titles rather than inventing user-impact summaries.

PR labels determine categories and exclusions using the rules in `scripts/release_notes.py`. The generator reads labels and attribution metadata through `gh` for PR numbers recorded in standard squash or merge commit subjects. It uses metadata as it exists at generation time, so a later title, label, or participation change can change a subsequent preview, but does not rewrite an already published release. Direct commits and PRs without category labels fall back to Conventional Commit types (`feat`, `fix`, `perf`, `docs`) and explicit breaking-change markers. Other entries go into Other changes. Metadata lookup failures stop note generation rather than silently misclassifying entries or omitting contributors.

Only same-channel tags reachable from the release tag are eligible comparison bases. An RC uses an earlier RC for the same target version when available, otherwise the preceding stable release. A final stable release compares with the preceding stable release, preserving the complete stable change set. With no comparison base, the first release uses a short initial-release sentence instead of repository-wide history. With a base but no entries after scope and exclusion filtering, the notes say so explicitly. The Full Changelog link remains a repository-wide comparison and is labeled accordingly.

### Attribution

When `GITHUB_ACTOR` is available, the first line mentions the account that pushed the release tag: `Release initiated by @<account>.` Reruns retain the original actor, not the account that requested the rerun. This line precedes curated highlights as well as generated notes. GitHub Actions remains the native Release author; no personal token is needed.

Each listed PR credits its author and GitHub-linked commit authors, including accounts associated with `Co-authored-by` trailers. Submitted reviewers are credited separately with `reviewed by` mentions. Unsubmitted reviews, review invitations, and comment-only participants are not counted. Deleted or unlinked accounts are omitted rather than inferred from names or email addresses. Commit, coauthor, and review collections are paginated.

A final `## Contributors` section lists the release initiator and the accounts credited by the displayed PRs, deduplicated case-insensitively and sorted by login. Skipped and unrelated PRs contribute no accounts. Initial releases keep their short initial-release notes and credit the initiator when available; they do not scan all historical PRs. Local previews without `GITHUB_ACTOR` omit the initiator line and credit only known PR contributors. The section is omitted when no accounts are known. Existing Releases remain unchanged on retries.

### Preview

To preview notes for an existing locally available tag, run from the repository root with complete history and release tags fetched:

```bash
GITHUB_REPOSITORY=converge-ai-labs/agent-foundation \
  uv run --locked python scripts/create-github-release.py \
  a13n-logging 0.1.1 'a13n Logging 0.1.1' --dry-run
```

Replace the component, version, and title as needed. Authenticate `gh` with access to read the repository's PRs. Set `GITHUB_ACTOR=<account>` to include the initiator in the preview. This prints notes using read-only metadata queries; it does not create a tag, release, or artifact. Automatic entries come from the target tag's history; optional curated notes are read from the current checkout, as in the release workflow.

## Optional Curated Notes

A release may include reviewed, human-written notes at:

```text
.github/release-notes/<component>/<version>.md
```

Supported component keys are `a13n-harness`, `a13n-harness-ui`, `a13n-logging`, `a13n-service`, and `a13n-envd`. Versions use canonical stable `X.Y.Z` or RC `X.Y.Z-rc.N` syntax, so RC notes use a path such as `.github/release-notes/a13n-service/1.2.3-rc.1.md`.

The file is optional. For a channel with an earlier release, its content is prepended to the component-scoped entries. For the first release, it replaces the default initial-release sentence. A missing or empty file is treated as no curated content, so the release remains automatic.

Use this structure when the sections are relevant:

```markdown
## Highlights

- Describe the most important user-visible changes.

## Upgrade notes

Describe required actions, or state that none are required.

## Known issues

- Describe material limitations that users should understand before upgrading.
```

Do not repeat the release title, generated pull-request list, contributors, or Full Changelog link.
