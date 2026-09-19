from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote

from release_version import COMPONENTS, parse_release_version, validate_version_syntax

TAG_PREFIXES = {
    "a13n-harness": "release/a13n-harness-v",
    "a13n-harness-ui": "release/a13n-harness-ui-v",
    "a13n-logging": "release/a13n-logging-v",
    "a13n-service": "release/a13n-service-v",
    "a13n-envd": "release/a13n-envd-v",
}
INITIAL_NOTES = {
    "a13n-harness": "Initial release for a13n Harness libraries.",
    "a13n-harness-ui": "Initial release for a13n Harness UI.",
    "a13n-logging": "Initial release for a13n Logging.",
    "a13n-service": "Initial release for a13n Service.",
    "a13n-envd": "Initial release for a13n-envd.",
}
RELEASE_NOTES_DIRECTORY = Path(".github/release-notes")

# Scope by shipped source, not commit-title scopes or mutable GitHub labels.
# Shared root lockfiles and unrelated release channels do not select a change.
COMPONENT_PATHS = {
    "a13n-harness": (
        "packages/a13n-harness",
        "packages/a13n-stream-protocol",
        "docs/a13n-environment",
        "docs/a13n-stream-protocol",
        "spec/a13n-environment",
        "spec/a13n-stream-protocol",
    ),
    "a13n-harness-ui": (
        "packages/a13n-harness-ui",
        "frontend/apps/a13n-harness-ui",
        "frontend/packages/a13n-ui",
        "deploy/containers/a13n-harness-ui",
        "scripts/prepare_harness_ui_image.py",
        "scripts/prepare_a13n_harness_ui_assets.py",
    ),
    "a13n-logging": ("packages/a13n-logging",),
    "a13n-service": (
        "packages/a13n-service",
        "proto/a13n-service",
        "frontend/apps/a13n-console",
        "frontend/packages/a13n-ui",
        "deploy/containers/a13n-service",
    ),
    "a13n-envd": (
        "crates/a13n-envd",
        "packages/a13n-envd-client",
        "proto/a13n-envd",
        "Cargo.toml",
        "Cargo.lock",
        "deploy/containers/sandbox",
        "scripts/install-a13n-envd.sh",
        "scripts/install-a13n-envd.ps1",
    ),
}
# Order resolves PRs carrying more than one category label.
LABEL_CATEGORIES = {
    "breaking-change": "Breaking changes",
    "enhancement": "Features",
    "bug": "Bug fixes",
    "documentation": "Documentation",
}
SKIP_LABELS = frozenset({"chore", "skip-changelog"})
CHANGE_CATEGORIES = ("Breaking changes", "Features", "Bug fixes", "Performance", "Documentation", "Other changes")
CONVENTIONAL_SUBJECT = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^\r\n()]+)\))?(?P<breaking>!)?:[ \t]+(?P<title>[^\r\n]+)$"
)


@dataclass(frozen=True)
class ReleaseChange:
    commit: str
    subject: str
    body: str = ""
    labels: tuple[str, ...] = ()

    @property
    def pull_request(self) -> str | None:
        match = re.search(r"\s+\(#(\d+)\)$", self.subject) or re.match(r"Merge pull request #(\d+)\b", self.subject)
        return match[1] if match else None


