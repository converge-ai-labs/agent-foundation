"""Recognize release-only Harness patch pin bumps in a PR merge or main push."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

CONSUMERS = {
    "a13n-harness-ui": "Harness UI",
    "a13n-service": "Service",
}
MANIFESTS = {f"packages/{component}/pyproject.toml": component for component in CONSUMERS}
COMPATIBILITY_DOC = "spec/repository-model.md"
HARNESS_DEPENDENCIES = ("a13n-harness", "a13n-stream-protocol")
STABLE_PIN = re.compile(r"==(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root).decode("utf-8")


def paired_pin(manifest: str) -> str | None:
    dependencies = tomllib.loads(manifest)["tool"]["a13n"]["release-dependencies"]
    harness, protocol = (dependencies.get(key) for key in HARNESS_DEPENDENCIES)
    if isinstance(harness, str) and harness == protocol and STABLE_PIN.fullmatch(harness):
        return harness
    return None


def patch_bump(before: str, after: str) -> tuple[str, str] | None:
    old, new = paired_pin(before), paired_pin(after)
    if old is None or new is None:
        return None
    old_line, _, old_patch = old.rpartition(".")
    new_line, _, new_patch = new.rpartition(".")
    if old_line != new_line or int(old_patch) >= int(new_patch):
        return None

    # Check the whole file, changing only the two canonical lines in the release table.
    header = "\n[tool.a13n.release-dependencies]\n"
    prefix, found_header, tables = before.partition(header)
    if not found_header:
        return None
    release_table, separator, rest = tables.partition("\n[")
    lines = release_table.splitlines(keepends=True)
    for key in HARNESS_DEPENDENCIES:
        old_line = f'{key} = "{old}"'
        matches = [index for index, line in enumerate(lines) if line.removesuffix("\n") == old_line]
        if len(matches) != 1:
            return None
        index = matches[0]
        lines[index] = lines[index].replace(old, new)
    if prefix + header + "".join(lines) + separator + rest == after:
        return old, new
    return None


def docs_match(before: str, after: str, bumps: dict[str, tuple[str, str]]) -> bool:
    for component, (old, new) in bumps.items():
        label = CONSUMERS[component]
        row = rf"(?m)^(\| {re.escape(label)} +\| Harness, Stream Protocol \| `){re.escape(old)}(`, identical for both \|)$"
        before, matches = re.subn(row, rf"\g<1>{new}\g<2>", before, count=1)
        if matches != 1:
            return False
    return before == after


def classify_changes(root: Path, before_push: str = "") -> tuple[str, ...]:
    if before_push:
        if not re.fullmatch(r"[0-9a-f]{40}", before_push):
            return ()
        base, head = before_push, "HEAD"
        if subprocess.run(["git", "merge-base", "--is-ancestor", base, head], cwd=root, check=False).returncode != 0:
            return ()
    else:
        revision = git(root, "rev-list", "--parents", "-n", "1", "HEAD").split()
        if len(revision) != 3:
            return ()
        head, base, _ = revision

    diff = git(root, "diff", "--raw", "--no-renames", "-z", base, head, "--")
    entries = diff.split("\0")[:-1]
    changed = {path: metadata for metadata, path in zip(entries[::2], entries[1::2], strict=True)}
    if not changed.keys() & MANIFESTS.keys() or changed.keys() - (MANIFESTS.keys() | {COMPATIBILITY_DOC}):
        return ()
    if any(not record.startswith(":100644 100644 ") or not record.endswith(" M") for record in changed.values()):
        return ()

    bumps = {}
    for path in sorted(changed.keys() & MANIFESTS.keys()):
        bump = patch_bump(git(root, "show", f"{base}:{path}"), git(root, "show", f"{head}:{path}"))
        if bump is None:
            return ()
        bumps[MANIFESTS[path]] = bump
    if COMPATIBILITY_DOC in changed and not docs_match(
        git(root, "show", f"{base}:{COMPATIBILITY_DOC}"), git(root, "show", f"{head}:{COMPATIBILITY_DOC}"), bumps
    ):
        return ()
    return tuple(bumps)


def main() -> None:
    try:
        before_push = os.environ.get("PUSH_BEFORE", "")
        components = (
            ()
            if os.environ.get("GITHUB_EVENT_NAME") == "push" and not before_push
            else classify_changes(Path.cwd(), before_push)
        )
    except (subprocess.CalledProcessError, KeyError, AttributeError, ValueError) as exc:
        print(f"Could not classify Harness pins; using standard CI: {exc}", file=sys.stderr)
        components = ()
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        print("components=" + " ".join(components), file=output)
    print("Harness pin smoke: " + " ".join(components) if components else "Using standard CI")


if __name__ == "__main__":
    main()
