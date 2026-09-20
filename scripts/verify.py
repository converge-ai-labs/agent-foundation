"""Run the checks and tests that local changes can affect.

The selection follows the import graph: a changed Python module selects every
test file that imports it, directly or through other modules; a changed
frontend file selects the Vitest files that import it. Shared inputs such as
lock files or the root pytest configuration select everything.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import tempfile
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from scripts import impact

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = REPOSITORY_ROOT / "packages"
FRONTEND = REPOSITORY_ROOT / "frontend"
SCRIPTS = REPOSITORY_ROOT / "scripts"

# Files that change how every test runs.
GLOBAL_PYTHON_INPUTS = {"pyproject.toml", "uv.lock", "conftest.py", "scripts/run_python_tests.py", "Makefile"}
GLOBAL_FRONTEND_INPUTS = {"frontend/package.json", "frontend/pnpm-lock.yaml", "frontend/pnpm-workspace.yaml"}
FRONTEND_PROJECT_INPUTS = {"package.json", "vitest.config.ts", "vite.config.ts", "tsconfig.json", "tests/setup.ts"}
FRONTEND_SUFFIXES = {".ts", ".tsx", ".mjs", ".js", ".css", ".json"}
FRONTEND_PROJECTS = ("apps/a13n-console", "apps/a13n-harness-ui", "packages/a13n-ui")
COMPACT_PYTEST = "-q --tb=short --no-header"


@dataclass
class Plan:
    python_tests: set[str] = field(default_factory=set)
    consumer_tests: set[str] = field(default_factory=set)
    python_files: set[str] = field(default_factory=set)
    frontend_related: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    frontend_full: set[str] = field(default_factory=set)
    frontend_files: set[str] = field(default_factory=set)
    markdown_files: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    @property
    def frontend_projects(self) -> set[str]:
        return self.frontend_full | set(self.frontend_related)


# --------------------------------------------------------------------------- change discovery


def changed_files(base: str) -> list[str]:
    """Committed changes since the merge base plus everything in the working tree."""
    files: set[str] = set()
    commands = (
        ["git", "diff", "--name-only", "--no-renames", "--merge-base", base],
        ["git", "diff", "--name-only", "--no-renames", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    for command in commands:
        result = subprocess.run(command, cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False)
        if result.returncode:
            raise SystemExit(f"{' '.join(command)} failed: {result.stderr.strip()}")
        files.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(files)


# --------------------------------------------------------------------------- python import graph


@dataclass(frozen=True)
class Module:
    path: Path
    name: str  # dotted module name, "tests.*" names are scoped by distribution
    distribution: str | None  # packages/<distribution>, None for scripts


class PythonGraph:
    """Reverse import graph over workspace packages, their tests and scripts."""

    def __init__(self, root: Path = REPOSITORY_ROOT) -> None:
        self.root = root
        self.modules: dict[Path, Module] = {}
        self._by_name: dict[tuple[str | None, str], Path] = {}
        self._importers: dict[Path, set[Path]] = defaultdict(set)
        self._reexport_cache: dict[Path, dict[str, Path]] = {}
        for path, module in self._discover():
            self.modules[path] = module
            self._by_name[(module.distribution if module.name.startswith("tests") else None, module.name)] = path
        for path, module in self.modules.items():
            for target in self._imports(path, module):
                self._importers[target].add(path)

    def _discover(self) -> Iterable[tuple[Path, Module]]:
        packages = self.root / "packages"
        if packages.is_dir():
            for distribution in sorted(packages.iterdir()):
                if not distribution.is_dir():
                    continue
                for top in sorted(distribution.iterdir()):
                    if not top.is_dir() or top.name.startswith((".", "_")):
                        continue
                    for path in top.rglob("*.py"):
                        if "__pycache__" in path.parts:
                            continue
                        yield path, Module(path, self._dotted(path, top.parent), distribution.name)
        scripts = self.root / "scripts"
        if scripts.is_dir():
            for path in scripts.rglob("*.py"):
                if "__pycache__" not in path.parts:
                    yield path, Module(path, self._dotted(path, self.root), None)

    @staticmethod
    def _dotted(path: Path, base: Path) -> str:
        parts = list(path.relative_to(base).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def _resolve(self, name: str, module: Module) -> Path | None:
        scope = module.distribution if name == "tests" or name.startswith("tests.") else None
        candidate = name
        while candidate:
            path = self._by_name.get((scope, candidate))
            if path is not None:
                return path
            candidate, _, _ = candidate.rpartition(".")
        return None

    def _import_names(self, path: Path, module: Module) -> Iterable[tuple[str, list[tuple[str, str]]]]:
        """Yield (module name, [(imported name, bound name)]) for every import statement in the file."""
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            return
        package = module.name if path.name == "__init__.py" else module.name.rpartition(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield alias.name, []
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base_parts = package.split(".") if package else []
                    base = ".".join(base_parts[: len(base_parts) - node.level + 1])
                    prefix = f"{base}.{node.module}" if node.module else base
                else:
                    prefix = node.module or ""
                yield prefix, [(alias.name, alias.asname or alias.name) for alias in node.names]

    def _reexports(self, init: Path) -> dict[str, Path]:
        """Names a package ``__init__`` re-exports, mapped to the module that defines them."""
        cached = self._reexport_cache.get(init)
        if cached is not None:
            return cached
        mapping: dict[str, Path] = {}
        self._reexport_cache[init] = mapping  # guards against import cycles between inits
        module = self.modules[init]
        for prefix, names in self._import_names(init, module):
            for imported, bound in names:
                if imported == "*":
                    target = self._resolve(prefix, module)
                    if target is not None and target != init:
                        mapping.update(self._reexports(target) if target.name == "__init__.py" else {})
                    continue
                target = self._resolve(f"{prefix}.{imported}", module) or self._resolve(prefix, module)
                if target is not None and target != init:
                    mapping[bound] = target
        return mapping

    def _imports(self, path: Path, module: Module) -> set[Path]:
        targets: set[Path] = set()
        for prefix, names in self._import_names(path, module):
            base = self._resolve(prefix, module)
            if not names:
                if base is not None and base != path:
                    targets.add(base)
                continue
            for imported, _bound in names:
                submodule = self._resolve(f"{prefix}.{imported}", module)
                if submodule is not None and submodule != base:
                    target = submodule
                elif base is not None and base.name == "__init__.py" and imported in self._reexports(base):
                    # ``from package import Name`` depends on the module defining Name, not the whole package.
                    target = self._reexports(base)[imported]
                else:
                    target = base
                if target is not None and target != path:
                    targets.add(target)
        return targets

    def affected_tests(self, changed: Iterable[Path]) -> set[Path]:
        """Every test file that transitively imports one of the changed modules."""
        seen: set[Path] = set()
        queue = deque(path for path in changed if path in self.modules)
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self._importers.get(current, ()))
        return {path for path in seen if path.name.startswith("test_") and path.name.endswith(".py")}


# --------------------------------------------------------------------------- planning


def _distribution_tests(path: Path) -> str | None:
    try:
        relative = path.relative_to(PACKAGES)
    except ValueError:
        return None
    tests = PACKAGES / relative.parts[0] / "tests"
    return tests.relative_to(REPOSITORY_ROOT).as_posix() if tests.is_dir() else None


def plan(files: Iterable[str], graph: PythonGraph | None = None, *, consumers: bool = False) -> Plan:
    result = Plan()
    files = list(files)
    python_sources: list[Path] = []
    package_python: list[Path] = []
    python_full = False
    frontend_full = False

    for relative in files:
        path = REPOSITORY_ROOT / relative
        posix = Path(relative).as_posix()
        if posix.endswith(".md") and path.is_file():
            result.markdown_files.add(posix)
        if posix in GLOBAL_PYTHON_INPUTS:
            python_full = True
            result.notes.append(f"{posix} changes every Python suite")
        elif posix.startswith("scripts/"):
            if posix.endswith(".py") and path.is_file():
                result.python_files.add(posix)
                python_sources.append(path)
            result.python_tests.add("scripts/tests")
        elif posix.startswith("packages/"):
            tests_dir = _distribution_tests(path)
            if posix.endswith(".py") and path.is_file():
                result.python_files.add(posix)
            if (
                tests_dir
                and posix.startswith(tests_dir + "/")
                and path.suffix == ".py"
                and path.name.startswith("test_")
                and path.is_file()
            ):
                # Recorded node IDs cannot cover newly added, renamed or reparametrized tests.
                result.python_tests.add(posix)
            if path.suffix == ".py" and tests_dir and path.name != "conftest.py" and "/migrations/" not in posix:
                package_python.append(path)
            elif tests_dir and posix.startswith(tests_dir + "/"):
                scope = path.parent
                while not scope.is_dir():
                    scope = scope.parent
                result.python_tests.add(scope.relative_to(REPOSITORY_ROOT).as_posix())
            elif path.suffix == ".py":
                python_sources.append(path)
                if "/migrations/" in posix and tests_dir:
                    result.python_tests.add(f"{tests_dir}/database")
            elif tests_dir:
                result.python_tests.add(tests_dir)
                result.notes.append(f"{posix} is a non-Python package input; running {tests_dir}")
        elif posix.startswith("frontend/"):
            if posix in GLOBAL_FRONTEND_INPUTS:
                frontend_full = True
                result.notes.append(f"{posix} changes every frontend project")
                continue
            project = next((p for p in FRONTEND_PROJECTS if posix.startswith(f"frontend/{p}/")), None)
            if project is None or "/node_modules/" in posix:
                continue
            inside = posix[len(f"frontend/{project}/") :]
            if inside in FRONTEND_PROJECT_INPUTS:
                result.frontend_full.add(project)
            elif path.suffix in FRONTEND_SUFFIXES and inside.startswith(("src/", "tests/")):
                if not path.is_file():
                    result.frontend_full.update(FRONTEND_PROJECTS if project.startswith("packages/") else (project,))
                    continue
                result.frontend_files.add(posix)
                # Shared packages are imported by the applications, so every project checks relatedness.
                for candidate in FRONTEND_PROJECTS:
                    result.frontend_related[candidate].add(posix)

    mapped = _impact_selection(package_python, result, consumers=consumers) if package_python else set()
    for path in package_python:
        posix = path.relative_to(REPOSITORY_ROOT).as_posix()
        if path in mapped:
            continue
        if path.name.startswith("test_") and posix.startswith(f"{_distribution_tests(path)}/") and path.is_file():
            result.python_tests.add(posix)
        else:
            python_sources.append(path)

    if python_sources:
        graph = graph or PythonGraph()
        for source in python_sources:
            if source not in graph.modules:
                tests_dir = _distribution_tests(source)
                if tests_dir:
                    result.python_tests.add(tests_dir)
                    result.notes.append(
                        f"no impact map for deleted source {source.relative_to(REPOSITORY_ROOT)}; running {tests_dir}"
                    )
                continue
            owner = graph.modules[source].distribution
            affected = graph.affected_tests([source])
            if not affected:
                tests_dir = _distribution_tests(source) or ("scripts/tests" if source.is_relative_to(SCRIPTS) else None)
                if tests_dir and not any(t.startswith(tests_dir) for t in result.python_tests):
                    result.python_tests.add(tests_dir)
                    result.notes.append(
                        f"no test imports {source.relative_to(REPOSITORY_ROOT).as_posix()}; running {tests_dir}"
                    )
                continue
            for path in affected:
                relative = path.relative_to(REPOSITORY_ROOT).as_posix()
                # Consumers of a shared package are widened in explicitly; CI runs them regardless.
                if consumers or graph.modules[path].distribution == owner:
                    result.python_tests.add(relative)
                else:
                    result.consumer_tests.add(relative)

    result.consumer_tests -= result.python_tests
    if result.consumer_tests:
        by_owner: dict[str, int] = defaultdict(int)
        for relative in result.consumer_tests:
            by_owner[relative.split("/")[1] if relative.startswith("packages/") else "scripts"] += 1
        summary = ", ".join(f"{count} in {owner}" for owner, count in sorted(by_owner.items()))
        result.notes.append(f"downstream tests not selected ({summary}); pass --consumers to include them")

    if python_full:
        result.python_tests = {"ALL"}
    if frontend_full:
        result.frontend_full = set(FRONTEND_PROJECTS)
    for project in result.frontend_full:
        result.frontend_related.pop(project, None)
    _collapse_python_selection(result)
    return result


def _impact_selection(paths: list[Path], result: Plan, *, consumers: bool) -> set[Path]:
    """Select node ids from recorded impact maps; returns the paths the owning package's map covered."""
    handled: set[Path] = set()
    posix = {path: path.relative_to(REPOSITORY_ROOT).as_posix() for path in paths}
    owners = {path: posix[path].split("/")[1] for path in paths}
    maps = {package: impact.find_map(package) for package in impact.packages_with_tests()}
    for package in sorted(set(owners.values())):
        if maps.get(package) is None:
            result.notes.append(f"no impact map for {package}; selecting by import graph (make impact-record)")
    for package, found in sorted(maps.items()):
        if found is None:
            continue
        selection = impact.select(found, posix.values())
        own = package in owners.values()
        if own:
            handled.update(path for path in paths if owners[path] == package and posix[path] not in selection.unmapped)
            for path in sorted(selection.cosmetic):
                result.notes.append(f"{path}: comment, docstring, or formatting change only")
            if found.distance < 0:
                result.notes.append(
                    f"{package} impact map was recorded outside this history (rebase?); "
                    "selection also covers the upstream difference"
                )
            elif found.distance > impact.STALE_COMMITS:
                result.notes.append(
                    f"{package} impact map is {found.distance} commits behind HEAD; run make impact-record"
                )
        if selection.widened:
            result.notes.append(f"{package}: the impact map selects most of the suite; running all of it")
        if own or consumers:
            result.python_tests.update(selection.tests)
        else:
            result.consumer_tests.update(selection.tests)
    return handled