def load_pull_request_labels(changes: Sequence[ReleaseChange], repository: str) -> list[ReleaseChange]:
    labels_by_pr: dict[str, tuple[str, ...]] = {}
    result = []
    for change in changes:
        number = change.pull_request
        if number is None:
            result.append(change)
            continue
        if number not in labels_by_pr:
            try:
                response = subprocess.run(
                    ["gh", "pr", "view", number, "--repo", repository, "--json", "labels", "--jq", ".labels[].name"],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise ReleaseNotesError(f"Cannot read labels for PR #{number}: {error}") from error
            labels_by_pr[number] = tuple(response.stdout.splitlines())
        result.append(replace(change, labels=labels_by_pr[number]))
    return result


def component_paths(component: str) -> tuple[str, ...]:
    _tag_prefix(component)
    return (
        *COMPONENT_PATHS[component],
        f"docs/{component}",
        f"spec/{component}",
        f".github/workflows/release-{component}.yml",
    )


def collect_release_changes(root: Path, component: str, previous_tag: str, current_tag: str) -> list[ReleaseChange]:
    # First-parent history gives one entry per merged PR, without repeating its
    # branch commits. Merge diffs must also be compared with their first parent.
    command = [
        "git",
        "log",
        "--first-parent",
        "--diff-merges=first-parent",
        "--no-renames",
        "--format=%H%x00%s%x00%b",
        "-z",
        f"{previous_tag}..{current_tag}",
        "--",
        *component_paths(component),
    ]
    try:
        result = subprocess.run(command, cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseNotesError(f"Cannot collect component changes: {error}") from error
    fields = result.stdout.split("\0")[:-1]
    return [ReleaseChange(fields[index], fields[index + 1], fields[index + 2]) for index in range(0, len(fields), 3)]


def _change_entry(change: ReleaseChange, repository: str) -> tuple[str, str]:
    subject = change.subject
    pr = re.search(r"\s+\(#(\d+)\)$", subject)
    merge = re.match(r"Merge pull request #(\d+)\b", subject)
    if pr:
        subject = subject[: pr.start()]
    elif merge and change.body.strip():
        subject = change.body.strip().splitlines()[0]
    conventional = CONVENTIONAL_SUBJECT.match(subject)
    category = "Other changes"
    if conventional:
        category = {"feat": "Features", "fix": "Bug fixes", "perf": "Performance", "docs": "Documentation"}.get(
            conventional["type"], "Other changes"
        )
        subject = conventional["title"]
    if (conventional and conventional["breaking"]) or re.search(r"(?m)^BREAKING[ -]CHANGE:", change.body):
        category = "Breaking changes"
    # Labels own PR classification. Unlabelled historical PRs and direct commits
    # retain the Conventional Commit fallback; no historical relabelling is needed.
    category = next((title for label, title in LABEL_CATEGORIES.items() if label in change.labels), category)
    # Render Git-authored titles as text; only the generated references are links.
    subject = re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", subject)
    reference = change.pull_request
    if reference:
        link = f"[#{reference}](https://github.com/{repository}/pull/{reference})"
    else:
        link = f"[{change.commit[:7]}](https://github.com/{repository}/commit/{change.commit})"
    return category, f"- {subject} ({link})"


def render_release_notes(
    *,
    component: str,
    version: str,
    repository: str,
    previous_tag: str | None,
    changes: Sequence[ReleaseChange] = (),
    manual_notes: str | None = None,
) -> str:
    current_tag = release_tag(component, version)
    if previous_tag is None:
        return (manual_notes or INITIAL_NOTES[component]) + "\n"
    sections = [manual_notes] if manual_notes else []
    sections.append("## What's Changed")
    groups: dict[str, list[str]] = {category: [] for category in CHANGE_CATEGORIES}
    for change in changes:
        if SKIP_LABELS.intersection(change.labels) and "breaking-change" not in change.labels:
            continue
        category, entry = _change_entry(change, repository)
        groups[category].append(entry)
    for category, entries in groups.items():
        if entries:
            sections.append(f"### {category}\n\n" + "\n".join(entries))
    if not any(groups.values()):
        sections.append("No component-scoped changelog entries in this release.")
    comparison = (
        f"https://github.com/{repository}/compare/{quote(previous_tag, safe='')}...{quote(current_tag, safe='')}"
    )
    sections.append(f"**Full Changelog** (repository comparison): {comparison}")
    return "\n\n".join(sections) + "\n"


class ReleaseNotesError(ValueError):
    pass


def _tag_prefix(component: str) -> str:
    if component not in COMPONENTS:
        raise ReleaseNotesError(f"Unknown release component: {component}")
    return TAG_PREFIXES[component]


def release_tag(component: str, version: str) -> str:
    validate_version_syntax(version)
    return f"{_tag_prefix(component)}{version}"


def previous_release_tag(component: str, version: str, tags: Iterable[str]) -> str | None:
    prefix = _tag_prefix(component)
    current_version = parse_release_version(version)
    current_target = current_version.major, current_version.minor, current_version.patch
    same_target_rcs: list[tuple[tuple[int, int, int, int, int], str]] = []
    stable_candidates: list[tuple[tuple[int, int, int, int, int], str]] = []
    for tag in tags:
        if not tag.startswith(prefix):
            continue
        candidate_text = tag.removeprefix(prefix)
        try:
            candidate_version = parse_release_version(candidate_text)
        except ValueError:
            continue
        if candidate_version.precedence_key >= current_version.precedence_key:
            continue
        if candidate_version.is_prerelease:
            candidate_target = candidate_version.major, candidate_version.minor, candidate_version.patch
            if current_version.is_prerelease and candidate_target == current_target:
                same_target_rcs.append((candidate_version.precedence_key, tag))
            continue
        stable_candidates.append((candidate_version.precedence_key, tag))
    if same_target_rcs:
        return max(same_target_rcs)[1]
    if stable_candidates:
        return max(stable_candidates)[1]
    return None


def release_notes_path(component: str, version: str) -> Path:
    release_tag(component, version)
    return RELEASE_NOTES_DIRECTORY / component / f"{version}.md"


def read_manual_release_notes(root: Path, component: str, version: str) -> str | None:
    relative_path = release_notes_path(component, version)
    path = root / relative_path
    if not path.exists():
        return None
    if not path.is_file():
        raise ReleaseNotesError(f"Release notes path is not a file: {relative_path}")
    try:
        notes = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise ReleaseNotesError(f"Cannot read release notes from {relative_path}: {error}") from error
    return notes or None


def build_release_command(
    *,
    component: str,
    version: str,
    repository: str,
    title: str,
    assets: Sequence[str],
) -> list[str]:
    tag = release_tag(component, version)
    command = [
        "gh",
        "release",
        "create",
        tag,
        *assets,
        "--repo",
        repository,
        "--verify-tag",
        "--title",
        title,
    ]
    if parse_release_version(version).is_prerelease:
        command.append("--prerelease")
    command.extend(("--notes-file", "-"))
    return command
