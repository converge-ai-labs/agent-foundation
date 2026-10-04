"""Content-aware manifest and lockfile selection for local verification."""

from __future__ import annotations

import json
import subprocess
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

FRONTEND_PROJECTS = ("apps/a13n-console", "apps/a13n-harness-ui", "apps/a13n-site", "packages/a13n-ui")
PYTHON_METADATA = {
    "version",
    "description",
    "readme",
    "license",
    "license-files",
    "authors",
    "maintainers",
    "keywords",
    "classifiers",
    "urls",
}
NODE_METADATA = {
    "version",
    "description",
    "private",
    "license",
    "author",
    "contributors",
    "homepage",
    "repository",
    "bugs",
    "keywords",
}


def documents(root: Path, path: str, base: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Unknown, added, deleted or malformed inputs must retain conservative selection."""
    previous = subprocess.run(["git", "show", f"{base}:{path}"], cwd=root, capture_output=True, text=True, check=False)
    if previous.returncode:
        return None
    parser = json.loads if path.endswith(".json") else yaml.safe_load if path.endswith(".yaml") else tomllib.loads
    try:
        old, new = parser(previous.stdout), parser((root / path).read_text())
    except (OSError, ValueError, yaml.YAMLError):
        return None
    return (old, new) if isinstance(old, dict) and isinstance(new, dict) else None


def changed_keys(old: dict, new: dict) -> set[str]:
    return {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}


def python_manifest(old: dict, new: dict) -> tuple[bool, set[str]]:
    """Return runtime/test impact and static checks, ignoring descriptive metadata."""
    checks: set[str] = set()
    remaining = []
    for document in (old, new):
        document = dict(document)
        document["project"] = {k: v for k, v in document.get("project", {}).items() if k not in PYTHON_METADATA}
        tool = dict(document.get("tool", {}))
        for name in ("ruff", "pyright", "deptry"):
            if old.get("tool", {}).get(name) != new.get("tool", {}).get(name):
                checks.add(name)
            tool.pop(name, None)
        document["tool"] = tool
        remaining.append(document)
    return remaining[0] != remaining[1], checks


def closure(names: set[str], edges: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name not in seen:
            seen.add(name)
            pending.extend(edges.get(name, ()))
    return seen


def python_lock(old: dict, new: dict) -> set[str] | None:
    """Owning suites of changed resolved dependencies, including transitive consumers.

    Root development dependencies affect the shared test environment. Unknown lock
    structure or resolution settings return None (all suites), never an empty guess.
    """
    if changed_keys(old, new) - {"package"} or not all(isinstance(d.get("package"), list) for d in (old, new)):
        return None
    records: list[dict[str, list[dict]]] = []
    edges: dict[str, set[str]] = defaultdict(set)
    owners: dict[str, str] = {}
    roots: set[str] = set()
    for document in (old, new):
        by_name: dict[str, list[dict]] = defaultdict(list)
        for entry in document["package"]:
            name = entry["name"]
            by_name[name].append(entry)
            dependencies = list(entry.get("dependencies", []))
            for group in ("optional-dependencies", "dev-dependencies"):
                for values in entry.get(group, {}).values():
                    dependencies.extend(values)
            edges[name].update(dependency["name"] for dependency in dependencies)
            source = entry.get("source", {})
            local = source.get("editable", source.get("virtual", ""))
            if local.startswith("packages/"):
                owners[name] = f"{local}/tests"
            elif local == ".":
                roots.add(name)
        records.append(by_name)
    changed = changed_keys(*records)
    # The root depends on workspace members for development; those are governed by
    # their own dependency closure, not a reason to rerun every unrelated package.
    root_edges = {name: values - owners.keys() for name, values in edges.items() if name not in owners}
    if changed & closure(roots, root_edges):
        return None
    return {tests for name, tests in owners.items() if changed & closure({name}, edges)}


def frontend_lock(old: dict, new: dict) -> set[str] | None:
    """Follow pnpm importer and snapshot edges, including peer-resolution identities."""
    if changed_keys(old, new) - {"importers", "packages", "snapshots"}:
        return None
    if not all(isinstance(d.get("importers"), dict) and isinstance(d.get("snapshots"), dict) for d in (old, new)):
        return None
    changed = changed_keys(old.get("snapshots", {}), new.get("snapshots", {}))
    changed_packages = changed_keys(old.get("packages", {}), new.get("packages", {}))
    selected = changed_keys(old["importers"], new["importers"])
    edges: dict[str, set[str]] = defaultdict(set)
    for document in (old, new):
        for name, entry in document["snapshots"].items():
            if name.split("(", 1)[0] in changed_packages:
                changed.add(name)
            for group in ("dependencies", "optionalDependencies"):
                edges[name].update(f"{dep}@{version}" for dep, version in entry.get(group, {}).items())
        for importer, entry in document["importers"].items():
            dependencies = {
                f"{dep}@{value['version']}"
                for group in ("dependencies", "devDependencies", "optionalDependencies")
                for dep, value in entry.get(group, {}).items()
            }
            if changed & closure(dependencies, edges):
                selected.add(importer)
    if "." in selected or selected - set(FRONTEND_PROJECTS):
        return None
    if "packages/a13n-ui" in selected:
        selected.update(FRONTEND_PROJECTS)
    return selected


def export_targets(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value[2:]} if value.startswith("./") else set()
    if isinstance(value, dict):
        return set().union(*(export_targets(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(export_targets(item) for item in value))
    return set()
