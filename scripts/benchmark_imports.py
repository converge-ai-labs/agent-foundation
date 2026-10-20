"""Measure fresh-process startup boundaries without external services.

Run this script with the same interpreter, lockfile and machine before and after
an import change. Each scenario gets one discarded warmup and independent child
processes; OS filesystem caches remain warm. Timings are observations, not gates.
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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "logging": "import a13n_logging",
    "envd": "import a13n_envd_client",
    "envd_protocol": "from a13n_envd_client.eip.v1.models import *",
    "stream": "import a13n_stream_protocol",
    "stream_fragments": "from a13n_stream_protocol import CustomEventAssembler; CustomEventAssembler()",
    "harness": "import a13n_harness",
    "harness_builder": "from a13n_harness import HarnessBuilder; type(HarnessBuilder)",
    "harness_models": "import a13n_harness.models",
    "harness_codeact": "from a13n_harness.capabilities.codeact import CodeActCapability; type(CodeActCapability)",
    "ui": "import a13n_harness_ui",
    "ui_cli": "import a13n_harness_ui.cli",
    "ui_runtime": "import a13n_harness_ui.cli_runtime",
    "ui_app": "from a13n_harness_ui.app import open_harness_ui_app; callable(open_harness_ui_app)",
    "ui_terminal_runtime": "from a13n_harness_ui.interactive.runtime import *",
    "service": "import a13n_service",
    "service_cli": "import a13n_service.cli",
    "service_app": "from a13n_service.app import build_app; callable(build_app)",
    "service_control": "from a13n_service.app import build_app; from a13n_service.settings import Settings; build_app(settings=Settings(), role='control')",
    "service_worker": "from a13n_service.app import build_app; from a13n_service.settings import Settings; build_app(settings=Settings(), role='worker')",
    "service_all": "from a13n_service.app import build_app; from a13n_service.settings import Settings; build_app(settings=Settings(), role='all')",
    "service_distribution": "from a13n_service.distribution import OSS; type(OSS)",
}
WORKER = """
import importlib.metadata, json, resource, sys, time
if sys.argv[2] == 'eager':
    sys.set_lazy_imports_filter(lambda *args: False)
start = time.perf_counter()
exec(sys.argv[1], {})
elapsed = time.perf_counter() - start
print(json.dumps({"elapsed_s": elapsed, "modules": len(sys.modules),
                  "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--eager-imports", action="store_true", help="Disable native lazy imports through Python 3.15's filter"
    )
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    command = [args.python]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("A13N_")}
    environment["PYTHONHASHSEED"] = "0"
    environment["PYDANTIC_AI_NO_BANNER"] = "1"
    metadata = subprocess.check_output(
        [
            *command,
            "-c",
            "import sys, importlib.metadata, json; print(json.dumps({'version':sys.version, 'packages':sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions())}))",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
    )
    report = {
        "label": args.label,
        "interpreter": json.loads(metadata),
        "native_lazy_imports": "filter-disabled" if args.eager_imports else "normal",
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "cpu_model": next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            None,
        )
        if Path("/proc/cpuinfo").exists()
        else platform.processor(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "samples": args.samples,
        "warmups": 1,
        "scenarios": {},
    }
    for name in args.scenario or SCENARIOS:
        samples = []
        for index in range(args.samples + 1):
            started = time.perf_counter()
            result = subprocess.run(
                [*command, "-c", WORKER, SCENARIOS[name], "eager" if args.eager_imports else "normal"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
            )
            wall = time.perf_counter() - started
            if result.returncode:
                report["scenarios"][name] = {"error": result.stderr, "returncode": result.returncode}
                print(f"{name}: ERROR", flush=True)
                break
            sample = json.loads(result.stdout.splitlines()[-1])
            sample["wall_s"] = wall
            if index:
                samples.append(sample)
        else:
            summary = {key: statistics.median(s[key] for s in samples) for key in samples[0]}
            report["scenarios"][name] = {
                "median": summary,
                "max_wall_s": max(s["wall_s"] for s in samples),
                "raw": samples,
            }
            print(f"{name}: {summary['elapsed_s']:.4f}s, {summary['modules']} modules", flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
