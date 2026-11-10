"""Measure real file preparation paths, with optional per-Session-RPC delay.

Run from the checkout: A13N_ENVD_TEST_BINARY=$PWD/target/debug/a13n-envd \
uv run --locked python dev/harness/benchmark_discovery.py --output /tmp/discovery.json
The old scanner/context modules are read from --baseline, not copied into the workspace.
Baseline scanner on the current transport isolates P1 from P2; it does not simulate old readiness.
Delay is injected before SessionRequester.request, once per RPC, including concurrent requests.
Response bytes count typed JSON result bodies, excluding transport envelopes and framing.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
from collections import Counter
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from a13n_envd_client.requester import SessionRequester
from a13n_harness import DefinitionError
from a13n_harness.capabilities import context, skills
from a13n_harness.environment.files import FileIgnoreMode, FileOperator, FileQueryRequest, FileTextSearchRequest
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.shared import _DirectLocalFilePolicy
from a13n_harness.providers.environment.eip.files import EIPFileOperator

ROOT = Path(__file__).resolve().parents[2]


def baseline_module(name, revision):
    path = f"packages/a13n-harness/a13n_harness/capabilities/{name}.py"
    source = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True)
    module = types.ModuleType(f"benchmark_baseline_{name}")
    sys.modules[module.__name__] = module
    tree = ast.parse(source)
    if name == "context":
        tree.body = [
            node
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            or (isinstance(node, ast.AsyncFunctionDef) and node.name == "_read_file_context")
        ]
        module.__dict__["FileContextConfiguration"] = context.FileContextConfiguration
    exec(compile(tree, f"{revision}:{path}", "exec"), module.__dict__)
    return module


def native_fixture():
    path = ROOT / "packages/a13n-envd-client/tests/eip/test_device_sessions.py"
    spec = importlib.util.spec_from_file_location("benchmark_device_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.running_device


class Meter:
    def __init__(self, delay):
        self.delay = delay
        self.calls = Counter()
        self.bytes = 0

    def instrument(self):
        original = SessionRequester.request

        async def request(requester, method, params):
            self.calls[method.name] += 1
            if self.delay:
                await asyncio.sleep(self.delay)
            result = await original(requester, method, params)
            self.bytes += len(result.model_dump_json().encode())
            return result

        return patch.object(SessionRequester, "request", request)

    def reset(self):
        self.calls.clear()
        self.bytes = 0


async def measure(rows, meter, label, operation, **dimensions):
    meter.reset()
    started = perf_counter()
    value = await operation
    row = {
        "case": label,
        **dimensions,
        "seconds": perf_counter() - started,
        "rpc": dict(meter.calls),
        "response_body_bytes": meter.bytes,
        "result_count": len(value),
    }
    rows.append(row)
    print(json.dumps(row), flush=True)
    return value


def seed(root):
    for count in (0, 10, 100, 250, 500):
        roots = 2 if count == 500 else 1
        for ordinal in range(roots):
            target = root / f"skills-{count}-{ordinal}"
            target.mkdir()
            for index in range(ordinal * 250 if roots == 2 else 0, (ordinal + 1) * 250 if roots == 2 else count):
                directory = target / f"s{index:04}"
                directory.mkdir()
                (directory / "SKILL.md").write_text(
                    f"---\nname: skill-{index}\ndescription: Representative bounded skill.\n---\nBody.\n"
                )
    for ordinal in range(4):
        target = root / f"stress-{ordinal}"
        target.mkdir()
        for index in range(250):
            directory = target / f"s{index:04}"
            directory.mkdir()
            (directory / "SKILL.md").write_text(
                f"---\nname: stress-{ordinal}-{index}\ndescription: Raised-bound stress.\n---\n"
            )
    for name in ("a", "invalid", "missing", "z"):
        directory = root / "edges" / name
        directory.mkdir(parents=True)
        if name != "missing":
            (directory / "SKILL.md").write_text(
                "not frontmatter"
                if name == "invalid"
                else "---\nname: duplicate\ndescription: Duplicate precedence.\n---\n"
            )
    for lines in (100, 200, 500, 1000):
        (root / f"lines-{lines}").write_text(("x" * 79 + "\n") * lines)
    (root / "multibyte").write_text(("中" * 79 + "\n") * 1000)
    (root / "long").write_text(("中" * 8000 + "\n") * 100)
    for shape, width, depth in (("shallow", 3, 1), ("wide", 32, 1), ("nested", 3, 3)):
        tree = root / shape
        tree.mkdir()
        pending = [tree]
        for _level in range(depth):
            children = []
            for parent in pending:
                for index in range(width):
                    directory = parent / f"d{index:02}"
                    directory.mkdir()
                    (directory / "AGENTS.md").write_text("guidance\n")
                    (directory / "file.txt").write_text("needle\n" * 3)
                    children.append(directory)
            pending = children
    tree = root / "query"
    tree.mkdir()
    (tree / ".gitignore").write_text("ignored/\n")
    for directory in ("a", "z", ".hidden", "ignored"):
        (tree / directory).mkdir()
        for index in range(50):
            (tree / directory / f"f{index:03}.txt").write_text("needle\n" * (10 if index % 2 else 1))
    (tree / "a.txt").write_text("needle\n")


async def suite(rows, meter, files, root, provider, old_skills, old_context, samples, cases):
    dimensions = {"provider": provider, "delay_ms": meter.delay * 1000}
    for count in (0, 10, 100, 250, 500) if "skills" in cases else ():
        roots = [str(root / f"skills-{count}-{i}") for i in range(2 if count == 500 else 1)]
        for stage, module in (("P1", old_skills), ("P2", skills)):
            manager = module.SkillManager((module.FileSkillSource("benchmark", roots),))
            for sample in range(samples):
                result = await measure(
                    rows,
                    meter,
                    "skills",
                    manager.scan(files=files),
                    stage=stage,
                    skills=count,
                    roots=len(roots),
                    sample=sample,
                    **dimensions,
                )
                assert len(result) == count
    for name in (
        ("lines-100", "lines-200", "lines-500", "lines-1000", "multibyte", "long") if "context" in cases else ()
    ):
        for stage, module in (("before", old_context), ("P1", context)):
            config = module.FileContextConfiguration()
            await measure(
                rows,
                meter,
                "context",
                module._read_file_context(files, str(root / name), config, config.max_bytes),
                stage=stage,
                fixture=name,
                **dimensions,
            )
    for shape in ("shallow", "wide", "nested"):
        selected = root / shape
        if "outline" in cases:
            config = context.WorkspaceOutlineConfiguration(root=str(selected))
            for sample in range(samples):
                await measure(
                    rows,
                    meter,
                    "outline",
                    context._scan_workspace_outline(files, config),
                    shape=shape,
                    stage="final",
                    sample=sample,
                    **dimensions,
                )
        if "guidance" in cases:

            async def root_list(path, selected=selected, **kwargs):
                return await files.list(str(selected) if path == "." else path, **kwargs)

            async def root_stat(path, selected=selected):
                return await files.stat(str(selected / path) if not path.startswith("/") else path)

            scoped = cast(FileOperator, SimpleNamespace(list=root_list, stat=root_stat))
            await measure(rows, meter, "guidance", context._scan_project_guidance(scoped), shape=shape, **dimensions)
    if "queries" in cases:
        for kind in ("glob", "search-dense", "search-sparse"):
            filters: tuple[tuple[bool, FileIgnoreMode], ...] = ((False, "none"), (True, "none"), (False, "git"))
            for offset in (0, 50, None):
                for hidden, ignore in filters:
                    await measure(
                        rows,
                        meter,
                        kind,
                        query_pages(files, root / "query", kind, offset, hidden, ignore),
                        offset=offset,
                        include_hidden=hidden,
                        ignore_mode=ignore,
                        **dimensions,
                    )
    if "edges" in cases:
        roots = [str(root / f"stress-{i}") for i in range(4)]
        for stage, module in (("P1", old_skills), ("final", skills)):
            edge = module.SkillManager(
                (module.FileSkillSource("edges", (str(root / "edges"), str(root / "missing")), required=False),)
            )
            result = await measure(
                rows, meter, "skills-invalid-duplicate-missing", edge.scan(files=files), stage=stage, **dimensions
            )
            assert len(result) == 1
            for raised in (False, True):
                manager = module.SkillManager(
                    (module.FileSkillSource("stress", roots),),
                    policy=module.SkillsPolicy(max_skills=1024 if raised else 512),
                )

                async def scan(manager=manager, raised=raised):
                    try:
                        return await manager.scan(files=files)
                    except DefinitionError as error:
                        assert not raised and error.code == "skill_catalog_too_large"
                        return (error.code,)

                await measure(rows, meter, "skills-stress", scan(), stage=stage, raised_bound=raised, **dimensions)


async def query_pages(files, root, kind, offset, hidden=False, ignore: FileIgnoreMode = "none"):
    complete = offset is None
    offset = offset or 0
    values = []
    while True:
        if kind == "glob":
            result = await files.query(
                FileQueryRequest(
                    root=str(root),
                    pattern="**/*.txt",
                    offset=offset,
                    max_results=50,
                    include_hidden=hidden,
                    ignore_mode=ignore,
                )
            )
            page = result.entries
        else:
            result = await files.search_text(
                FileTextSearchRequest(
                    root=str(root),
                    pattern="needle" if kind == "search-dense" else "absent",
                    offset=offset,
                    include_hidden=hidden,
                    ignore_mode=ignore,
                    max_matches=50,
                )
            )
            page = result.matches
        values.extend(page)
        if not complete or not result.has_more:
            return values
        assert page
        offset += len(page)


async def main(args):
    rows = []
    old_skills = baseline_module("skills", args.baseline)
    old_context = baseline_module("context", args.baseline)
    running_device = native_fixture()
    for provider in args.providers:
        with tempfile.TemporaryDirectory(prefix="discovery-benchmark-") as temp:
            directory = Path(temp)
            if provider == "local":
                root = directory / "alpha"
                root.mkdir()
                seed(root)
                files = LocalFileOperator(
                    root=root, policy=_DirectLocalFilePolicy(2 * 1024 * 1024), execution_id="benchmark", generation="1"
                )
                await suite(
                    rows, Meter(0), files, Path("/"), provider, old_skills, old_context, args.samples, args.cases
                )
            else:
                started = perf_counter()
                async with running_device(directory, provider) as device:
                    rows.append({"case": "cold-device", "provider": provider, "seconds": perf_counter() - started})
                    root = directory / "alpha"
                    seed(root)
                    started = perf_counter()
                    async with await device.open_session() as session:
                        rows.append({"case": "cold-session", "provider": provider, "seconds": perf_counter() - started})
                        files = EIPFileOperator(
                            session=session, environment_id="benchmark", execution_id="benchmark", generation="1"
                        )
                        for delay in args.delays:
                            meter = Meter(delay / 1000)
                            with meter.instrument():
                                await suite(
                                    rows,
                                    meter,
                                    files,
                                    root,
                                    provider,
                                    old_skills,
                                    old_context,
                                    args.samples,
                                    args.cases,
                                )
            args.output.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="64a84e1a94176be93be139d54fbad516a2f41ffe")
    parser.add_argument("--providers", nargs="+", default=["local", "http", "websocket"])
    parser.add_argument("--delays", nargs="+", type=float, default=[0, 50])
    parser.add_argument("--cases", nargs="+", default=["skills", "context", "outline", "guidance", "queries", "edges"])
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
