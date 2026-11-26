from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from release_version import COMPONENTS, RELEASE_DEPENDENCIES_FILE, parse_release_version, validate_version_syntax

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

# Select primary changes from product source, user docs, and shipped asset inputs.
COMPONENT_PATHS = {
    "a13n-harness": (
        "packages/a13n-harness",
        "packages/a13n-stream-protocol",
        "docs/a13n-stream-protocol",
    ),
    "a13n-harness-ui": (
        "packages/a13n-harness-ui",
        "frontend/apps/a13n-harness-ui",
        "scripts/prepare-a13n-harness-ui-assets.py",
        "scripts/prepare_a13n_harness_ui_assets.py",
    ),
    "a13n-logging": ("packages/a13n-logging",),
    "a13n-service": (
        "packages/a13n-service",
        "proto/a13n-service",
        "frontend/apps/a13n-console",
        "deploy/docker/images/a13n-service",
        "deploy/docker/images/browser",
        "deploy/docker/compose/a13n-service.yaml",
        "deploy/docker/compose/a13n-service-browser.yaml",
        "deploy/docker/compose/a13n-service-quickstart.yaml",
        "deploy/kubernetes",
        "deploy/monitoring",
    ),
    "a13n-envd": (
        "crates/a13n-envd",
        "packages/a13n-envd-client",
        "proto/a13n-envd",
        "Cargo.toml",
        "Cargo.lock",
        "deploy/docker/images/sandbox",
        "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py",
        "scripts/install-a13n-envd.sh",
        "scripts/install-a13n-envd.ps1",
        "scripts/envd_release_metadata.py",
    ),
}
SHARED_COMPONENT_PATHS = {
    "a13n-harness": ("docs/environments",),
    "a13n-harness-ui": ("frontend/packages/a13n-ui",),
    "a13n-service": ("frontend/packages/a13n-ui", "docs/environments"),
}
EXCLUDED_PATHS = (
    ":(top,glob,exclude)packages/*/tests/**",
    ":(top,glob,exclude)frontend/**/tests/**",
    ":(top,glob,exclude)frontend/**/*.test.*",
    ":(top,glob,exclude)frontend/**/*.spec.*",
    ":(top,glob,exclude)deploy/monitoring/tests/**",
    ":(top,glob,exclude)deploy/kubernetes/kind-local.yaml",
)
# Order resolves PRs carrying more than one category label.
LABEL_CATEGORIES = {
    "breaking-change": "Breaking changes",
    "enhancement": "Features",
    "bug": "Bug fixes",
    "documentation": "Documentation",
}
SKIP_LABELS = frozenset({"chore", "skip-changelog"})
CHANGE_CATEGORIES = (
    "Breaking changes",
    "Features",
    "Bug fixes",
    "Performance",
    "Documentation",
    "Shared changes",
    "Other changes",
)
TYPE_CATEGORIES = {
    "feat": "Features",
    "fix": "Bug fixes",
    "revert": "Bug fixes",
    "perf": "Performance",
    "docs": "Documentation",
}
KNOWN_TITLE_TYPES = TYPE_CATEGORIES.keys() | {"chore", "refactor", "test", "ci", "build", "style"}
SCOPE_ALIASES = {name: name.removeprefix("a13n-") for name in (*COMPONENTS, "a13n-stream-protocol")}
CONVENTIONAL_SUBJECT = re.compile(
    r"(?P<type>[a-z]+)(?:\((?P<scope>[^\r\n()]+)\))?(?P<breaking>!)?:[ \t]+(?P<title>\S[^\r\n]*)"
)


@dataclass(frozen=True)
class ReleaseChange:
    commit: str
    subject: str
    body: str = ""
    labels: tuple[str, ...] = ()
    title: str | None = None
    authors: tuple[str, ...] = ()
    reviewers: tuple[str, ...] = ()
    shared_only: bool = False

    @property
    def pull_request(self) -> str | None:
        match = re.search(r"\s+\(#(\d+)\)$", self.subject) or re.match(r"Merge pull request #(\d+)\b", self.subject)
        return match[1] if match else None


