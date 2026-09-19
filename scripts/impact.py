"""Test impact maps: record which functions each test executes, then select tests for a diff.

Recording runs a package suite under pytest-xdist with coverage dynamic contexts and stores a
pytest-tia map keyed by the commit it was recorded at, in the machine cache shared by every
worktree. Selection diffs the working tree against that commit and picks the tests that executed
a changed function; a module-level edit selects every test that executed anything in the file.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MAP_VARIABLE = "A13N_IMPACT_MAP"
COVERAGE_VARIABLE = "A13N_IMPACT_COVERAGE"
KEEP_MAPS = 5
SEARCH_COMMITS = 500
STALE_COMMITS = 50
# Above this share of the suite, one xdist run over the whole package balances better than a list.
WIDEN_RATIO = 0.5


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False)
    if result.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def cache_dir() -> Path:
    """Maps live outside every checkout, keyed by the repository's root commit."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "a13n" / "impact" / _git("rev-list", "--max-parents=0", "HEAD").split()[-1]


def map_path(package: str, commit: str) -> Path:
    return cache_dir() / package / f"{commit}.json.gz"


def packages_with_tests() -> list[str]:
    return sorted(path.parent.name for path in (REPOSITORY_ROOT / "packages").glob("*/tests"))


# --------------------------------------------------------------------------- selection


@dataclass(frozen=True)
class ImpactMap:
    package: str
    commit: str
    distance: int
    tests: dict[str, dict[str, set[str]]]
    funcmaps: dict[str, dict[int, str]]
    dynamic: dict[str, list[str]]

    @property
    def tests_dir(self) -> str:
        return f"packages/{self.package}/tests"


@dataclass
class Selection:
    tests: set[str] = field(default_factory=set)
    widened: bool = False
    cosmetic: set[str] = field(default_factory=set)
    unmapped: set[str] = field(default_factory=set)


def load_map(path: Path, package: str, distance: int) -> ImpactMap:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    return ImpactMap(
        package,
        data["ref"],
        distance,
        tests={nodeid: {f: set(q) for f, q in files.items()} for nodeid, files in data["tests"].items()},
        funcmaps={p: {int(line): q for line, q in table.items()} for p, table in data["funcmaps"].items()},
        dynamic=data.get("dynamic", {}),
    )


def find_map(package: str) -> ImpactMap | None:
    """The map recorded at the nearest ancestor of HEAD; the diff base is that commit.

    After a rebase no map is an ancestor, so the newest map whose commit still exists is used
    instead (distance -1): diffing against it also covers the upstream changes, which over-selects
    rather than misses.
    """
    directory = cache_dir() / package
    if not directory.is_dir():
        return None
    for distance, commit in enumerate(_git("rev-list", f"--max-count={SEARCH_COMMITS}", "HEAD").split()):
        path = directory / f"{commit}.json.gz"
        if path.exists():
            return load_map(path, package, distance)
    for path in sorted(directory.glob("*.json.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
        exists = subprocess.run(
            ["git", "cat-file", "-e", f"{path.name.split('.')[0]}^{{commit}}"], cwd=REPOSITORY_ROOT, check=False
        )
        if exists.returncode == 0:
            return load_map(path, package, -1)
    return None


def select(impact: ImpactMap, files: Iterable[str]) -> Selection:
    """Tests to run for the changed Python files, as node ids or the widened package directory."""
    from tia import diff, resolve, semantic
    from tia import select as tia_select

    result = Selection()
    wanted = {path for path in files if path.endswith(".py")}
    result.unmapped = {path for path in wanted if path not in impact.funcmaps}
    changed = {p: k for p, k in diff.changed_lines(impact.commit, cwd=str(REPOSITORY_ROOT)).items() if p in wanted}
    for path in list(changed):
        old = resolve._git_show(impact.commit, path, str(REPOSITORY_ROOT))
        if old is not None and not semantic.is_semantic_change(old, (REPOSITORY_ROOT / path).read_text("utf-8")):
            result.cosmetic.add(path)
            del changed[path]
    func_changes, module_files = resolve.changed_functions(
        changed, impact.commit, str(REPOSITORY_ROOT), impact.funcmaps
    )
    for path, kinds in changed.items():
        # tia ignores module-level insertions; a new import or registration is a real change.
        if any(impact.funcmaps[path].get(line) is None for line in kinds["ins"]):
            module_files.add(path)
    module_files, _ = tia_select.escalate_dynamic(func_changes, module_files, impact.dynamic)
    selected = tia_select.select_tests(impact.tests, func_changes, module_files, set())
    if len(selected) > WIDEN_RATIO * len(impact.tests):
        result.tests = {impact.tests_dir}
        result.widened = True
    else:
        result.tests = set(selected)
    return result


# --------------------------------------------------------------------------- recording plugin


def _nodeid(item: pytest.Item) -> str:
    """The node id without the ``@group`` suffix xdist appends under loadgroup scheduling."""
    nodeid = item.nodeid
    marker = item.get_closest_marker("xdist_group")
    if marker is not None:
        group = marker.args[0] if marker.args else marker.kwargs.get("name")
        if group and nodeid.endswith(f"@{group}"):
            nodeid = nodeid[: -len(group) - 1]
    return nodeid


class _Recorder:
    """Attributes every line a test executes (setup, call, teardown) to its node id."""

    def __init__(self, data_file: str) -> None:
        import coverage

        self.cov = coverage.Coverage(
            data_file=data_file,
            data_suffix=True,
            branch=False,
            source=[str(REPOSITORY_ROOT)],
            omit=[str(REPOSITORY_ROOT / ".venv" / "*"), str(REPOSITORY_ROOT / "var" / "*")],
            config_file=False,
        )
        self.cov.start()

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(self, item: pytest.Item):
        self.cov.switch_context(_nodeid(item))
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item):
        self.cov.switch_context(_nodeid(item))
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(self, item: pytest.Item):
        try:
            return (yield)
        finally:
            self.cov.switch_context("")

    def pytest_sessionfinish(self) -> None:
        self.cov.stop()
        self.cov.save()


class _Controller:
    """Combines the per-process coverage data into one map after the session."""

    def __init__(self, data_file: str, target: Path) -> None:
        self.data_file = data_file
        self.target = target

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self) -> None:
        write_map(self.data_file, self.target)


