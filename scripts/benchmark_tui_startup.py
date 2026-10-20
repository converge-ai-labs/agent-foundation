"""Measure configured TUI backend readiness and first-use costs in fresh processes.

Uses disposable SQLite/configuration directories, no model calls, and no terminal
rendering. Resume reads a real empty Thread seeded by a separate process. Optional
--source-root selects a git-archive snapshot of all six packages with the same
installed dependencies. Run serially on an idle host; timings are not test gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configuration(root: Path) -> None:
    (root / "workspace").mkdir()
    files = {
        "a13n-harness-ui.yaml": 'schema_version: "1"\nprocess:\n  pricing_auto_update: false\n  terminal_update_check: false\ndefaults:\n  project: project-main\n  agent: agent-assistant\n',
        "models/primary.yaml": 'schema_version: "1"\nkind: model\nid: model-primary\nname: Primary\nroute: openai:gpt-5\nauthentication: {kind: api_key, env: BENCHMARK_UNUSED_API_KEY}\n',
        "agents/assistant.yaml": 'schema_version: "1"\nkind: agent\nid: agent-assistant\nname: Assistant\nmodel: model-primary\ninstructions: Help the user.\n',
        "projects/main.yaml": f'schema_version: "1"\nkind: project\nid: project-main\nname: Main\nroots:\n  - path: {root / "workspace"}\n',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(content)


async def worker(root: Path, scenario: str) -> dict:
    import asyncio
    import resource

    started, cpu = time.perf_counter(), time.process_time()
    from a13n_harness_ui.cli import CliRequest
    from a13n_harness_ui.interactive.rendering import Status
    from a13n_harness_ui.interactive.startup import _load_runtime

    factory = await asyncio.to_thread(_load_runtime)
    request = CliRequest(
        config_path=root / "a13n-harness-ui.yaml",
        data_root=root / "data",
        no_update_check=True,
        thread_id=(root / "thread-id").read_text() if scenario == "resume" else None,
    )
    async with factory(request, root / "workspace", Status(), lambda _: None) as backend:
        assert await backend.initialize(), "Configured backend must be ready"
        result = {
            "ready_s": time.perf_counter() - started,
            "ready_cpu_s": time.process_time() - cpu,
            "ready_modules": len(sys.modules),
            "ready_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        if scenario == "seed":
            await backend.ensure_session()
            assert backend.thread_id is not None
            (root / "thread-id").write_text(backend.thread_id)
            return result
        if scenario == "resume":
            assert backend.thread_id == (root / "thread-id").read_text()
            assert backend.resumed_transcript is not None
        started = time.perf_counter()
        for _ in range(5):
            assert await backend.refresh()
        result["refresh_s"] = (time.perf_counter() - started) / 5

        def sandbox():
            from pydantic_monty import Monty

            with Monty() as pool:
                with pool.checkout(script_name="benchmark.py") as session:
                    assert session.feed_start("1 + 1").output == 2

        def image():
            import base64

            from a13n_harness.filters.image import _prepare_binary_image
            from a13n_harness.image_input import ImageInputPolicy
            from pydantic_ai import BinaryContent

            return _prepare_binary_image(
                BinaryContent(
                    data=base64.b64decode(
                        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="
                    ),
                    media_type="image/png",
                ),
                ImageInputPolicy(),
            )

        async def mcp():
            from a13n_harness_ui.composition.models import ResolvedMcpRecipe
            from a13n_harness_ui.configuration.models import McpCommandTransport
            from a13n_harness_ui.mcp_adapters import prepare_mcp_transport

            recipe = ResolvedMcpRecipe(server_id="benchmark", transport=McpCommandTransport(command=sys.executable))
            transport, _, _ = await prepare_mcp_transport(recipe, None)
            await transport.close()

        for name, operation in (("sandbox", sandbox), ("image", image), ("mcp_transport", mcp)):
            for phase in ("first", "warm"):
                started, cpu = time.perf_counter(), time.process_time()
                if name == "mcp_transport":
                    await operation()
                else:
                    await asyncio.to_thread(operation)
                result[f"{name}_{phase}_s"] = time.perf_counter() - started
                result[f"{name}_{phase}_cpu_s"] = time.process_time() - cpu
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--eager-imports", action="store_true")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", choices=("new", "resume", "seed"))
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.eager_imports:
        sys.set_lazy_imports_filter(lambda *args: False)
    if args.worker:
        import asyncio

        print(json.dumps(asyncio.run(worker(args.root, args.worker))))
        return
    if args.samples < 1 or args.output is None:
        parser.error("Positive --samples and --output are required")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("A13N_")}
    environment.update(PYTHONHASHSEED="0", PYDANTIC_AI_NO_BANNER="1", OTEL_SDK_DISABLED="true")
    if args.source_root:
        environment["PYTHONPATH"] = os.pathsep.join(
            str(path) for path in sorted((args.source_root / "packages").glob("*"))
        )
    command = [args.python, str(Path(__file__).resolve())]
    if args.eager_imports:
        command.append("--eager-imports")

    def run(root: Path, scenario: str) -> dict:
        start = time.perf_counter()
        completed = subprocess.run(
            [*command, "--worker", scenario, "--root", str(root)],
            env=environment,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=120,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout + completed.stderr)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        result["process_wall_s"] = time.perf_counter() - start
        return result

    report = {
        "python": subprocess.check_output([args.python, "--version"], text=True).strip(),
        "platform": platform.platform(),
        "source_root": str(args.source_root or ROOT),
        "mode": "filter-disabled" if args.eager_imports else "normal",
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "samples": args.samples,
        "warmups": 1,
        "scenarios": {},
    }
    for scenario in ("new", "resume"):
        samples = []
        for index in range(args.samples + 1):
            with tempfile.TemporaryDirectory(prefix="a13n-tui-benchmark-") as tmp:
                root = Path(tmp)
                configuration(root)
                if scenario == "resume":
                    run(root, "seed")
                sample = run(root, scenario)
                if index:
                    samples.append(sample)
        report["scenarios"][scenario] = {
            "median": {key: statistics.median(sample[key] for sample in samples) for key in samples[0]},
            "raw": samples,
        }
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(scenario, report["scenarios"][scenario]["median"], flush=True)


if __name__ == "__main__":
    main()