PR_COMMIT_AUTHORS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      commits(first: 100, after: $endCursor) {
        nodes { commit { id authors(first: 100) { totalCount nodes { user { login } } } } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
COMMIT_AUTHORS_QUERY = """
query($id: ID!, $endCursor: String) {
  node(id: $id) {
    ... on Commit {
      authors(first: 100, after: $endCursor) {
        nodes { user { login } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class PullRequestMetadata:
    title: str
    labels: tuple[str, ...]
    authors: tuple[str, ...]
    reviewers: tuple[str, ...]


def _unique_logins(logins: Iterable[str]) -> tuple[str, ...]:
    accounts: dict[str, str] = {}
    for login in logins:
        if login:
            accounts.setdefault(login.casefold(), login)
    return tuple(sorted(accounts.values(), key=str.casefold))


def _github_json(command: list[str]) -> Any:
    response = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
    return json.loads(response.stdout)


def _pull_request_authors(repository: str, number: str) -> tuple[str, ...]:
    owner, name = repository.split("/")
    pages = _github_json(
        [
            "gh",
            "api",
            "graphql",
            "--paginate",
            "--slurp",
            "-f",
            f"query={PR_COMMIT_AUTHORS_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        ]
    )
    logins = []
    for page in pages:
        for node in page["data"]["repository"]["pullRequest"]["commits"]["nodes"]:
            commit = node["commit"]
            authors = commit["authors"]
            nodes = authors["nodes"]
            if authors["totalCount"] > len(nodes):
                author_pages = _github_json(
                    [
                        "gh",
                        "api",
                        "graphql",
                        "--paginate",
                        "--slurp",
                        "-f",
                        f"query={COMMIT_AUTHORS_QUERY}",
                        "-F",
                        f"id={commit['id']}",
                    ]
                )
                nodes = [
                    author for author_page in author_pages for author in author_page["data"]["node"]["authors"]["nodes"]
                ]
            logins.extend(author["user"]["login"] for author in nodes if author["user"] is not None)
    return _unique_logins(logins)


def load_pull_request_metadata(changes: Sequence[ReleaseChange], repository: str) -> list[ReleaseChange]:
    metadata_by_pr: dict[str, PullRequestMetadata] = {}
    result = []
    for change in changes:
        number = change.pull_request
        if number is None:
            result.append(change)
            continue
        if number not in metadata_by_pr:
            try:
                details = _github_json(
                    ["gh", "pr", "view", number, "--repo", repository, "--json", "labels,title,author"]
                )
                author = details["author"]
                authors = _unique_logins(
                    [
                        *([author["login"]] if author is not None else []),
                        *_pull_request_authors(repository, number),
                    ]
                )
                review_pages = _github_json(
                    ["gh", "api", "--paginate", "--slurp", f"repos/{repository}/pulls/{number}/reviews"]
                )
                reviewers = _unique_logins(
                    review["user"]["login"]
                    for page in review_pages
                    for review in page
                    if review["user"] is not None and review["state"] != "PENDING"
                )
                metadata_by_pr[number] = PullRequestMetadata(
                    title=details["title"],
                    labels=tuple(label["name"] for label in details["labels"]),
                    authors=authors,
                    reviewers=reviewers,
                )
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
                raise ReleaseNotesError(f"Cannot read metadata for PR #{number}: {error}") from error
        metadata = metadata_by_pr[number]
        result.append(
            replace(
                change,
                labels=metadata.labels,
                title=metadata.title,
                authors=metadata.authors,
                reviewers=metadata.reviewers,
            )
        )
    return result


def _primary_component_paths(component: str) -> tuple[str, ...]:
    _tag_prefix(component)
    return (*COMPONENT_PATHS[component], f"docs/{component}")


def component_paths(component: str) -> tuple[str, ...]:
    return (*_primary_component_paths(component), *SHARED_COMPONENT_PATHS.get(component, ()))


def _release_log(root: Path, previous_tag: str, current_tag: str, paths: tuple[str, ...], format_string: str) -> str:
    # First-parent history gives one entry per merged PR, without repeating its
    # branch commits. Merge diffs must also be compared with their first parent.
    command = [
        "git",
        "log",
        "--first-parent",
        "--diff-merges=first-parent",
        "--no-renames",
        f"--format={format_string}",
        "-z",
        f"{previous_tag}..{current_tag}",
        "--",
        *paths,
        *EXCLUDED_PATHS,
    ]
    try:
        return subprocess.run(command, cwd=root, check=True, capture_output=True, text=True, encoding="utf-8").stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseNotesError(f"Cannot collect component changes: {error}") from error


def collect_release_changes(root: Path, component: str, previous_tag: str, current_tag: str) -> list[ReleaseChange]:
    log = _release_log(root, previous_tag, current_tag, component_paths(component), "%H%x00%s%x00%b")
    fields = log.split("\0")[:-1]
    changes = [ReleaseChange(fields[index], fields[index + 1], fields[index + 2]) for index in range(0, len(fields), 3)]
    if changes and component in SHARED_COMPONENT_PATHS:
        primary = _release_log(root, previous_tag, current_tag, _primary_component_paths(component), "%H")
        primary_commits = set(primary.split("\0"))
        changes = [replace(change, shared_only=change.commit not in primary_commits) for change in changes]
    return changes


@dataclass(frozen=True)
class DependencyChange:
    package: str
    before: str | None
    after: str | None


def _tag_toml(root: Path, tag: str, path: str) -> dict[str, Any] | None:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8"
        ).stdout

    try:
        if not git("ls-tree", "--name-only", tag, "--", path):
            return None
        return tomllib.loads(git("show", f"{tag}:{path}"))
    except (OSError, subprocess.SubprocessError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ReleaseNotesError(f"Cannot read release dependencies from {tag}:{path}: {error}") from error


def _tag_dependencies(root: Path, component: str, tag: str) -> dict[str, str] | None:
    path = RELEASE_DEPENDENCIES_FILE.as_posix()
    document = _tag_toml(root, tag, path)
    if document is None:
        path = f"packages/{component}/pyproject.toml"
        document = _tag_toml(root, tag, path) or {}
        for name in ("tool", "a13n"):
            document = document.get(name, {})
            if not isinstance(document, dict):
                raise ReleaseNotesError(f"Invalid release dependency table in {tag}:{path}")
        key = "release-dependencies"
    else:
        key = component
    if key not in document:
        return None
    table = document[key]
    if not isinstance(table, dict) or any(not isinstance(value, str) for value in table.values()):
        raise ReleaseNotesError(f"Invalid release dependency table in {tag}:{path}")
    return table


def collect_dependency_changes(
    root: Path, component: str, previous_tag: str, current_tag: str
) -> list[DependencyChange]:
    current = _tag_dependencies(root, component, current_tag)
    if current is None:
        return []
    previous = _tag_dependencies(root, component, previous_tag)
    if previous is None:
        return []
    return [
        DependencyChange(package, previous.get(package), current.get(package))
        for package in sorted(previous.keys() | current.keys())
        if previous.get(package) != current.get(package)
    ]


def _frozen_title(change: ReleaseChange) -> str:
    subject = change.subject
    pr = re.search(r"\s+\(#(\d+)\)$", subject)
    merge = re.match(r"Merge pull request #(\d+)\b", subject)
    if pr:
        subject = subject[: pr.start()]
    elif merge and change.body.strip():
        subject = change.body.strip().splitlines()[0]
    return subject


def _parse_title(subject: str) -> re.Match[str] | None:
    match = CONVENTIONAL_SUBJECT.fullmatch(subject)
    return match if match and match["type"] in KNOWN_TITLE_TYPES else None


def _change_category(change: ReleaseChange, conventional: re.Match[str] | None) -> str | None:
    if (
        "breaking-change" in change.labels
        or (conventional and conventional["breaking"])
        or re.search(r"(?m)^BREAKING[ -]CHANGE:", change.body)
    ):
        return "Breaking changes"
    if SKIP_LABELS.intersection(change.labels):
        return None
    if change.shared_only:
        return "Shared changes"
    change_type = conventional["type"] if conventional else ""
    if change_type == "perf" and "enhancement" in change.labels and {"bug", "documentation"}.isdisjoint(change.labels):
        return "Performance"
    return next(
        (title for label, title in LABEL_CATEGORIES.items() if label in change.labels),
        TYPE_CATEGORIES.get(change_type, "Other changes"),
    )


def _escape_text(text: str) -> str:
    # Render Git-authored titles as text; only the generated references are links.
    return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", text)


def _change_entry(change: ReleaseChange, component: str, repository: str) -> tuple[str, str] | None:
    subject = change.title if change.title is not None else _frozen_title(change)
    conventional = _parse_title(subject)
    category = _change_category(change, conventional)
    if category is None:
        return None
    if conventional:
        subject = _escape_text(conventional["title"])
        scope = (conventional["scope"] or "").strip()
        scope = SCOPE_ALIASES.get(scope.lower(), scope)
        if scope and scope.lower() != SCOPE_ALIASES[component]:
            subject = f"**{_escape_text(scope)}**: {subject}"
    else:
        subject = _escape_text(subject)
    reference = change.pull_request
    if reference:
        link = f"[#{reference}](https://github.com/{repository}/pull/{reference})"
    else:
        link = f"[{change.commit[:7]}](https://github.com/{repository}/commit/{change.commit})"
    attribution = ""
    if change.authors:
        attribution += " — by " + ", ".join(f"@{login}" for login in change.authors)
    reviewers = [
        login for login in change.reviewers if login.casefold() not in {author.casefold() for author in change.authors}
    ]
    if reviewers:
        attribution += ("; " if attribution else " — ") + "reviewed by " + ", ".join(f"@{login}" for login in reviewers)
    return category, f"- {subject}{attribution} ({link})"


def render_release_notes(
    *,
    component: str,
    version: str,
    repository: str,
    previous_tag: str | None,
    changes: Sequence[ReleaseChange] = (),
    dependencies: Sequence[DependencyChange] = (),
    manual_notes: str | None = None,
    releaser: str | None = None,
) -> str:
    current_tag = release_tag(component, version)
    sections = [f"Release initiated by @{releaser}."] if releaser else []
    contributors = [releaser] if releaser else []
    if previous_tag is None:
        sections.append(manual_notes or INITIAL_NOTES[component])
    else:
        if manual_notes:
            sections.append(manual_notes)
        sections.append("## What's Changed")
        groups: dict[str, list[str]] = {category: [] for category in CHANGE_CATEGORIES}
        for change in changes:
            rendered = _change_entry(change, component, repository)
            if rendered is None:
                continue
            category, entry = rendered
            groups[category].append(entry)
            contributors.extend((*change.authors, *change.reviewers))
        for category, entries in groups.items():
            if entries:
                sections.append(f"### {category}\n\n" + "\n".join(entries))
        if dependencies:
            entries = [
                f"- `{item.package}`: {_dependency_value(item.before)} → {_dependency_value(item.after)}"
                for item in dependencies
            ]
            sections.append("### Dependencies\n\n" + "\n".join(entries))
        if not any(groups.values()) and not dependencies:
            sections.append("No component-scoped changelog entries in this release.")
        comparison = (
            f"https://github.com/{repository}/compare/{quote(previous_tag, safe='')}...{quote(current_tag, safe='')}"
        )
        sections.append(f"**Full Changelog** (repository comparison): {comparison}")
    if contributors:
        sections.append("## Contributors\n\n" + " ".join(f"@{login}" for login in _unique_logins(contributors)))
    return "\n\n".join(sections) + "\n"


def _dependency_value(value: str | None) -> str:
    return f"`{value}`" if value is not None else "not declared"


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
