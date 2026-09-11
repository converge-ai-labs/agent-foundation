from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from release_notes import (
    TAG_PREFIXES,
    build_release_command,
    collect_release_changes,
    load_pull_request_labels,
    previous_release_tag,
    read_manual_release_notes,
    release_notes_path,
    release_tag,
    render_release_notes,
)
from release_version import COMPONENTS


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _release_tags(component: str, current_tag: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "tag", "--merged", current_tag, "--list", f"{TAG_PREFIXES[component]}*"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"Cannot list release tags: {error}") from error
    return result.stdout.splitlines()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a GitHub Release with notes scoped to one component release channel."
    )
    parser.add_argument("component", choices=COMPONENTS)
    parser.add_argument("version")
    parser.add_argument("title")
    parser.add_argument("assets", nargs="*")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview notes with read-only GitHub label queries; publish nothing."
    )
    args = parser.parse_args()

    try:
        repository = _required_environment("GITHUB_REPOSITORY")
        expected_tag = release_tag(args.component, args.version)
        current_ref = expected_tag if args.dry_run else _required_environment("GITHUB_REF_NAME")
        if current_ref != expected_tag:
            raise ValueError(f"Expected release tag {expected_tag}, got {current_ref}")

        tags = _release_tags(args.component, expected_tag)
        if expected_tag not in tags:
            raise ValueError(f"Release tag is missing from the checkout: {expected_tag}")
        previous_tag = previous_release_tag(args.component, args.version, tags)
        manual_notes = read_manual_release_notes(Path.cwd(), args.component, args.version)

        missing_assets = [asset for asset in args.assets if not Path(asset).is_file()]
        if missing_assets:
            details = ", ".join(missing_assets)
            raise ValueError(f"Release assets do not exist: {details}")

        changes = (
            collect_release_changes(Path.cwd(), args.component, previous_tag, expected_tag)
            if previous_tag is not None
            else []
        )
        changes = load_pull_request_labels(changes, repository)
        notes = render_release_notes(
            component=args.component,
            version=args.version,
            repository=repository,
            previous_tag=previous_tag,
            changes=changes,
            manual_notes=manual_notes,
        )
        command = build_release_command(
            component=args.component,
            version=args.version,
            repository=repository,
            title=args.title,
            assets=args.assets,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    if args.dry_run:
        print(notes, end="")
        return

    if manual_notes is not None:
        print(f"Prepending curated notes from {release_notes_path(args.component, args.version)}")
    if previous_tag is None:
        print(f"Creating {expected_tag} as the first release in its channel")
    else:
        print(f"Generating release notes for {previous_tag}...{expected_tag}")
    try:
        result = subprocess.run(command, input=notes, text=True, encoding="utf-8", check=False)
    except OSError as error:
        raise SystemExit(f"Cannot run gh: {error}") from error
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