def pytest_configure(config: pytest.Config) -> None:
    target = os.environ.get(MAP_VARIABLE)
    if not target:
        return
    data_file = os.environ[COVERAGE_VARIABLE]
    worker = hasattr(config, "workerinput")
    if worker or not config.getoption("numprocesses", default=0):
        config.pluginmanager.register(_Recorder(data_file), "a13n-impact-recorder")
    if not worker:
        config.pluginmanager.register(_Controller(data_file, Path(target)), "a13n-impact-controller")


def write_map(data_file: str, target: Path) -> None:
    import coverage
    from tia import astmap, dynscan

    cov = coverage.Coverage(data_file=data_file, config_file=False)
    cov.combine()
    data = cov.get_data()
    lines: dict[str, dict[str, set[int]]] = {}
    for measured in data.measured_files():
        path = Path(measured)
        # Generated modules (Alembic's mako templates, for example) report names that are not files.
        if not path.is_relative_to(REPOSITORY_ROOT) or path.suffix != ".py" or not path.is_file():
            continue
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        for lineno, contexts in data.contexts_by_lineno(measured).items():
            for context in contexts:
                if context:
                    lines.setdefault(context, {}).setdefault(relative, set()).add(lineno)
    funcmaps: dict[str, dict[int, str]] = {}
    tests: dict[str, dict[str, list[str]]] = {}
    for nodeid, files in sorted(lines.items()):
        tests[nodeid] = {}
        for relative, numbers in files.items():
            if relative not in funcmaps:
                try:
                    funcmaps[relative] = astmap.line_to_qualname_from_file(str(REPOSITORY_ROOT / relative))
                except (SyntaxError, OSError):
                    funcmaps[relative] = {}
            table = funcmaps[relative]
            tests[nodeid][relative] = sorted({table[n] for n in numbers if n in table})
    dynamic = {}
    for relative in funcmaps:
        markers = dynscan.find_markers((REPOSITORY_ROOT / relative).read_text("utf-8"))
        if markers:
            dynamic[relative] = markers
    document = {
        "version": 5,
        "ref": _git("rev-parse", "HEAD").strip(),
        "tests": tests,
        "reads": {},
        "funcmaps": {p: {str(n): q for n, q in sorted(t.items())} for p, t in sorted(funcmaps.items())},
        "dynamic": dynamic,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(target, "wt", encoding="utf-8") as handle:
        json.dump(document, handle)
    for stale in sorted(target.parent.glob("*.json.gz"), key=lambda p: p.stat().st_mtime)[:-KEEP_MAPS]:
        stale.unlink()
    print(f"\nimpact map: {len(tests)} tests -> {target}", flush=True)


# --------------------------------------------------------------------------- commands


def record(packages: list[str], workers: int | None) -> int:
    if _git("status", "--porcelain", "--untracked-files=no").strip():
        raise SystemExit("commit or stash tracked changes first: impact maps are keyed by the recorded commit")
    commit = _git("rev-parse", "HEAD").strip()
    missing: list[str] = []
    for package in packages:
        target = map_path(package, commit)
        with tempfile.TemporaryDirectory() as scratch:
            env = {
                **os.environ,
                MAP_VARIABLE: str(target),
                COVERAGE_VARIABLE: str(Path(scratch) / "coverage"),
                # The sysmon core keeps only the first context per line; the C tracer keeps them all.
                "COVERAGE_CORE": "ctrace",
                "PYTHONPATH": os.pathsep.join(p for p in (str(REPOSITORY_ROOT), os.environ.get("PYTHONPATH")) if p),
                "PYTEST_ADDOPTS": f"{os.environ.get('PYTEST_ADDOPTS', '')} -p scripts.impact".strip(),
            }
            command = [sys.executable, "-m", "scripts.run_python_tests", f"packages/{package}/tests"]
            if workers is not None:
                command[3:3] = ["--workers", str(workers)]
            result = subprocess.run(command, cwd=REPOSITORY_ROOT, env=env, check=False)
        if not target.exists():
            missing.append(package)
        elif result.returncode:
            print(f"{package}: some tests failed; the map still records them", flush=True)
    for package in missing:
        print(f"{package}: no map written", file=sys.stderr)
    return 1 if missing else 0


def status() -> int:
    for package in packages_with_tests():
        impact = find_map(package)
        if impact is None:
            print(f"{package}: no map")
        else:
            where = "outside this history" if impact.distance < 0 else f"{impact.distance} commit(s) behind"
            print(f"{package}: {len(impact.tests)} tests recorded at {impact.commit[:12]}, {where}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    recorder = commands.add_parser("record", help="run package suites and store their impact maps")
    recorder.add_argument("packages", nargs="*", help="Package directory names (default: every package with tests)")
    recorder.add_argument("--workers", type=int, help="Override the suite worker count")
    commands.add_parser("status", help="show the map each package would use")
    args = parser.parse_args(argv)
    if args.command == "record":
        return record(args.packages or packages_with_tests(), args.workers)
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
