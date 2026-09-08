from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from release_version import COMPONENTS, parse_release_version, validate_version_syntax

TAG_PREFIXES = {
    "a13n-harness": "release/a13n-harness-v",
    "a13n-harness-ui": "release/a13n-harness-ui-v",
    "a13n-logging": "release/a13n-logging-v",
    "a13n-service": "release/a13n-service-v",
    "a13n-envd": "release/a13n-envd-v",
    "a13n-service-cli": "release/a13n-service-cli-v",
    "a13n-python": "release/a13n/python/",
    "a13n-go": "release/a13n/go/",
    "a13n-rust": "release/a13n/rust/",
    "a13n-typescript": "release/a13n/typescript/",
}
INITIAL_NOTES = {
    "a13n-harness": "Initial release for a13n Harness libraries.",
    "a13n-harness-ui": "Initial release for a13n Harness UI.",
    "a13n-logging": "Initial release for a13n Logging.",
    "a13n-service": "Initial release for a13n Service.",
    "a13n-envd": "Initial release for a13n-envd.",
    "a13n-service-cli": "Initial release for the a13n Service CLI.",
    "a13n-python": "Initial release for the a13n SDK for Python.",
    "a13n-go": "Initial release for the a13n SDK for Go.",
    "a13n-rust": "Initial release for the a13n SDK for Rust.",
    "a13n-typescript": "Initial release for the a13n SDK for TypeScript.",
}
RELEASE_NOTES_DIRECTORY = Path(".github/release-notes")


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
    previous_tag: str | None,
    manual_notes: str | None = None,
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
    if component == "a13n-service-cli":
        command.append("--latest=false")
    if parse_release_version(version).is_prerelease:
        command.append("--prerelease")
    if previous_tag is None:
        command.extend(("--notes", manual_notes or INITIAL_NOTES[component]))
    else:
        if manual_notes is not None:
            command.extend(("--notes", manual_notes))
        command.extend(("--generate-notes", "--notes-start-tag", previous_tag))
    return command
