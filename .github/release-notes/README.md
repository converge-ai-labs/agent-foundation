# Curated Release Notes

A release may include reviewed, human-written notes at:

```text
.github/release-notes/<component>/<version>.md
```

Supported component keys are `a13n-harness`, `a13n-harness-ui`, `a13n-service`, `a13n-envd`, `a13n-service-cli`, `a13n-python`, `a13n-go`, `a13n-rust`, and `a13n-typescript`. Versions use canonical stable `X.Y.Z` or RC `X.Y.Z-rc.N` syntax, so RC notes use a path such as `.github/release-notes/a13n-service/1.2.3-rc.1.md`.

Directories for earlier release channels are immutable historical records. New notes use only the canonical component keys above.

The file is optional. For a channel with an earlier release, its content is prepended to GitHub's generated pull-request notes and channel-scoped Full Changelog. The first RC for a target compares with the preceding release, and later RCs compare with the preceding RC. A final stable release compares with the preceding stable release rather than an RC, preserving complete stable release notes. For the first release in a channel, the curated content replaces the default initial-release sentence; no cross-channel generated notes are added. A missing or empty file is treated as no curated content, so the release remains fully automatic.

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