def _collapse_python_selection(result: Plan) -> None:
    """Drop entries already covered by a selected parent directory or test file."""
    selected = sorted(result.python_tests, key=len)
    kept: list[str] = []
    for entry in selected:
        if any(entry == parent or entry.startswith((parent + "/", parent + "::")) for parent in kept):
            continue
        kept.append(entry)
    result.python_tests = set(kept)


# --------------------------------------------------------------------------- execution


@dataclass
class Step:
    name: str
    command: list[str]
    cwd: Path = REPOSITORY_ROOT
    env: dict[str, str] | None = None


def steps_for(result: Plan) -> list[Step]:
    steps: list[Step] = []
    python_files = sorted(result.python_files)
    if python_files:
        steps.append(Step("ruff check", ["uv", "run", "--locked", "ruff", "check", "--no-fix", *python_files]))
        steps.append(Step("ruff format", ["uv", "run", "--locked", "ruff", "format", "--check", *python_files]))
        typed = [f for f in python_files if not f.startswith("packages/") or "/tests/" not in f]
        if typed:
            steps.append(Step("pyright", ["uv", "run", "--locked", "pyright", *typed]))
    if result.markdown_files:
        steps.append(
            Step(
                "mdformat", ["uv", "run", "--locked", "mdformat", "--check", "--number", *sorted(result.markdown_files)]
            )
        )
    frontend_files = sorted(result.frontend_files)
    if frontend_files:
        relative = [f[len("frontend/") :] for f in frontend_files]
        steps.append(Step("prettier", ["pnpm", "exec", "prettier", "--check", *relative], cwd=FRONTEND))
    for project in sorted(result.frontend_projects):
        if (
            any(f.startswith(f"frontend/{project}/") and f.endswith((".ts", ".tsx")) for f in frontend_files)
            or project in result.frontend_full
        ):
            steps.append(
                Step(f"typecheck {project}", ["pnpm", "--filter", f"./{project}", "run", "typecheck"], cwd=FRONTEND)
            )
    if result.python_tests:
        env = {**os.environ, "PYTEST_ADDOPTS": f"{os.environ.get('PYTEST_ADDOPTS', '')} {COMPACT_PYTEST}".strip()}
        if result.python_tests == {"ALL"}:
            steps.append(Step("python tests (all)", ["make", "test"], env=env))
        else:
            entries = sorted(result.python_tests)
            if any("::" in entry for entry in entries):
                # Node ids may contain spaces and brackets, so the runner reads them from a file.
                with tempfile.NamedTemporaryFile("w", prefix="a13n-verify-", suffix=".txt", delete=False) as listing:
                    listing.write("\n".join(entries) + "\n")
                selection = f"@{listing.name}"
            else:
                selection = " ".join(entries)
            steps.append(Step("python tests", ["make", "test", f"PYTHON_TEST_DIRS={selection}"], env=env))
    for project in sorted(result.frontend_full):
        steps.append(
            Step(
                f"vitest {project}",
                ["pnpm", "--filter", f"./{project}", "exec", "vitest", "run", "--reporter=dot"],
                cwd=FRONTEND,
            )
        )
    for project, files in sorted(result.frontend_related.items()):
        related = [str(REPOSITORY_ROOT / f) for f in sorted(files)]
        steps.append(
            Step(
                f"vitest related {project}",
                [
                    "pnpm",
                    "--filter",
                    f"./{project}",
                    "exec",
                    "vitest",
                    "related",
                    *related,
                    "--run",
                    "--reporter=dot",
                    "--passWithNoTests",
                ],
                cwd=FRONTEND,
            )
        )
    return steps


