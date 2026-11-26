# Release Notes

Write the usual PR title and push the component's release tag as usual. PR labels are added automatically on opening and readiness; the release workflow generates a component-scoped changelog without an AI service or a required notes file. See [PR labels](../../CONTRIBUTING.md#pr-labels) for the automatic mapping and manual overrides.

## Automatic Changelog

`scripts/release_notes.py` selects first-parent changes between the comparison and release tags by product source, user docs, and shipped asset paths. Harness includes Stream Protocol and Environment execution; Service includes Console and Environment target management; a13n-envd includes its Python client, protocol, installers, and sandbox image. The shared UI package qualifies for Harness UI and Service, and `docs/environments` qualifies for Harness and Service. A change matching only shared paths in a channel goes under Shared changes, except that breaking changes keep their own section.

Test directories and frontend test files covered by the path filters, the local Kubernetes kind config, standalone specifications, release workflows, and shared root Python/frontend lockfiles do not qualify on their own. Selection does not depend on the title's scope and does not infer transitive dependency impact. The independent Service SDKs and CLI have their own releases.

A merged PR appears once using its GitHub title at generation time; a direct commit uses its subject and links to the commit. Known Conventional Commit types are stripped. Explicit legacy `a13n-` scope aliases normalize to [canonical scopes](../../CONTRIBUTING.md#canonical-scopes); the matching release-channel scope is omitted and other scopes appear in bold. Plain titles and unknown prefixes are preserved as text.

A `breaking-change` label, a recognized `!` in the current PR title (or the recorded title when there is no PR metadata), or a `BREAKING CHANGE:` / `BREAKING-CHANGE:` marker in the commit body takes precedence over all other categories and exclusions. Non-breaking entries with `chore` or `skip-changelog` are omitted. Remaining category labels take precedence as `enhancement`, `bug`, `documentation`; a `perf` title with `enhancement` and no `bug` or `documentation` goes under Performance. Without a category label, `feat`, `fix`/`revert`, `perf`, and `docs` map to Features, Bug fixes, Performance, and Documentation. Otherwise the entry goes under Other changes. The generator does not read the live PR body; adding a breaking footer after open/readiness requires a `breaking-change` label unless the marker also appears in the recorded commit.

The generator reads PR labels, titles, and attribution through `gh` for PR numbers recorded in standard squash or merge commit subjects. A later title, label, or participation change can change a subsequent preview. Metadata lookup failures stop generation; existing Releases remain unchanged.

### Dependencies

When the component's declared release requirements differ, Dependencies lists the net changes between the comparison and release tags in package name order. It compares that channel's table in `release-dependencies.toml`, falling back to `[tool.a13n.release-dependencies]` in each tag's package manifest when its root file does not exist. Channels without a table or a known baseline omit this section. Intermediate pin commits do not create separate dependency entries.

Only same-channel tags reachable from the release tag are eligible comparison bases. An RC uses an earlier RC for the same target version when available, otherwise the preceding stable release. A final stable release compares with the preceding stable release, preserving the complete stable change set. With no comparison base, the first release uses a short initial-release sentence instead of repository-wide history. With a base but no displayed changes or dependency differences, the notes say so explicitly. The Full Changelog link remains a repository-wide comparison and is labeled accordingly.

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