def full_steps() -> list[Step]:
    return [
        Step("lint", ["make", "lint"]),
        Step("typecheck", ["make", "typecheck"]),
        Step("frontend check", ["make", "frontend-check"]),
        Step("python tests (all)", ["make", "test"]),
        Step("frontend tests", ["make", "frontend-test"]),
    ]


def run(steps: list[Step], *, dry_run: bool) -> int:
    failures = 0
    for step in steps:
        shown = " ".join(step.command)
        if len(shown) > 200:
            shown = shown[:197] + "..."
        print(f"\n==> {step.name}: {shown}", flush=True)
        if dry_run:
            continue
        started = time.monotonic()
        result = subprocess.run(step.command, cwd=step.cwd, env=step.env, check=False)
        elapsed = time.monotonic() - started
        status = "ok" if result.returncode == 0 else f"FAILED ({result.returncode})"
        print(f"<== {step.name}: {status} in {elapsed:.1f}s", flush=True)
        failures += result.returncode != 0
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Ref whose merge base bounds committed changes")
    parser.add_argument("--full", action="store_true", help="Run the complete lint, type, and test gates instead")
    parser.add_argument("--dry-run", action="store_true", help="Print the selected steps without running them")
    parser.add_argument(
        "--consumers", action="store_true", help="Also run tests in other packages that import the changed modules"
    )
    parser.add_argument("paths", nargs="*", help="Treat these paths as the change set instead of consulting git")
    args = parser.parse_args(argv)

    if args.full:
        return run(full_steps(), dry_run=args.dry_run)

    files = args.paths or changed_files(args.base)
    if not files:
        print("no local changes; nothing to verify")
        return 0
    result = plan(files, consumers=args.consumers)
    print(f"{len(files)} changed file(s)")
    for note in result.notes:
        print(f"  note: {note}")
    if result.python_tests:
        targets = (
            "every Python suite"
            if result.python_tests == {"ALL"}
            else f"{len(result.python_tests)} Python test target(s)"
        )
        print(f"  selected: {targets}")
    for project in sorted(result.frontend_projects):
        scope = (
            "full run"
            if project in result.frontend_full
            else f"{len(result.frontend_related[project])} related source(s)"
        )
        print(f"  selected: {project} ({scope})")
    steps = steps_for(result)
    if not steps:
        print("changes touch no verifiable inputs")
        return 0
    return run(steps, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
